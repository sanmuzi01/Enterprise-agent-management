import os
from typing import Dict, List

from cryptography.fernet import Fernet


PLACEHOLDER_MARKERS = ("change-me", "your-", "example.com", "placeholder")
WEAK_ADMIN_PASSWORDS = {"139218", "admin", "admin123", "password", "123456", "12345678", ""}
# docker-compose.yml 里为了让 `docker compose up` 零配置跑通演示，给
# JWT_SECRET_KEY / LLM_ENCRYPTION_KEY 写了公开的默认值——任何拿到这份代码的人
# 都知道这两个值，绝不能被当成真实密钥使用。它们是合法格式（尤其后者是可用的
# Fernet key），普通的“看起来像占位符”检测认不出来，所以单独按精确值拦截。
KNOWN_DEMO_SECRETS = {
    "dev-only-not-a-secret-change-me",
    "LG5sThiGcVsg9jRbbN_fezONjfKdo3E72yQPYIUwZHQ=",
}


def is_production() -> bool:
    """判断当前是否生产环境。只有显式配置为 production 才启用严格校验。"""

    return os.getenv("APP_ENV", "development").strip().lower() == "production"


def _is_blank(value: str) -> bool:
    return not (value or "").strip()


def _looks_placeholder(value: str) -> bool:
    stripped = (value or "").strip()
    if stripped in KNOWN_DEMO_SECRETS:
        return True
    lowered = stripped.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _split_csv(value: str) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _add_required(checks: List[Dict], name: str, value: str) -> None:
    if _is_blank(value):
        checks.append({"name": name, "ok": False, "level": "error", "message": "缺少必填配置"})
    elif _looks_placeholder(value):
        checks.append({"name": name, "ok": False, "level": "error", "message": "仍是示例占位值"})
    else:
        checks.append({"name": name, "ok": True, "level": "ok", "message": "已配置"})


def _check_fernet_key(checks: List[Dict]) -> None:
    value = os.getenv("LLM_ENCRYPTION_KEY", "")
    if _is_blank(value) or _looks_placeholder(value):
        return
    try:
        Fernet(value.encode("utf-8"))
        checks.append({"name": "LLM_ENCRYPTION_KEY_FORMAT", "ok": True, "level": "ok", "message": "Fernet key 格式正确"})
    except Exception:
        checks.append({"name": "LLM_ENCRYPTION_KEY_FORMAT", "ok": False, "level": "error", "message": "不是有效 Fernet key"})


