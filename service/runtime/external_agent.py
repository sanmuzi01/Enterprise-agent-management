"""外部 Agent 服务的调用层（传输 + 安全边界）。

协议见 docs/external-agent-protocol.md。这里只做三件事：
1. 校验地址（防 SSRF）：默认只允许公网地址；企业内网里的 Agent 服务必须由运维把主机写进
   EXTERNAL_AGENT_ALLOWED_HOSTS 才放行；云元数据地址任何情况下都不放行；不跟随重定向。
2. 给每个请求签名：X-Agent-Signature = HMAC-SHA256(密钥, 时间戳 + "." + 请求体)，
   对方据此确认请求来自平台、没被改动、没有被重放。
3. 限制对方：超时、响应体大小、答案长度都有上限，对方的任何输出都当作不可信文本。

地址、密钥、附加请求头都来自管理员预先配置的数据库记录，用户输入和模型输出无法改变它们。
"""
import hashlib
import hmac
import ipaddress
import json
import os
import socket
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlparse

import requests

from utils.logger_handler import get_logger

logger = get_logger("external_agent")

PROTOCOL = "enterprise-agent/1"
SIGNATURE_HEADER = "X-Agent-Signature"
TIMESTAMP_HEADER = "X-Agent-Timestamp"
REQUEST_ID_HEADER = "X-Agent-Request-Id"

MAX_HISTORY_MESSAGES = 20
MAX_HISTORY_CHARS = 4000
MAX_MESSAGE_CHARS = 8000

# 任何情况下都不允许的目标：云厂商元数据服务（拿到就能偷实例凭据）
_ALWAYS_BLOCKED = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("100.100.100.200"),
                   ipaddress.ip_address("fd00:ec2::254")}


class ExternalAgentError(Exception):
    """调用外部 Agent 失败。message 可以直接给用户看，不含对方的内部细节。"""


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def max_response_bytes() -> int:
    return _env_int("EXTERNAL_AGENT_MAX_RESPONSE_BYTES", 1024 * 1024)


def max_answer_chars() -> int:
    return _env_int("EXTERNAL_AGENT_MAX_ANSWER_CHARS", 50000)


def max_timeout_seconds() -> int:
    return _env_int("EXTERNAL_AGENT_MAX_TIMEOUT_SECONDS", 300)


def allowed_hosts() -> set:
    """运维明确放行的内网主机（逗号分隔，小写；可写 host 或 host:port）。"""
    raw = os.getenv("EXTERNAL_AGENT_ALLOWED_HOSTS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _is_production() -> bool:
    return os.getenv("APP_ENV", "development").strip().lower() == "production"


def validate_endpoint_url(url: str) -> str:
    """校验外部 Agent 的地址，返回规范化后的地址；不合规抛 ExternalAgentError。"""
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ExternalAgentError("地址必须以 http:// 或 https:// 开头")
    if not parsed.hostname:
        raise ExternalAgentError("地址缺少主机名")
    if parsed.username or parsed.password or "@" in parsed.netloc or "\\" in parsed.netloc:
        raise ExternalAgentError("地址里不能带用户名或密码，认证信息请放在附加请求头里")
    host = parsed.hostname.lower()
    host_port = f"{host}:{parsed.port}" if parsed.port else host
    listed = host in allowed_hosts() or host_port in allowed_hosts()

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    addresses = [literal] if literal is not None else _resolve(host)
    if any(address in _ALWAYS_BLOCKED for address in addresses):
        raise ExternalAgentError("不允许访问云平台元数据地址")

    if listed:
        pass
    else:
        from service.web_crawler_service import CrawlerError, validate_crawl_url
        try:
            validate_crawl_url(raw)
        except CrawlerError as exc:
            raise ExternalAgentError(
                f"{exc}。如果这是企业内网里的 Agent 服务，请让运维把主机名加入 EXTERNAL_AGENT_ALLOWED_HOSTS"
            ) from exc
    if parsed.scheme == "http" and _is_production() and not listed:
        raise ExternalAgentError("生产环境的外部 Agent 必须使用 https（内网主机加入白名单后可用 http）")
    return parsed.geturl()


def _resolve(host: str) -> List[Any]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ExternalAgentError("地址的主机名无法解析") from exc
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def sign(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str, *, tolerance_seconds: int = 300,
                     now: Optional[float] = None) -> bool:
    """参考实现：对方（外部 Agent）校验平台请求时用的就是这个函数的逻辑。"""
    try:
        if abs((now if now is not None else time.time()) - float(timestamp)) > tolerance_seconds:
            return False
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature or "")


