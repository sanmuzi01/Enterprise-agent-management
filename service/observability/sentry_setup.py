"""Sentry 接入（可选）：配置了 SENTRY_DSN 才启用，没配置时所有函数都是空操作，本地开发不受影响。

只上报真正的故障（代码缺陷、依赖不可用、数据不一致、任务最终失败），预期的业务异常（校验失败、没权限、找不到、冲突、限流）
不上报；发出去之前整个事件过一遍脱敏（请求头、Cookie、请求体、用户信息一律去掉）。事件带 environment / release / service /
trace_id / operation / error_code 标签，Sentry 里能按这些筛选，问题中心里的 sentry_event_id 用来互相跳转。
"""
import os
from typing import Any, Dict, Optional

from service.observability.error_codes import should_report
from service.observability.redact import redact
from utils.logger_handler import get_logger

logger = get_logger("sentry")
_enabled = False


def _drop_expected(event: Dict[str, Any], hint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    exc = (hint or {}).get("exc_info", (None, None, None))[1]
    from service.exceptions import AppError
    if isinstance(exc, AppError) and not should_report(exc.code, exc.http_status):
        return None
    return event


def scrub_event(event: Dict[str, Any], hint: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    kept = _drop_expected(event, hint or {})
    if kept is None:
        return None
    request = kept.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)
        request.pop("data", None)          # 请求体（可能含文档正文、密码）一律不外发
        request.pop("query_string", None)
        request["headers"] = {k: v for k, v in (request.get("headers") or {}).items() if k.lower() in ("user-agent", "content-type", "x-request-id", "x-trace-id")}
    kept.pop("user", None)
    for key in ("extra", "contexts", "tags", "breadcrumbs"):
        if key in kept:
            kept[key] = redact(kept[key])
    return kept


def init(service: str = "agent-service") -> bool:
    global _enabled
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("配置了 SENTRY_DSN 但没有安装 sentry-sdk，已跳过 Sentry 接入")
        return False
    sentry_sdk.init(dsn=dsn, environment=os.getenv("APP_ENV", "development"), release=os.getenv("RELEASE") or os.getenv("GIT_COMMIT") or None,
                    traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0")), send_default_pii=False,
                    before_send=scrub_event, max_request_body_size="never")
    sentry_sdk.set_tag("service", service)
    _enabled = True
    logger.info("Sentry 已启用")
    return True


def capture(exc: BaseException, *, trace_id: Optional[str], operation: str, error_code: str,
            department_id: Optional[int] = None) -> Optional[str]:
    """上报一次异常，返回 sentry_event_id；未启用或失败返回 None。"""
    if not _enabled:
        return None
    try:
        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("trace_id", trace_id or "-")
            scope.set_tag("operation", operation)
            scope.set_tag("error_code", error_code)
            if department_id is not None:
                scope.set_tag("department_id", str(department_id))
            return sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001 —— Sentry 不可用不能影响业务
        logger.warning("Sentry 上报失败", exc_info=True)
        return None
