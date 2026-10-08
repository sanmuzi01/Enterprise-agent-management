import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量，配置错误时使用默认值。"""

    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """统一添加安全响应头，并限制请求体大小。"""

    async def dispatch(self, request, call_next):
        max_body_bytes = _env_int("MAX_REQUEST_BODY_BYTES", 50 * 1024 * 1024)
        content_length = request.headers.get("content-length")
        if content_length and max_body_bytes > 0:
            try:
                if int(content_length) > max_body_bytes:
                    return JSONResponse(
                        {"detail": "请求体过大"},
                        status_code=413,
                    )
            except ValueError:
                return JSONResponse({"detail": "Content-Length 格式错误"}, status_code=400)

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        # API 只返回 JSON / 文件下载，不需要加载任何资源：即使某个响应被浏览器当成页面打开，也不能执行脚本、不能被嵌入。
        # 在线文档页（/docs、/redoc）要从 CDN 加载脚本，单独放行。
        if not request.url.path.startswith(("/docs", "/redoc")):
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if os.getenv("ENABLE_HSTS", "0") == "1":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