def clip_history(history: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
    cleaned = []
    for item in (history or [])[-MAX_HISTORY_MESSAGES:]:
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        cleaned.append({"role": role, "content": str(item.get("content") or "")[:MAX_HISTORY_CHARS]})
    return cleaned


def build_payload(*, kind: str, agent: Dict[str, Any], user: Dict[str, Any], message: str,
                  history: Optional[List[Dict[str, str]]], run_id: Optional[int],
                  conversation_id: Optional[int], knowledge: Optional[Dict[str, Any]] = None,
                  request_id: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "protocol": PROTOCOL,
        "type": kind,                      # chat | ping
        "request_id": request_id or uuid.uuid4().hex,
        "run_id": run_id,
        "conversation_id": conversation_id,
        "agent": agent,
        "user": user,
        "message": (message or "")[:MAX_MESSAGE_CHARS],
        "history": clip_history(history),
    }
    if knowledge:
        payload["knowledge"] = knowledge
    return payload


class EndpointConfig:
    """调用所需的明文配置（密钥和请求头已解密）。只在调用期间存在于内存里。"""

    def __init__(self, url: str, secret: str, headers: Optional[Dict[str, str]] = None, timeout_seconds: int = 60):
        self.url = url
        self.secret = secret
        self.headers = headers or {}
        self.timeout_seconds = max(1, min(int(timeout_seconds or 60), max_timeout_seconds()))


def _open(cfg: EndpointConfig, payload: Dict[str, Any], *, stream: bool) -> requests.Response:
    url = validate_endpoint_url(cfg.url)               # 每次调用前重新校验：DNS 可能在配置之后变化
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    headers = {
        **{k: v for k, v in cfg.headers.items() if k.lower() not in {"content-type", "accept", SIGNATURE_HEADER.lower(),
                                                                       TIMESTAMP_HEADER.lower(), REQUEST_ID_HEADER.lower()}},
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "text/event-stream, application/json" if stream else "application/json",
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: sign(cfg.secret, timestamp, body),
        REQUEST_ID_HEADER: str(payload.get("request_id") or ""),
        "User-Agent": "EnterpriseAgentPlatform/1",
    }
    try:
        response = requests.post(url, data=body, headers=headers, timeout=(min(10, cfg.timeout_seconds), cfg.timeout_seconds),
                                 stream=True, allow_redirects=False)
    except requests.ConnectTimeout as exc:
        raise ExternalAgentError("无法连接外部 Agent 服务（连接超时）") from exc
    except requests.Timeout as exc:
        raise ExternalAgentError("外部 Agent 服务响应超时") from exc
    except requests.RequestException as exc:
        logger.warning(f"外部 Agent 连接失败: {type(exc).__name__}")
        raise ExternalAgentError("无法连接外部 Agent 服务") from exc
    if 300 <= response.status_code < 400:
        response.close()
        raise ExternalAgentError("外部 Agent 服务返回了重定向，已拒绝跟随；请把地址改成最终地址")
    if response.status_code >= 400:
        response.close()
        raise ExternalAgentError(f"外部 Agent 服务返回错误（HTTP {response.status_code}）")
    return response


def _read_limited(response: requests.Response, deadline: float) -> bytes:
    limit, chunks, total = max_response_bytes(), [], 0
    for chunk in response.iter_content(chunk_size=16384):
        total += len(chunk or b"")
        if total > limit:
            response.close()
            raise ExternalAgentError("外部 Agent 服务返回的内容过大，已中止")
        if time.monotonic() > deadline:
            response.close()
            raise ExternalAgentError("外部 Agent 服务响应超时")
        chunks.append(chunk)
    return b"".join(chunks)


def _clean_result(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ExternalAgentError("外部 Agent 服务返回的格式不正确")
    answer = data.get("answer")
    if not isinstance(answer, str):
        raise ExternalAgentError("外部 Agent 服务的返回里缺少 answer 文本")
    steps = []
    for item in (data.get("steps") or [])[:50]:
        if isinstance(item, dict):
            steps.append({"title": str(item.get("title") or "")[:120], "detail": str(item.get("detail") or "")[:2000]})
    citations = []
    for item in (data.get("citations") or [])[:50]:
        if isinstance(item, dict):
            citations.append({k: (str(v)[:500] if not isinstance(v, (int, float)) else v) for k, v in list(item.items())[:10]})
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
    tokens = usage.get("total_tokens") if usage else None
    return {
        "answer": answer[:max_answer_chars()],
        "steps": steps,
        "citations": citations,
        "usage": {"total_tokens": int(tokens)} if isinstance(tokens, (int, float)) else None,
    }


def call(cfg: EndpointConfig, payload: Dict[str, Any]) -> Dict[str, Any]:
    """一次性调用，返回 {answer, steps, citations, usage}。对方即使返回了事件流也会被合并成一个结果。"""
    result = None
    deltas: List[str] = []
    extra_steps, extra_citations, usage = [], [], None
    for event in stream(cfg, payload):
        kind = event["type"]
        if kind == "delta":
            deltas.append(event["text"])
        elif kind == "step":
            extra_steps.append({"title": event["title"], "detail": event["detail"]})
        elif kind == "citations":
            extra_citations = event["items"]
        elif kind == "result":
            result = event["data"]
    if result is None:
        result = {"answer": "".join(deltas), "steps": extra_steps, "citations": extra_citations, "usage": usage}
    return result


def stream(cfg: EndpointConfig, payload: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """调用并产出归一化事件：delta{text} / step{title,detail} / citations{items} / result{data}。

    对方返回 application/json → 只产出一个 result；返回 text/event-stream → 逐条解析 data: 行，
    事件 JSON 的 type 可以是 delta / step / citations / done / error。"""
    deadline = time.monotonic() + cfg.timeout_seconds
    response = _open(cfg, payload, stream=True)
    content_type = (response.headers.get("Content-Type") or "").lower()
    try:
        if "text/event-stream" not in content_type:
            raw = _read_limited(response, deadline)
            try:
                data = json.loads(raw.decode(response.encoding or "utf-8", errors="replace"))
            except ValueError as exc:
                raise ExternalAgentError("外部 Agent 服务返回的不是合法的 JSON") from exc
            yield {"type": "result", "data": _clean_result(data)}
            return

        answer_parts: List[str] = []
        steps, citations, usage, total, answer_len = [], [], None, 0, 0
        limit, cap = max_response_bytes(), max_answer_chars()
        for raw_line in response.iter_lines(decode_unicode=False):
            if time.monotonic() > deadline:
                raise ExternalAgentError("外部 Agent 服务响应超时")
            total += len(raw_line or b"") + 1
            if total > limit:
                raise ExternalAgentError("外部 Agent 服务返回的内容过大，已中止")
            line = (raw_line or b"").decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "delta":
                text = str(event.get("text") or "")[: max(0, cap - answer_len)]
                if text:
                    answer_len += len(text)
                    answer_parts.append(text)
                    yield {"type": "delta", "text": text}
            elif kind == "step":
                step = {"title": str(event.get("title") or "")[:120], "detail": str(event.get("detail") or "")[:2000]}
                if len(steps) < 50:
                    steps.append(step)
                    yield {"type": "step", **step}
            elif kind == "citations":
                cleaned = _clean_result({"answer": "", "citations": event.get("items")})["citations"]
                citations = cleaned
                yield {"type": "citations", "items": cleaned}
            elif kind == "error":
                raise ExternalAgentError(str(event.get("message") or "外部 Agent 服务报告了错误")[:200])
            elif kind == "done":
                if isinstance(event.get("usage"), dict):
                    tokens = event["usage"].get("total_tokens")
                    usage = {"total_tokens": int(tokens)} if isinstance(tokens, (int, float)) else None
                break
        yield {"type": "result", "data": {"answer": "".join(answer_parts), "steps": steps, "citations": citations, "usage": usage}}
    finally:
        response.close()
