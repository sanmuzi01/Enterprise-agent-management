import time

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from service.metrics_service import observe_http_request
from service.operation_log_service import create_operation_log, extract_user_from_authorization


SKIP_PREFIXES = ("/static",)
SKIP_PATHS = {"/health", "/metrics"}


class OperationLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path in SKIP_PATHS or any(path.startswith(prefix) for prefix in SKIP_PREFIXES):
            return await call_next(request)

        started = time.perf_counter()
        status_code = 500
        error_msg = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            error_msg = str(exc)[:1000]
            raise
        finally:
            elapsed_seconds = time.perf_counter() - started
            elapsed_ms = int(elapsed_seconds * 1000)
            observe_http_request(request.method, path, status_code, elapsed_seconds)
            user_payload = extract_user_from_authorization(request.headers.get("authorization"))
            request_id = getattr(request.state, "request_id", None)
            log_error_msg = error_msg
            if request_id:
                log_error_msg = f"request_id={request_id}" + (f"; {error_msg}" if error_msg else "")
            # 同步写库必须放到线程池：在事件循环线程上执行时，operation_log 的外键检查要等
            # user 行锁，而持锁的并发请求（更新最近访问时间）因事件循环被占住发不出 COMMIT，
            # 两边互等直到 innodb_lock_wait_timeout（50 秒），整个进程的请求都会卡住。
            await run_in_threadpool(
                create_operation_log,
                user_id=user_payload.get("user_id"),
                username=user_payload.get("username"),
                method=request.method,
                path=path,
                status_code=status_code,
                latency_ms=elapsed_ms,
                client_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                error_msg=log_error_msg,
            )
