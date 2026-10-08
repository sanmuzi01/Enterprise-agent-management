"""登录令牌放在 HttpOnly Cookie 里（前端 JavaScript 读不到），并用“签名的双重提交”防 CSRF。

为什么：之前前端把令牌存在 localStorage，任何一次 XSS（哪怕来自第三方包）都能直接把令牌读走。
现在登录成功后后端下发两个 Cookie：
  session_token  HttpOnly（脚本读不到）、Secure（生产）、SameSite=Lax —— 就是 JWT 本身
  csrf_token     脚本可读 —— 值是 HMAC(服务端密钥, session_token)；前端对 POST/PUT/PATCH/DELETE 把它放进 X-CSRF-Token 请求头
用 Cookie 鉴权的“会改数据的请求”必须同时满足：请求头 == csrf Cookie == 按当前 session_token 重新算出的值。
攻击者的页面既读不到 Cookie，也算不出这个值（不知道服务端密钥），即使能往子域名里塞 Cookie 也伪造不了。
带 Authorization: Bearer 的请求（脚本 / 集成 / 测试）不受影响，也不需要 CSRF（浏览器不会自动带这个头）。

环境变量：
  SESSION_COOKIE_SECURE    1/0，默认生产环境为 1（只在 https 上发送）
  SESSION_COOKIE_SAMESITE  lax（默认）或 strict
"""
import hashlib
import hmac
import os
from typing import Optional

from fastapi import HTTPException, Request, Response, status

SESSION_COOKIE = "session_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _secret() -> bytes:
    from service.auth import _get_secret_key
    return _get_secret_key().encode("utf-8")


def csrf_for(session_token: str) -> str:
    """跟某个登录令牌绑定的 CSRF 值：换了登录（新令牌）它就变，不能跨会话复用。"""
    return hmac.new(_secret(), b"csrf:" + session_token.encode("utf-8"), hashlib.sha256).hexdigest()[:48]


def _secure() -> bool:
    configured = os.getenv("SESSION_COOKIE_SECURE")
    if configured is not None and configured.strip() != "":
        return configured.strip().lower() in ("1", "true", "yes")
    return os.getenv("APP_ENV", "development").lower() == "production"


def _samesite() -> str:
    value = os.getenv("SESSION_COOKIE_SAMESITE", "lax").strip().lower()
    return value if value in ("lax", "strict") else "lax"


def issue(response: Response, session_token: str, max_age_seconds: int) -> None:
    """登录成功后下发会话 Cookie 和 CSRF Cookie。"""
    common = dict(max_age=max_age_seconds, path="/", secure=_secure(), samesite=_samesite())
    response.set_cookie(SESSION_COOKIE, session_token, httponly=True, **common)
    response.set_cookie(CSRF_COOKIE, csrf_for(session_token), httponly=False, **common)


def clear(response: Response) -> None:
    for name, http_only in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(name, path="/", secure=_secure(), httponly=http_only, samesite=_samesite())


def verify_csrf(request: Request, session_token: str) -> None:
    """只对用 Cookie 鉴权、且会改数据的请求调用；不通过抛 403。"""
    if request.method.upper() in SAFE_METHODS:
        return
    expected = csrf_for(session_token)
    header: Optional[str] = request.headers.get(CSRF_HEADER)
    cookie: Optional[str] = request.cookies.get(CSRF_COOKIE)
    if not header or not cookie or not hmac.compare_digest(header, expected) or not hmac.compare_digest(cookie, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="安全校验失败，请刷新页面后重试")
