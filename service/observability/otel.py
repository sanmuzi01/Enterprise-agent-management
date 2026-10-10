"""OpenTelemetry：把链路（trace）和日志经 OTLP 发给 Collector，再由 Collector 送到 Tempo / Loki。

设置 `OTEL_EXPORTER_OTLP_ENDPOINT`（比如 http://127.0.0.1:4317）才启用，没设置时所有函数都是空操作，业务代码不用判断。

几条硬约束（都有测试）：
1. **遥测永远不能影响业务**：创建 / 结束 span、导出日志的任何异常都被吞掉；导出走批处理线程（有界队列、短超时），
   Collector 宕机时只会丢遥测（队列满了丢弃），请求不会变慢、不会失败，恢复后自动继续；
2. **trace_id 与现有日志、错误响应、问题中心、Java 业务服务里的是同一个**：span 的父上下文用我们自己的 32 位 trace_id 构造，
   所以 Tempo 里搜到的 trace，和日志里的 `trace_id`、前端看到的 `X-Trace-ID` 是同一个值；
3. **不带敏感内容**：span 只记方法、路由模板、状态码、业务操作名这类低风险信息，不记查询串、请求头、请求体；
   异常消息先脱敏再截断；Collector 里还有一道删除敏感属性名的兜底；
4. 采样按 trace_id 的比例（`OTEL_TRACE_SAMPLE_RATIO`，默认 1.0），各服务对同一个 trace 的取舍一致。
"""
import contextlib
import copy
import logging
import os
import random
import socket
import time
import warnings
from typing import Any, Dict, Iterator, Optional

from service.observability import context as trace_context
from service.observability.redact import redact_text

logger = logging.getLogger("otel_setup")

# Collector 不可用时每次导出失败都会在这些 logger 上记一条错误
NOISY_LOGGERS = ("opentelemetry.exporter.otlp.proto.grpc.exporter", "opentelemetry.sdk._shared_internal",
                 "opentelemetry.sdk.trace.export", "opentelemetry.sdk._logs._internal.export")

# 已经 shutdown 的 provider 故意留着：OTel SDK 在 Linux 上给每个批处理器注册了“fork 之后在子进程里重新初始化”的回调，
# 回调只持有批处理器的弱引用。批处理器被回收后，之后任何一次 fork（subprocess / multiprocessing）都会打印
# “Exception ignored … TypeError: 'NoneType' object is not callable”。一个进程里 init / shutdown 很少超过一两次，留着无所谓。
_RETIRED: list = []

_state: Dict[str, Any] = {"tracer": None, "tracer_provider": None, "logger_provider": None, "handler": None}


def enabled() -> bool:
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))


def active() -> bool:
    return _state["tracer"] is not None


class _RateLimited(logging.Filter):
    """Collector 不可用时，OTel 导出器每次失败都会打一条错误日志：限制成每分钟一条，免得刷屏（同时也不转发给 OTel 自己的日志处理器，避免循环）。"""

    def __init__(self):
        super().__init__()
        self.last = 0.0

    def filter(self, record: logging.LogRecord) -> bool:
        now = time.monotonic()
        if now - self.last < 60:
            return False
        self.last = now
        return True


def _handler_class(base):
    """日志导出 handler：每条日志发出前 ① 脱敏（根 logger 上的 handler 会收到所有模块的日志，没有经过各模块自己的脱敏）；
    ② 即使日志不是写在某个 span 里面（比如请求结束后的访问日志），也带上当前请求的 trace_id，Loki 里才能按 trace_id 查到整条链路的日志。"""
    class TraceAwareHandler(base):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                from opentelemetry import context as otel_context
                safe = copy.copy(record)
                safe.msg, safe.args, safe.exc_info, safe.exc_text = redact_text(record.getMessage()), (), None, None     # 异常堆栈里可能带变量值，不送出
                parent = _parent_context(None)
                token = otel_context.attach(parent) if parent is not None else None
                try:
                    super().emit(safe)
                finally:
                    if token is not None:
                        otel_context.detach(token)
            except Exception:  # noqa: BLE001 —— 日志导出出任何错都不能反过来影响写日志的业务代码
                pass
    return TraceAwareHandler


