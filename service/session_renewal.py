"""登录会话按使用续期：一直在用就不掉线，真正闲置超过有效期才要求重新登录。

之前登录令牌固定 60 分钟（JWT_ACCESS_TOKEN_EXPIRE_MINUTES）到期，期间不管用没用都不续，
用户正在填表也会突然被踢回登录页。现在：
  - 浏览器（Cookie 会话）发来的请求鉴权通过、并且令牌剩下的时间不到一半时，响应里换发一个新令牌（同时换 CSRF Cookie）；
  - 页面后台定时拉取的请求（待办数、未读通知，带 X-Background-Poll 头）不算“在用”，不续期——
    否则页面开着就永远不过期，闲置超时形同虚设；
  - 从最初登录起超过 SESSION_MAX_HOURS（默认 12 小时）就不再续期，到期必须重新登录，令牌泄露也不能被无限续命；
  - 只续“这次请求已经验证通过”的令牌：改密码、强制下线后版本号不一致的旧令牌照常 401，不会被续期；
  - 脚本 / 集成用的 Bearer 令牌不续期（它们自己管理令牌）。
"""
import calendar
import os
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from utils.timeutil import utcnow

BACKGROUND_POLL_HEADER = "X-Background-Poll"
_STATE_KEY = "renewed_session_token"


def _now_ts() -> int:
    return calendar.timegm(utcnow().utctimetuple())


def lifetime_seconds() -> int:
    from service.auth import ACCESS_TOKEN_EXPIRE_MINUTES
    return ACCESS_TOKEN_EXPIRE_MINUTES * 60


def max_session_seconds() -> int:
    try:
        hours = float(os.getenv("SESSION_MAX_HOURS", "12"))
    except ValueError:
        hours = 12.0
    return int(max(hours, 0) * 3600)


def renewal_token(payload: dict, now: Optional[int] = None) -> Optional[str]:
    """该续期就返回新令牌，否则 None。payload 必须是已经验证通过（签名、有效期、版本号）的令牌内容。"""
    now = _now_ts() if now is None else now
    exp = int(payload.get("exp") or 0)
    lifetime = lifetime_seconds()
    if exp - now > lifetime // 2:
        return None   # 还很新，不必每个请求都换
    # 老令牌没有 auth_time：按“签发时刻 = 到期 - 有效期”推算，只会少算不会多算
    auth_time = int(payload.get("auth_time") or (exp - lifetime))
    if now - auth_time >= max_session_seconds():
        return None   # 超过最长会话时长，到期后必须重新登录
    from datetime import timedelta
    from service.auth import create_access_token
    data = {k: payload[k] for k in ("user_id", "username", "ver") if k in payload}
    data["auth_time"] = auth_time
    # 新令牌不会越过最长会话时长
    remaining = min(lifetime, max_session_seconds() - (now - auth_time))
    return create_access_token(data, expires_delta=timedelta(seconds=remaining))


def mark_for_renewal(request: Optional[Request], credentials, payload: dict) -> None:
    """鉴权依赖在验证通过后调用：符合条件就把新令牌放进 request.state，由中间件写进响应的 Cookie。"""
    if request is None or getattr(credentials, "scheme", "") != "Cookie":
        return
    if request.headers.get(BACKGROUND_POLL_HEADER):
        return
    token = renewal_token(payload)
    if token:
        setattr(request.state, _STATE_KEY, token)


class SessionRenewalMiddleware(BaseHTTPMiddleware):
    """把鉴权依赖算好的新令牌写进响应（session_token + csrf_token 两个 Cookie）。请求失败时不换发。"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        token = getattr(request.state, _STATE_KEY, None)
        if token and response.status_code < 400:
            from service import session_cookie
            session_cookie.issue(response, token, lifetime_seconds())
        return response
