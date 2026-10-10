"""脚本 / CLI / 集成专用的令牌接口：`POST /auth/token`。

浏览器登录走 `POST /user/login`：只下发 HttpOnly Cookie，**响应体里没有 JWT**——登录时运行的页面脚本也读不到令牌。
需要 Bearer 令牌的非浏览器调用方（运维脚本、压测、集成）显式走这里：
  · 单独的限流（比浏览器登录更紧）和审计（auth.token_issued：谁、从哪个地址、什么 User-Agent）；
  · 不下发 Cookie；
  · 可以用 AUTH_TOKEN_ENDPOINT_ENABLED 关掉：默认开发 / 测试环境开启，生产环境默认关闭（需要时显式设为 1）；关闭时返回 404。
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from FasdtApi.login import LoginUser, _limit_error
from models.async_db import get_async_db
from service import audit_service, auth_async_service
from service.auth import ACCESS_TOKEN_EXPIRE_MINUTES
from utils.logger_handler import get_logger
from utils.rate_limit import LimitExceeded, require_limit

router = APIRouter(prefix="/auth", tags=["脚本 / 集成令牌"])
logger = get_logger("auth_token")


def endpoint_enabled() -> bool:
    configured = os.getenv("AUTH_TOKEN_ENDPOINT_ENABLED", "").strip().lower()
    if configured:
        return configured in ("1", "true", "yes")
    return os.getenv("APP_ENV", "development").lower() != "production"


@router.post("/token", summary="脚本 / 集成专用：用账号密码换 Bearer 令牌（浏览器请用 /user/login）")
async def issue_token(user: LoginUser, request: Request, async_db=Depends(get_async_db)):
    if not endpoint_enabled():
        raise HTTPException(404, detail="Not Found")
    client_ip = request.client.host if request.client else "unknown"
    try:
        require_limit(
            critical=True,
            key=f"token:ip:{client_ip}",
            limit_env="TOKEN_IP_RATE_LIMIT", default_limit=10,
            window_env="TOKEN_RATE_WINDOW_SECONDS", default_window=300,
            label="获取令牌",
        )
        require_limit(
            critical=True,
            key=f"token:user:{user.name.strip().lower()}",
            limit_env="TOKEN_USER_RATE_LIMIT", default_limit=5,
            window_env="TOKEN_RATE_WINDOW_SECONDS", default_window=300,
            label="获取令牌",
        )
    except LimitExceeded as e:
        raise _limit_error(e)
    result = await auth_async_service.login(async_db, user.name, user.password)
    await audit_service.record_async(
        result["user_id"], "auth.token_issued",
        detail={"ip": client_ip, "user_agent": (request.headers.get("user-agent") or "")[:200]},
    )
    logger.info("已签发 Bearer 令牌：user_id=%s ip=%s", result["user_id"], client_ip)
    return {
        "access_token": result["access_token"],
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user_id": result["user_id"],
        "username": result["username"],
    }
