"""OpenTelemetry 封装的测试：未配置时是空操作、遥测故障不影响业务、trace_id 与我们自己的一致、错误消息脱敏、Collector 不可用时不拖慢请求。
真实 Collector / Tempo / Loki 上的行为见 scripts/e2e_observability.py。"""
import logging
import os
import time
import unittest
from unittest import mock

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from service.observability import context as trace_context, otel


def _install_memory_tracer():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel._state.update(tracer=provider.get_tracer("test"), tracer_provider=provider, logger_provider=None, handler=None)
    return exporter


class OtelDisabledTests(unittest.TestCase):
    def setUp(self):
        otel.shutdown()

    def test_unset_endpoint_is_noop(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
            self.assertFalse(otel.enabled())
            self.assertFalse(otel.init())
            self.assertFalse(otel.active())
            with otel.span("x", kind="server", trace_id="a" * 32, attributes={"k": "v"}) as current:
                self.assertIsNone(current)
                otel.set_attributes(current, a=1)
                otel.mark_error(current, "boom")
                otel.rename(current, "y")

    def test_exception_still_propagates_when_disabled(self):
        with self.assertRaises(ValueError):
            with otel.span("x"):
                raise ValueError("业务错误")


class OtelSpanTests(unittest.TestCase):
    def setUp(self):
        otel.shutdown()
        self.exporter = _install_memory_tracer()

    def tearDown(self):
        otel.shutdown()

    def test_trace_id_matches_our_own(self):
        trace_id = "0123456789abcdef0123456789abcdef"
        with otel.span("GET /x", kind="server", trace_id=trace_id):
            with otel.span("child", kind="client"):
                pass
        spans = {s.name: s for s in self.exporter.get_finished_spans()}
        self.assertEqual(format(spans["GET /x"].context.trace_id, "032x"), trace_id)
        self.assertEqual(format(spans["child"].context.trace_id, "032x"), trace_id)       # 子 span 沿用同一个 trace
        self.assertEqual(spans["child"].parent.span_id, spans["GET /x"].context.span_id)

    def test_trace_id_from_log_context_when_not_passed(self):
        trace_id = "fedcba9876543210fedcba9876543210"
        token = trace_context.set_trace(trace_id)
        try:
            with otel.span("job"):
                pass
        finally:
            trace_context.reset_trace(token) if hasattr(trace_context, "reset_trace") else None
        span = self.exporter.get_finished_spans()[0]
        self.assertEqual(format(span.context.trace_id, "032x"), trace_id)

    def test_bad_trace_id_does_not_break(self):
        with otel.span("x", trace_id="not-hex"):
            pass
        with otel.span("y", trace_id="z" * 32):
            pass
        self.assertEqual(len(self.exporter.get_finished_spans()), 2)

    def test_error_is_recorded_redacted_and_reraised(self):
        with self.assertRaises(RuntimeError):
            with otel.span("work", trace_id="a" * 32):
                raise RuntimeError("调用失败 password=hunter2 token=abcdefghijkl 手机 13800138000")
        span = self.exporter.get_finished_spans()[0]
        self.assertEqual(span.status.status_code.name, "ERROR")
        description = span.status.description
        self.assertNotIn("hunter2", description)
        self.assertNotIn("abcdefghijkl", description)
        self.assertNotIn("13800138000", description)
        self.assertEqual(span.attributes["exception.type"], "RuntimeError")

    def test_none_attributes_are_dropped_and_names_truncated(self):
        with otel.span("n" * 500, attributes={"keep": "yes", "drop": None}) as current:
            otel.set_attributes(current, status=200, nothing=None)
            otel.rename(current, "r" * 500)
        span = self.exporter.get_finished_spans()[0]
        self.assertEqual(span.attributes.get("keep"), "yes")
        self.assertNotIn("drop", span.attributes)
        self.assertNotIn("nothing", span.attributes)
        self.assertEqual(span.attributes["status"], 200)
        self.assertLessEqual(len(span.name), 120)

    def test_broken_tracer_never_affects_business(self):
        class Broken:
            def start_span(self, *args, **kwargs):
                raise RuntimeError("遥测库内部错误")
        otel._state["tracer"] = Broken()
        ran = []
        with otel.span("x") as current:
            ran.append(current)
        self.assertEqual(ran, [None])
        with self.assertRaises(KeyError):
            with otel.span("x"):
                raise KeyError("业务异常照常抛出")

    def test_broken_span_end_is_swallowed(self):
        class BadSpan:
            def set_attribute(self, *a, **k): raise RuntimeError("x")
            def set_status(self, *a, **k): raise RuntimeError("x")
            def update_name(self, *a, **k): raise RuntimeError("x")
            def end(self): raise RuntimeError("x")
        otel.set_attributes(BadSpan(), a=1)
        otel.mark_error(BadSpan(), "m")
        otel.rename(BadSpan(), "n")


class OtelLogHandlerTests(unittest.TestCase):
    """送去 Loki 的日志：先脱敏、不带异常堆栈、带上当前请求的 trace_id（哪怕日志不是写在 span 里面）。"""

    def test_redacts_drops_traceback_and_carries_trace_id(self):
        from opentelemetry import trace
        seen = []

        class Base(logging.Handler):
            def emit(self, record):
                seen.append((record.getMessage(), record.exc_info, trace.get_current_span().get_span_context().trace_id))

        handler = otel._handler_class(Base)()
        token = trace_context.set_trace("c" * 32)
        try:
            try:
                raise ValueError("secret=abc")
            except ValueError:
                import sys
                record = logging.LogRecord("app", logging.ERROR, __file__, 1, "登录 password=%s 手机 %s", ("hunter2", "13800138000"), sys.exc_info())
            handler.emit(record)
        finally:
            trace_context.reset_trace(token)
        message, exc_info, trace_id = seen[0]
        self.assertNotIn("hunter2", message)
        self.assertNotIn("13800138000", message)
        self.assertIsNone(exc_info)
        self.assertEqual(format(trace_id, "032x"), "c" * 32)
        self.assertIn("hunter2", record.getMessage())            # 原始记录不被改动（别的 handler 还要用）

    def test_handler_failure_never_raises(self):
        class Base(logging.Handler):
            def emit(self, record):
                raise RuntimeError("导出器内部错误")
        handler = otel._handler_class(Base)()
        handler.emit(logging.LogRecord("app", logging.INFO, __file__, 1, "x", (), None))


class OtelUnreachableCollectorTests(unittest.TestCase):
    """Collector 不在时：初始化成功（导出走后台线程）、业务代码不变慢、退出不被拖住。"""

    def setUp(self):
        self._mute = lambda record: False                         # 导出失败是预期的：别让 OTel 自己的错误日志落到测试输出里
        for name in otel.NOISY_LOGGERS:
            logging.getLogger(name).addFilter(self._mute)

    def tearDown(self):
        for name in otel.NOISY_LOGGERS:
            logging.getLogger(name).removeFilter(self._mute)
            logging.getLogger(name).filters = [f for f in logging.getLogger(name).filters if not isinstance(f, otel._RateLimited)]
        otel.shutdown()
        logging.getLogger().handlers = [h for h in logging.getLogger().handlers if "otel" not in type(h).__module__]

    def test_requests_unaffected_and_shutdown_bounded(self):
        otel.shutdown()
        with mock.patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1", "OTEL_EXPORT_TIMEOUT_SECONDS": "1"}):
            self.assertTrue(otel.init("test-service"))
            self.assertTrue(otel.active())
            with otel.span("recording check", trace_id="d" * 32) as current:
                self.assertTrue(current.is_recording(), "init 之后开出来的必须是真正记录的 span（曾经误用全局空 tracer，span 全部被丢弃）")
            started = time.perf_counter()
            for index in range(500):
                with otel.span(f"op {index}", kind="server", trace_id="b" * 32, attributes={"i": index}):
                    logging.getLogger("otel_test").info("处理中 %s", index)
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 3.0, "Collector 不可用时业务路径不能被拖慢")
            shutdown_started = time.perf_counter()
            otel.shutdown(timeout_seconds=1.0)
            self.assertLess(time.perf_counter() - shutdown_started, 8.0)
            self.assertFalse(otel.active())

    def test_shutdown_keeps_retired_providers_alive_for_sdk_fork_callbacks(self):
        """OTel SDK 的 fork 回调只持有批处理器的弱引用：provider 被回收后，之后任何一次 fork 都会报
        “'NoneType' object is not callable”（在 Linux CI 上看到过）。shutdown 之后要留着引用。"""
        with mock.patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1", "OTEL_EXPORT_TIMEOUT_SECONDS": "1"}):
            self.assertTrue(otel.init("retire-test"))
            provider = otel._state["tracer_provider"]
            before = len(otel._RETIRED)
            otel.shutdown(timeout_seconds=1.0)
        self.assertGreater(len(otel._RETIRED), before)
        self.assertTrue(any(p is provider for p in otel._RETIRED))

    def test_init_failure_is_swallowed(self):
        otel.shutdown()
        with mock.patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1"}):
            with mock.patch("opentelemetry.sdk.trace.TracerProvider", side_effect=RuntimeError("坏了")):
                with self.assertLogs("otel_setup", level="WARNING") as captured:
                    self.assertFalse(otel.init())
            self.assertIn("初始化失败", captured.output[0])
            self.assertFalse(otel.active())

    def test_rate_limited_filter_passes_one_per_minute(self):
        limiter = otel._RateLimited()
        record = logging.LogRecord("opentelemetry.exporter", logging.ERROR, __file__, 1, "x", (), None)
        self.assertTrue(limiter.filter(record))
        self.assertFalse(limiter.filter(record))


if __name__ == "__main__":
    unittest.main()
