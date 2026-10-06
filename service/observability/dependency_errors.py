"""把底层异常归类成统一错误码：Java 业务服务不可用、模型超时、数据库不可用等依赖故障有明确的码，
其余未预期异常才是 INTERNAL_ERROR（代码缺陷）。返回 (错误码, HTTP 状态码)。"""
from typing import Tuple


def classify(exc: BaseException) -> Tuple[str, int]:
    import asyncio

    import requests
    from sqlalchemy.exc import DBAPIError, OperationalError

    from service import enterprise_hub_client as hub
    from service.http_resilience import CircuitOpenError

    if isinstance(exc, hub.HubUnavailable):
        return "JAVA_SERVICE_UNAVAILABLE", 503
    if isinstance(exc, CircuitOpenError):
        return ("JAVA_SERVICE_UNAVAILABLE" if hub.SERVICE_NAME in str(exc) else "upstream_error"), 503
    if isinstance(exc, (requests.Timeout, asyncio.TimeoutError, TimeoutError)):
        return "MODEL_TIMEOUT", 504
    if isinstance(exc, (OperationalError, DBAPIError)) and getattr(exc, "connection_invalidated", False):
        return "DATABASE_ERROR", 503
    if isinstance(exc, OperationalError):
        return "DATABASE_ERROR", 503
    if isinstance(exc, requests.ConnectionError):
        return "upstream_error", 502
    return "INTERNAL_ERROR", 500
