"""数据源：用户自定义的外部接口地址（P2）。

用户直接填一个 URL，平台去取数据。因为目标 URL 完全由用户控制，这里必须：
- 出站前做 SSRF 校验（复用 web_crawler_service.validate_crawl_url：挡内网 / localhost /
  云元数据地址，并对域名解析出的所有 IP 逐个检查）；
- 走统一的超时 / 重试 / 指数退避 / 熔断封装（http_resilience.async_request_with_retry）；
- 限制响应体大小，避免把内存打爆。

config:
  - url:      必填，http/https
  - method:   GET（默认）/ POST
  - headers:  选填，最多 10 个，值会被转成字符串；Host / Content-Length 之类被忽略
  - body:     选填，仅 POST，作为 JSON 发送
  - as:       "json"（默认，解析失败则回退文本）/ "text"
  - json_path: 选填，安全点路径，只取响应里的一部分（见 processors.resolve_path）
"""

import asyncio
import json
import os
from typing import Any, Dict, List

from utils.logger_handler import get_logger
from service.widgets.connectors.base import BaseConnector
from service.widgets.context import WidgetRunContext

logger = get_logger("widget_http_connector")

_ALLOWED_METHODS = {"GET", "POST"}
_DROP_HEADERS = {"host", "content-length", "connection", "cookie", "authorization"}


def _max_bytes() -> int:
    try:
        return int(os.getenv("WIDGET_HTTP_MAX_BYTES", str(1024 * 1024)))
    except (TypeError, ValueError):
        return 1024 * 1024


def _clean_headers(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for key, value in list(raw.items())[:10]:
        name = str(key).strip()
        if not name or name.lower() in _DROP_HEADERS:
            continue
        out[name] = str(value)[:1024]
    return out


class HttpConnector(BaseConnector):
    kind = "http"
    label = "外部接口地址"

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        config = config or {}
        errors: List[str] = []
        url = str(config.get("url") or "").strip()
        if not url:
            errors.append("请填写要访问的接口地址（URL）")
        elif not url.lower().startswith(("http://", "https://")):
            errors.append("接口地址必须以 http:// 或 https:// 开头")
        method = str(config.get("method") or "GET").strip().upper()
        if method not in _ALLOWED_METHODS:
            errors.append("接口请求方式只支持 GET 或 POST")
        if config.get("headers") is not None and not isinstance(config.get("headers"), dict):
            errors.append("请求头需要是键值对")
        return errors

    async def fetch(self, ctx: WidgetRunContext, config: Dict[str, Any]) -> Any:
        from service.http_resilience import async_request_with_retry
        from service.web_crawler_service import CrawlerError, validate_crawl_url

        config = config or {}
        raw_url = str(config.get("url") or "").strip()
        method = str(config.get("method") or "GET").strip().upper()
        headers = _clean_headers(config.get("headers"))
        body = config.get("body") if method == "POST" else None
        want = str(config.get("as") or "json").strip().lower()

        try:
            url = await asyncio.to_thread(validate_crawl_url, raw_url)
        except CrawlerError as exc:
            from service.exceptions import InvalidInput
            raise InvalidInput(f"这个接口地址不允许访问：{exc}") from exc

        host = url.split("//", 1)[-1].split("/", 1)[0]
        response = await async_request_with_retry(
            service_name=f"widget_http:{host}",
            sender=lambda client: client.request(method, url, headers=headers, json=body),
            timeout_env="WIDGET_HTTP_TIMEOUT_SECONDS",
            default_timeout=10.0,
        )
        response.raise_for_status()

        content = response.content or b""
        if len(content) > _max_bytes():
            from service.exceptions import UpstreamError
            raise UpstreamError(f"接口返回内容过大（超过 {_max_bytes()} 字节），请缩小范围或换一个接口")

        text = content.decode(response.encoding or "utf-8", errors="replace")
        parsed: Any
        if want == "text":
            parsed = text
        else:
            try:
                parsed = json.loads(text) if text.strip() else None
            except (ValueError, RecursionError):          # 深度嵌套的 JSON 会让解析器 RecursionError：当作普通文本，不能让组件整个失败
                parsed = text

        json_path = str(config.get("json_path") or "").strip()
        if json_path:
            from service.widgets.processors import resolve_path

            parsed = resolve_path(parsed, json_path)

        meta = {
            "url": url,
            "status": response.status_code,
            "fetched_at": ctx.now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        # 让下游处理器能直接看到列表 / 对象里的字段（_as_rows 认 rows/items/data/list/results）
        if isinstance(parsed, list):
            return {"rows": parsed, "data": parsed, "_meta": meta}
        if isinstance(parsed, dict):
            return {**parsed, "data": parsed, "_meta": meta}
        if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
            return {"value": float(parsed), "data": parsed, "_meta": meta}
        return {"text": str(parsed) if parsed is not None else "", "data": parsed, "_meta": meta}


CONNECTOR = HttpConnector()