def validate_runtime_config() -> Dict[str, object]:
    """校验运行时配置，返回不包含敏感值的检查结果。"""

    checks: List[Dict] = []
    production = is_production()

    for name in ("DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME", "JWT_SECRET_KEY", "LLM_ENCRYPTION_KEY"):
        _add_required(checks, name, os.getenv(name, ""))
    _check_fernet_key(checks)

    # MYSQL_ROOT_PASSWORD 只给建账号/跑迁移用，业务运行时账号（DB_USER/DB_PASSWORD、
    # ENTERPRISE_DB_USER/ENTERPRISE_DB_PASSWORD）应该权限收紧过，见
    # docker-compose.prod.yml 顶部注释和 docs/enterprise-rbac-plan.md。这里拦的是
    # 最容易犯的错误——操作员图省事把两个密码设成一样的，等于业务账号一旦泄露就
    # 直接等于泄露了 root，限权账号形同虚设。
    if production:
        mysql_root_password = (os.getenv("MYSQL_ROOT_PASSWORD", "") or "").strip()
        db_password = (os.getenv("DB_PASSWORD", "") or "").strip()
        enterprise_db_password = (os.getenv("ENTERPRISE_DB_PASSWORD", "") or "").strip()
        if _is_blank(mysql_root_password):
            checks.append({"name": "MYSQL_ROOT_PASSWORD", "ok": False, "level": "error", "message": "缺少必填配置"})
        elif _looks_placeholder(mysql_root_password):
            checks.append({"name": "MYSQL_ROOT_PASSWORD", "ok": False, "level": "error", "message": "仍是示例占位值"})
        elif mysql_root_password == db_password:
            checks.append({
                "name": "MYSQL_ROOT_PASSWORD", "ok": False, "level": "error",
                "message": "不能跟 DB_PASSWORD 相同——业务运行时账号一旦泄露就等于泄露了 root，限权就没意义了",
            })
        elif enterprise_db_password and mysql_root_password == enterprise_db_password:
            checks.append({
                "name": "MYSQL_ROOT_PASSWORD", "ok": False, "level": "error",
                "message": "不能跟 ENTERPRISE_DB_PASSWORD 相同，理由同上",
            })
        else:
            checks.append({"name": "MYSQL_ROOT_PASSWORD", "ok": True, "level": "ok", "message": "已配置且与业务账号密码不同"})

    redis_url = os.getenv("REDIS_URL", "")
    if production:
        _add_required(checks, "REDIS_URL", redis_url)
    else:
        checks.append({
            "name": "REDIS_URL",
            "ok": True,
            "level": "ok" if redis_url else "warn",
            "message": "已配置 Redis" if redis_url else "未配置 Redis，当前会使用进程内缓存兜底",
        })

    sms_provider = os.getenv("SMS_PROVIDER", "console").strip().lower()
    if sms_provider not in {"console", "webhook", "aliyun"} or (production and sms_provider == "console"):
        checks.append({"name": "SMS_PROVIDER", "ok": False, "level": "error", "message": "生产环境必须使用 webhook 或 aliyun 短信服务"})
    else:
        checks.append({"name": "SMS_PROVIDER", "ok": True, "level": "ok", "message": sms_provider or "console"})
    if sms_provider == "webhook":
        _add_required(checks, "SMS_WEBHOOK_URL", os.getenv("SMS_WEBHOOK_URL", ""))
        _add_required(checks, "SMS_WEBHOOK_TOKEN", os.getenv("SMS_WEBHOOK_TOKEN", ""))
    elif sms_provider == "aliyun":
        for name in (
            "ALIBABA_CLOUD_ACCESS_KEY_ID",
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
            "SMS_ALIYUN_SIGN_NAME",
            "SMS_ALIYUN_TEMPLATE_CODE",
        ):
            _add_required(checks, name, os.getenv(name, ""))
    if production and os.getenv("SMS_EXPOSE_DEV_CODE", "0") == "1":
        checks.append({"name": "SMS_EXPOSE_DEV_CODE", "ok": False, "level": "error", "message": "生产环境不能把验证码返回给前端"})

    trusted_hosts = _split_csv(os.getenv("TRUSTED_HOSTS", ""))
    if production and (not trusted_hosts or "*" in trusted_hosts):
        checks.append({"name": "TRUSTED_HOSTS", "ok": False, "level": "error", "message": "生产环境必须配置明确 Host 白名单"})
    elif trusted_hosts:
        checks.append({"name": "TRUSTED_HOSTS", "ok": True, "level": "ok", "message": "已配置 Host 白名单"})

    cors_origins = _split_csv(os.getenv("CORS_ALLOW_ORIGINS", ""))
    if production and (not cors_origins or "*" in cors_origins):
        checks.append({"name": "CORS_ALLOW_ORIGINS", "ok": False, "level": "error", "message": "生产环境必须配置明确 CORS 来源"})
    elif cors_origins:
        checks.append({"name": "CORS_ALLOW_ORIGINS", "ok": True, "level": "ok", "message": "已配置 CORS 来源"})

    if production and "*" in _split_csv(os.getenv("FORWARDED_ALLOW_IPS", "")):
        checks.append({
            "name": "FORWARDED_ALLOW_IPS", "ok": True, "level": "warn",
            "message": "设为 * 时会采信客户端自带的 X-Forwarded-For，用户可伪造 IP 绕过登录/短信限流；建议只列 nginx 所在的内网段",
        })

    admin_password = os.getenv("ADMIN_PASSWORD", "")
    if production:
        if _is_blank(admin_password):
            checks.append({"name": "ADMIN_PASSWORD", "ok": False, "level": "error", "message": "生产环境必须显式设置管理员初始密码"})
        elif admin_password.strip() in WEAK_ADMIN_PASSWORDS or _looks_placeholder(admin_password):
            checks.append({"name": "ADMIN_PASSWORD", "ok": False, "level": "error", "message": "管理员密码过于简单或仍是示例占位值"})
        else:
            checks.append({"name": "ADMIN_PASSWORD", "ok": True, "level": "ok", "message": "已配置"})

    errors = [item for item in checks if item["level"] == "error"]
    warnings = [item for item in checks if item["level"] == "warn"]
    return {
        "environment": "production" if production else "development",
        "ok": not errors,
        "checks": checks,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


def assert_runtime_config() -> None:
    """生产环境启动前强校验配置，避免带占位密钥或错误短信配置上线。"""

    result = validate_runtime_config()
    if is_production() and not result["ok"]:
        messages = [
            f"{item['name']}: {item['message']}"
            for item in result["checks"]
            if item["level"] == "error"
        ]
        raise RuntimeError("生产配置校验失败: " + "; ".join(messages))
