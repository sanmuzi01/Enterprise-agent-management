"""敏感信息脱敏：日志、Sentry、问题中心、Kafka 消息在落地或外发前都要过一遍。

两层：
1. 按字段名——字典里名字像密码、令牌、手机号、原文内容的键，值整个替换；
2. 按内容——字符串里出现的 Bearer 令牌、`sk-…` 密钥、手机号、身份证号、`password=…` 这类片段被遮盖。
脱敏是尽力而为的兜底，不能代替"不把敏感数据放进日志"的约定。
"""
import re
from typing import Any

REDACTED = "[已脱敏]"
MAX_DEPTH = 8
MAX_STRING = 2000

_SENSITIVE_KEYS = (
    "authorization", "cookie", "set-cookie", "password", "passwd", "secret", "token", "api_key", "apikey", "access_key",
    "phone", "mobile", "id_card", "idcard", "document_content", "source_text", "attachment", "model_prompt", "prompt",
    "content", "body", "verification_code", "sms_code", "x-signature", "x-context",
)
_EXACT_OK = {"max_tokens", "total_tokens", "input_tokens", "output_tokens", "token_count", "content_type", "content-type",
             "content_length", "content-length"}

_PATTERNS = [
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), REDACTED),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|token)(\s*[=:]\s*)[^\s,;&\"']+"), r"\1\2" + REDACTED),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "1**********"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[身份证号已脱敏]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[JWT已脱敏]"),
]


def is_sensitive_key(key: Any) -> bool:
    name = str(key).lower()
    if name in _EXACT_OK:
        return False
    return any(marker in name for marker in _SENSITIVE_KEYS)


def redact_text(value: str) -> str:
    text = value if len(value) <= MAX_STRING else value[:MAX_STRING] + "…[已截断]"
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact(value: Any, _depth: int = 0) -> Any:
    """返回脱敏后的副本（不修改入参）。"""
    if _depth > MAX_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        return {k: (REDACTED if is_sensitive_key(k) else redact(v, _depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact(v, _depth + 1) for v in list(value)[:200]]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes):
        return REDACTED
    return value