def init(service_name: Optional[str] = None) -> bool:
    """初始化链路与日志导出；已初始化或未配置返回当前状态。任何失败只记一条警告，不抛出。"""
    if active():
        return True
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return False
    try:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
        from service.observability.issues import release

        resource = Resource.create({
            "service.name": service_name or os.getenv("SERVICE_NAME", "agent-service"), "service.version": release(),
            "deployment.environment": os.getenv("APP_ENV", "development"), "service.instance.id": f"{socket.gethostname()}-{os.getpid()}",
        })
        ratio = max(0.0, min(1.0, float(os.getenv("OTEL_TRACE_SAMPLE_RATIO", "1.0"))))
        timeout = float(os.getenv("OTEL_EXPORT_TIMEOUT_SECONDS", "3"))
        provider = TracerProvider(resource=resource, sampler=TraceIdRatioBased(ratio))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, timeout=int(timeout)), max_queue_size=2048, max_export_batch_size=256,
                                                        schedule_delay_millis=1000, export_timeout_millis=int(timeout * 1000)))
        log_provider = LoggerProvider(resource=resource)
        log_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, timeout=int(timeout)), max_queue_size=4096,
                                                                       max_export_batch_size=256, schedule_delay_millis=1000, export_timeout_millis=int(timeout * 1000)))
        with warnings.catch_warnings():          # SDK 把这个 handler 标成弃用（新位置在另一个可选包里），功能不变，这里不让它在每次启动时刷警告
            warnings.simplefilter("ignore", DeprecationWarning)
            handler = _handler_class(LoggingHandler)(level=logging.INFO, logger_provider=log_provider)
        handler.addFilter(lambda record: not record.name.startswith(("opentelemetry", "otel_setup", "urllib3", "grpc")))      # 遥测自己的日志不再送进遥测
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(min(logging.getLogger().level or logging.INFO, logging.INFO))
        for name in NOISY_LOGGERS:                  # logger 上的过滤器只管直接记在它名下的日志，所以要挂在真正出错的那几个子 logger 上
            logging.getLogger(name).addFilter(_RateLimited())
        _state.update(tracer=provider.get_tracer("enterprise-agent"), tracer_provider=provider, logger_provider=log_provider, handler=handler)
        logger.info("OpenTelemetry 已启用，导出到 %s（采样比例 %s）", endpoint, ratio)
        return True
    except Exception:  # noqa: BLE001 —— 遥测初始化失败不能让应用起不来
        logger.warning("OpenTelemetry 初始化失败，已忽略（业务不受影响）", exc_info=True)
        return False


def shutdown(timeout_seconds: float = 3.0) -> None:
    """退出前把队列里的遥测尽量送出去；Collector 不可用时最多等 timeout 就放弃，不拖慢进程退出。"""
    handler = _state.get("handler")
    try:
        if handler is not None:
            logging.getLogger().removeHandler(handler)
        if _state["tracer_provider"] is not None:
            _state["tracer_provider"].force_flush(int(timeout_seconds * 1000))
            _state["tracer_provider"].shutdown()
        if _state["logger_provider"] is not None:
            _state["logger_provider"].force_flush(int(timeout_seconds * 1000))
            _state["logger_provider"].shutdown()
    except Exception:  # noqa: BLE001
        pass
    finally:
        _RETIRED.extend(p for p in (_state["tracer_provider"], _state["logger_provider"]) if p is not None)
        _state.update(tracer=None, tracer_provider=None, logger_provider=None, handler=None)


_KINDS = {"internal": "INTERNAL", "server": "SERVER", "client": "CLIENT", "producer": "PRODUCER", "consumer": "CONSUMER"}


def _parent_context(trace_id: Optional[str]):
    """父上下文：当前已有有效 span 就沿用；否则用我们自己的 trace_id 构造一个远程父，让新 span 的 trace id 与日志里的一致。"""
    from opentelemetry import trace
    if trace.get_current_span().get_span_context().is_valid:
        return None
    tid = trace_id or trace_context.current_trace_id()
    if not tid or len(tid) != 32:
        return None
    try:
        context = trace.SpanContext(trace_id=int(tid, 16), span_id=random.getrandbits(64) or 1, is_remote=True, trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED))
        return trace.set_span_in_context(trace.NonRecordingSpan(context))
    except Exception:  # noqa: BLE001
        return None


@contextlib.contextmanager
def span(name: str, *, kind: str = "internal", trace_id: Optional[str] = None, attributes: Optional[Dict[str, Any]] = None) -> Iterator[Any]:
    """在 with 块里开一个 span（未启用时什么都不做，yield None）。块里的异常照常向外抛，同时在 span 上记录脱敏后的错误。"""
    current = None
    token = None
    if active():
        try:
            from opentelemetry import context as otel_context, trace
            parent = _parent_context(trace_id)
            current = _state["tracer"].start_span(name[:120], context=parent, kind=getattr(trace.SpanKind, _KINDS.get(kind, "INTERNAL")),
                                                  attributes={k: v for k, v in (attributes or {}).items() if v is not None}, record_exception=False,
                                                  set_status_on_exception=False)
            token = otel_context.attach(trace.set_span_in_context(current, parent))
        except Exception:  # noqa: BLE001
            current, token = None, None
    try:
        yield current
    except BaseException as exc:
        if current is not None:
            try:
                from opentelemetry.trace import Status, StatusCode
                current.set_status(Status(StatusCode.ERROR, redact_text(f"{type(exc).__name__}: {exc}")[:200]))
                current.set_attribute("exception.type", type(exc).__name__)
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        if current is not None:
            try:
                current.end()
            except Exception:  # noqa: BLE001
                pass
        if token is not None:
            try:
                from opentelemetry import context as otel_context
                otel_context.detach(token)
            except Exception:  # noqa: BLE001
                pass


def set_attributes(current, **attributes) -> None:
    if current is None:
        return
    try:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
    except Exception:  # noqa: BLE001
        pass


def mark_error(current, message: str) -> None:
    if current is None:
        return
    try:
        from opentelemetry.trace import Status, StatusCode
        current.set_status(Status(StatusCode.ERROR, redact_text(message)[:200]))
    except Exception:  # noqa: BLE001
        pass


def rename(current, name: str) -> None:
    if current is None:
        return
    try:
        current.update_name(name[:120])
    except Exception:  # noqa: BLE001
        pass
