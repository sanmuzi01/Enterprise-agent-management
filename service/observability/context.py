"""请求级追踪上下文（trace_id 等），用 contextvars 保存，同一个请求里的日志、对 Java 的调用、问题记录都能取到。

trace_id 的来源（按优先级）：W3C `traceparent` 请求头里的 trace-id → 前端 X-Request-ID（8–80 位安全字符）→ 新生成。
统一成 32 位小写十六进制（OpenTelemetry 的 trace-id 格式）；不是这个格式的外部值用 sha256 前 32 位折算，保证稳定、不泄露原值。
"""
import contextvars
import hashlib
import re
import uuid
from typing import Dict, Optional

_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)
_fields: contextvars.ContextVar[Dict[str, object]] = contextvars.ContextVar("trace_fields", default={})

_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")
_SAFE_ID = re.compile(r"^[a-zA-Z0-9_.:-]{8,80}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def new_trace_id() -> str:
    return uuid.uuid4().hex


def normalize(value: Optional[str]) -> Optional[str]:
    raw = (value or "").strip()
    if not _SAFE_ID.match(raw):
        return None
    lowered = raw.lower().replace("-", "")
    if _HEX32.match(lowered) and lowered != "0" * 32:
        return lowered
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def from_headers(traceparent: Optional[str], request_id: Optional[str]) -> str:
    match = _TRACEPARENT.match((traceparent or "").strip().lower())
    if match and match.group(1) != "0" * 32:
        return match.group(1)
    return normalize(request_id) or new_trace_id()


def traceparent_for(trace_id: Optional[str] = None) -> str:
    """向下游传播用：同一个 trace-id，新的 span-id。"""
    return f"00-{trace_id or current_trace_id() or new_trace_id()}-{uuid.uuid4().hex[:16]}-01"


def set_trace(trace_id: str, **fields) -> contextvars.Token:
    _fields.set({k: v for k, v in fields.items() if v is not None})
    return _trace_id.set(trace_id)


def reset_trace(token: contextvars.Token) -> None:
    _trace_id.reset(token)
    _fields.set({})


def current_trace_id() -> Optional[str]:
    return _trace_id.get()


def add_fields(**fields) -> None:
    """请求处理过程中补充上下文（登录后补 user_id、进入业务后补 department_id / operation）。"""
    merged = dict(_fields.get())
    merged.update({k: v for k, v in fields.items() if v is not None})
    _fields.set(merged)


def current_fields() -> Dict[str, object]:
    return dict(_fields.get())
