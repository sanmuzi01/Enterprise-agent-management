import contextlib
import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from service.observability import context as trace_context, otel


REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"
PROCESS_TIME_HEADER = "X-Process-Time"
_ACCESS_LOG = logging.getLogger("http_request")
_UNTRACED_PATHS = {"/health", "/live", "/ready", "/metrics", "/favicon.ico"}
_REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.:-]{8,80}$")


def normalize_request_id(value: str = "") -> str:
    """校验外部传入的请求 ID，不可信或为空时生成新的 ID。"""
    request_id = (value or "").strip()
    if _REQUEST_ID_PATTERN.match(request_id):
        return request_id
    return uuid.uuid4().hex


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求注入追踪上下文：request_id 原样回显给前端，trace_id（W3C traceparent 优先，其次 request_id）
    写进 contextvars，日志、对 Java 业务服务的调用、问题中心记录都用它，一个 id 串起前端到后端。"""

    async def dispatch(self, request, call_next):
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        trace_id = trace_context.from_headers(request.headers.get("traceparent"), request_id)
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        trace_context.set_trace(trace_id, request_id=request_id, method=request.method)

        started = time.perf_counter()
        quiet = request.url.path in _UNTRACED_PATHS      # 探活和指标抓取很频繁，不记链路
        with (contextlib.nullcontext() if quiet else otel.span(
                f"{request.method} {request.url.path}", kind="server", trace_id=trace_id,
                attributes={"http.request.method": request.method, "url.path": request.url.path, "request.id": request_id})) as current:
            try:
                response = await call_next(request)
            except Exception as exc:  # noqa: BLE001
                # 未处理的异常在这里就地转成统一错误响应：不再冒泡到 Starlette 最外层，否则同一次故障会被它和服务器再各打一遍完整堆栈
                handler = getattr(request.app.state, "unhandled_handler", None)
                if handler is None:
                    raise
                response = await handler(request, exc)
            route = request.scope.get("route")
            otel.rename(current, f"{request.method} {getattr(route, 'path', request.url.path)}")      # 用路由模板命名（/department/{id}），不是带具体编号的路径
            otel.set_attributes(current, **{"http.route": getattr(route, "path", None), "http.response.status_code": response.status_code})
            if response.status_code >= 500:
                otel.mark_error(current, f"HTTP {response.status_code}")
        elapsed = time.perf_counter() - started
        if not quiet:      # 每个请求一条结构化的完成日志（只有方法、路由模板、状态码、耗时；没有查询串和请求体）。没有处理器，只会被送去 Loki，不刷控制台
            _ACCESS_LOG.info("%s %s -> %s %.0fms", request.method, getattr(route, "path", request.url.path), response.status_code, elapsed * 1000)

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[TRACE_ID_HEADER] = trace_id
        response.headers[PROCESS_TIME_HEADER] = f"{elapsed:.4f}"
        return response
