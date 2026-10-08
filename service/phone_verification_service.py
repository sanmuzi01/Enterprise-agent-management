import hashlib
import hmac
import json
import os
import random
import re
import time
from typing import Dict, Optional
from urllib import request as urllib_request

from fastapi import HTTPException, status

from utils.cache import verification_cache
from utils.logger_handler import logger
from utils.rate_limit import LimitExceeded, require_limit

PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量，配置缺失或格式错误时使用默认值。"""

    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def normalize_phone(phone: str) -> str:
    """规范化中国大陆手机号，只保留数字并校验格式。"""

    normalized = re.sub(r"\D", "", phone or "")
    if not PHONE_PATTERN.fullmatch(normalized):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请输入有效的手机号")
    return normalized


def _cache_key(scene: str, phone: str) -> tuple:
    return f"sms_{scene}", phone


def _code_digest(phone: str, code: str) -> str:
    """用服务端密钥保存验证码摘要，避免缓存泄露时直接暴露明文验证码。"""

    secret = os.getenv("JWT_SECRET_KEY") or os.getenv("LLM_ENCRYPTION_KEY") or "dev-secret"
    payload = f"{phone}:{code}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _new_code() -> str:
    return f"{random.SystemRandom().randint(0, 999999):06d}"


def _now() -> int:
    return int(time.time())


def _send_by_webhook(phone: str, code: str, ttl_seconds: int, scene: str = "register") -> None:
    """通过通用 Webhook 发送短信，方便上线时接入任意短信服务网关。"""

    webhook_url = os.getenv("SMS_WEBHOOK_URL")
    if not webhook_url:
        raise RuntimeError("未配置 SMS_WEBHOOK_URL")

    payload = json.dumps({
        "phone": phone,
        "code": code,
        "ttl_seconds": ttl_seconds,
        "scene": scene,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = os.getenv("SMS_WEBHOOK_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib_request.Request(webhook_url, data=payload, headers=headers, method="POST")
    timeout = float(os.getenv("SMS_WEBHOOK_TIMEOUT", "3"))
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"短信服务返回异常状态: {resp.status}")


def _send_by_aliyun(phone: str, code: str, ttl_seconds: int) -> None:
    """使用号码认证服务发送项目生成的验证码，校验仍由本项目完成。"""

    from alibabacloud_dypnsapi20170525.client import Client
    from alibabacloud_dypnsapi20170525 import models as sms_models
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_tea_util import models as util_models

    config = open_api_models.Config(
        access_key_id=os.environ["ALIBABA_CLOUD_ACCESS_KEY_ID"],
        access_key_secret=os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"],
        endpoint="dypnsapi.aliyuncs.com",
    )
    request = sms_models.SendSmsVerifyCodeRequest(
        phone_number=phone,
        sign_name=os.environ["SMS_ALIYUN_SIGN_NAME"],
        template_code=os.environ["SMS_ALIYUN_TEMPLATE_CODE"],
        template_param=json.dumps({"code": code, "min": str((ttl_seconds + 59) // 60)}),
        valid_time=ttl_seconds,
        return_verify_code=False,
    )
    try:
        response = Client(config).send_sms_verify_code_with_options(
            request, util_models.RuntimeOptions(connect_timeout=3000, read_timeout=5000)
        )
    except Exception as exc:
        logger.error("阿里云短信认证请求失败: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="短信发送失败，请稍后重试") from None
    if not response.body or response.body.code != "OK" or not response.body.success:
        logger.error("阿里云短信认证返回失败: code=%s", getattr(response.body, "code", None))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="短信发送失败，请稍后重试")


def _send_sms(phone: str, code: str, ttl_seconds: int, scene: str) -> Dict[str, Optional[str]]:
    """发送验证码。

    SMS_PROVIDER=webhook 时走真实短信网关；默认 console 模式只写日志，便于本地开发测试。
    """

    provider = os.getenv("SMS_PROVIDER", "console").strip().lower()
    if provider == "webhook":
        _send_by_webhook(phone, code, ttl_seconds, scene)
        return {"provider": "webhook", "dev_code": None}
    if provider == "aliyun":
        _send_by_aliyun(phone, code, ttl_seconds)
        return {"provider": "aliyun", "dev_code": None}

    logger.info(f"短信验证码[{scene}]: phone={phone}, code={code}, ttl={ttl_seconds}s")
    dev_code = code if os.getenv("SMS_EXPOSE_DEV_CODE", "0") == "1" else None
    return {"provider": "console", "dev_code": dev_code}


def send_verification_code(phone: str, client_ip: str = "", scene: str = "register") -> Dict[str, object]:
    """发送验证码，并限制同一手机号和 IP 的发送频率。scene 区分用途（register/reset），互不干扰。"""

    normalized = normalize_phone(phone)
    ttl_seconds = _env_int("SMS_CODE_TTL_SECONDS", 300)
    interval_seconds = _env_int("SMS_CODE_SEND_INTERVAL_SECONDS", 60)

    try:
        # 顺序：先查"来源"再查"号码"。被封的 IP 不能顺带占用别人号码的冷却时间；
        # 全局上限放最后一道之前，最多把短信费用封顶（0 表示关闭）。
        if client_ip:
            require_limit(
                critical=True,
                key=f"sms:{scene}:ip:{client_ip}",
                limit_env="SMS_CODE_IP_LIMIT",
                default_limit=20,
                window_env="SMS_CODE_IP_WINDOW_SECONDS",
                default_window=3600,
                label="验证码发送",
            )
        require_limit(
            critical=True,
            key=f"sms:{scene}:phone:{normalized}",
            limit_env="SMS_CODE_PHONE_LIMIT",
            default_limit=1,
            window_env="SMS_CODE_PHONE_WINDOW_SECONDS",
            default_window=interval_seconds,
            label="验证码发送",
        )
        require_limit(
            critical=True,
            key=f"sms:{scene}:daily:{normalized}",
            limit_env="SMS_CODE_DAILY_LIMIT",
            default_limit=10,
            window_env="SMS_CODE_DAILY_WINDOW_SECONDS",
            default_window=86400,
            label="验证码发送",
        )
        require_limit(
            critical=True,
            key="sms:global:hourly",
            limit_env="SMS_CODE_GLOBAL_HOURLY_LIMIT",
            default_limit=200,
            window_env="SMS_CODE_GLOBAL_HOURLY_WINDOW_SECONDS",
            default_window=3600,
            label="验证码发送",
        )
        require_limit(
            critical=True,
            key="sms:global:daily",
            limit_env="SMS_CODE_GLOBAL_DAILY_LIMIT",
            default_limit=1000,
            window_env="SMS_CODE_GLOBAL_DAILY_WINDOW_SECONDS",
            default_window=86400,
            label="验证码发送",
        )
    except LimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=exc.message,
            headers={"Retry-After": str(exc.retry_after)},
        )

    code = _new_code()
    sent = _send_sms(normalized, code, ttl_seconds, scene)
    verification_cache.set(
        _cache_key(scene, normalized),
        {
            "digest": _code_digest(normalized, code),
            "attempts": 0,
            "created_at": _now(),
            "expires_at": _now() + ttl_seconds,
        },
        ttl=ttl_seconds,
    )
    return {
        "message": "验证码已发送",
        "phone": normalized,
        "expires_in": ttl_seconds,
        "retry_after": interval_seconds,
        "provider": sent["provider"],
        "dev_code": sent["dev_code"],
    }


def verify_verification_code(phone: str, code: str, scene: str = "register", consume: bool = True) -> str:
    """校验验证码，成功后默认立即失效。"""

    normalized = normalize_phone(phone)
    clean_code = re.sub(r"\D", "", code or "")
    if not re.fullmatch(r"\d{6}", clean_code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请输入 6 位短信验证码")

    # 单个验证码只能错 5 次，但"读次数→写次数"不是原子的，并发请求能绕过；
    # 这里再按手机号做一层原子计数（Redis INCR），把窗口内的猜测总量封顶。
    try:
        require_limit(
            critical=True,
            key=f"sms:verify:{scene}:{normalized}",
            limit_env="SMS_CODE_VERIFY_LIMIT",
            default_limit=10,
            window_env="SMS_CODE_VERIFY_WINDOW_SECONDS",
            default_window=600,
            label="验证码校验",
        )
    except LimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=exc.message,
            headers={"Retry-After": str(exc.retry_after)},
        )

    record = verification_cache.get(_cache_key(scene, normalized))
    if not record:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="验证码已过期，请重新获取")

    max_attempts = _env_int("SMS_CODE_MAX_VERIFY_ATTEMPTS", 5)
    expires_at = int(record.get("expires_at") or 0)
    remaining_ttl = expires_at - _now()
    if remaining_ttl <= 0:
        verification_cache.invalidate(_cache_key(scene, normalized))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="验证码已过期，请重新获取")

    attempts = int(record.get("attempts") or 0) + 1
    expected = record.get("digest") or ""
    actual = _code_digest(normalized, clean_code)
    if not hmac.compare_digest(expected, actual):
        if attempts >= max_attempts:
            verification_cache.invalidate(_cache_key(scene, normalized))
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="验证码错误次数过多，请重新获取")
        record["attempts"] = attempts
        verification_cache.set(_cache_key(scene, normalized), record, ttl=remaining_ttl)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="验证码错误")

    if consume:
        verification_cache.invalidate(_cache_key(scene, normalized))
    return normalized


def send_register_code(phone: str, client_ip: str = "") -> Dict[str, object]:
    return send_verification_code(phone, client_ip, scene="register")


def verify_register_code(phone: str, code: str, consume: bool = True) -> str:
    return verify_verification_code(phone, code, scene="register", consume=consume)
