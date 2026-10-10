"""把一个 AgentApiConnector 配置变成一个真正可被 LLM 调用的 LangChain 工具。

和 outline_generator 这类静态注册在 ToolRegistry 里的工具不一样：每个 Agent 配的
企业接口都不一样（不同 URL/参数），没法用一个固定的工具类描述所有情况，所以这里
直接按每条连接器动态构建一个 StructuredTool，工具名/描述/参数 schema 都来自用户
自己配的内容，LLM 看到的是一个专门的、有名字有意义的工具（比如"查询订单状态"），
而不是一个通用的"调用任意 HTTP 接口"。

安全边界（不能改）：
- URL、method、认证 header 全部来自数据库里用户预先配好的值，_run 闭包里直接用，
  不接受 LLM 传入 URL/headers。
- 每次实际发请求前都重新过一遍 validate_crawl_url（SSRF 校验），不信任建连接器
  时校验过一次就永远安全（DNS 可能变化）。
- 响应体大小设上限，避免把一个几十 MB 的接口响应整个塞进模型上下文。
"""
import json
import os
from typing import Any, Dict

import requests
from langchain_core.tools import BaseTool as LCBaseTool, StructuredTool
from pydantic import BaseModel

from service.http_resilience import request_with_retry
from service.tools.langchain_adapter import create_args_schema
from service.web_crawler_service import CrawlerError, validate_crawl_url
from utils.crypto import decrypt
from utils.logger_handler import get_logger

logger = get_logger("http_connector_tool")


class _NoArgs(BaseModel):
    pass


def _max_bytes() -> int:
    try:
        return int(os.getenv("AGENT_API_CONNECTOR_MAX_BYTES", str(256 * 1024)))
    except (TypeError, ValueError):
        return 256 * 1024


def _max_response_chars() -> int:
    try:
        return int(os.getenv("AGENT_API_CONNECTOR_MAX_RESPONSE_CHARS", "4000"))
    except (TypeError, ValueError):
        return 4000


def build_http_connector_tool(connector) -> LCBaseTool:
    """connector: models.init_db.AgentApiConnector 实例。"""
    headers: Dict[str, str] = {}
    if connector.headers_encrypted:
        try:
            headers = json.loads(decrypt(connector.headers_encrypted))
        except Exception as e:  # noqa: BLE001
            logger.error(f"企业接口连接器解密请求头失败: connector_id={connector.id}, error={e}")

    static_query: Dict[str, Any] = json.loads(connector.static_query_json or "{}")
    schema = json.loads(connector.param_schema_json or "{}")
    args_schema = create_args_schema(schema) or _NoArgs
    method = (connector.method or "GET").upper()
    raw_url = connector.url
    connector_id = connector.id

    def _run(**kwargs) -> str:
        try:
            url = validate_crawl_url(raw_url)
        except CrawlerError as e:
            return f"接口地址不允许访问：{e}"

        params = {**static_query, **kwargs} if method == "GET" else None
        body = {**static_query, **kwargs} if method == "POST" else None

        try:
            response = request_with_retry(
                service_name=f"agent_api_connector:{connector_id}",
                sender=lambda timeout: requests.request(
                    method, url, headers=headers, params=params, json=body, timeout=timeout,
                    stream=True, allow_redirects=False,       # 不跟随重定向：校验过的是这个地址，不是它指向的地址
                ),
                timeout_env="AGENT_API_CONNECTOR_TIMEOUT_SECONDS",
                default_timeout=10.0,
            )
            if 300 <= response.status_code < 400:
                response.close()
                return "接口返回了重定向，已拒绝跟随（重定向目标没有经过地址安全校验）；请把连接器地址改成最终地址"
            response.raise_for_status()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"企业接口调用失败: connector_id={connector_id}, error={e}")
            return f"接口调用失败：{e}"

        limit_bytes = _max_bytes()
        chunks, total = [], 0
        try:
            for chunk in response.iter_content(chunk_size=16384):
                total += len(chunk or b"")
                if total > limit_bytes:
                    response.close()
                    return f"接口返回内容过大（超过 {limit_bytes} 字节），无法处理"
                chunks.append(chunk)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"企业接口读取失败: connector_id={connector_id}, error={e}")
            return f"接口调用失败：{e}"
        content = b"".join(chunks)

        text = content.decode(response.encoding or "utf-8", errors="replace")
        limit = _max_response_chars()
        if len(text) > limit:
            text = text[:limit] + f"\n...(已截断，完整长度 {len(text)} 字符)"
        return text

    return StructuredTool.from_function(
        name=connector.name,
        description=connector.description,
        func=_run,
        args_schema=args_schema,
    )
