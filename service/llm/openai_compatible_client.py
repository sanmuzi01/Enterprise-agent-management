import json
from typing import Dict, Generator, List
import httpx
import requests

from service.llm.base import BaseLLM
from service.llm.usage import from_openai_usage
from service.http_resilience import async_request_with_retry, request_with_retry, stream_request_with_circuit
from utils.logger_handler import get_logger

logger = get_logger("openai_compatible_client")
class OpenAICompatibleClient(BaseLLM):
    """通用 OpenAI-compatible Chat Completions 客户端。"""
    DEFAULT_BASE_URLS = {
        "deepseek": "https://api.deepseek.com/v1",
        "openai": "https://api.openai.com/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "moonshot": "https://api.moonshot.cn/v1",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "perplexity": "https://api.perplexity.ai",
    }

    def __init__(self, api_key: str, api_url: str = None, model_name: str = None):
        super().__init__(api_key, api_url, model_name)
        normalized = self._normalize_chat_url(api_url or self._default_base_url(model_name))
        # api_url 可能是用户在「模型连接」里自填的自定义端点——出站前必须做和其它
        # 用户可控 URL（Webhook、企业接口连接器、组件 HTTP 数据源）同样的 SSRF 校验，
        # 否则模型的回答会把内网/云元数据接口的响应原样"读"给用户，等于一条数据泄露通道。
        from service.web_crawler_service import CrawlerError, validate_crawl_url
        try:
            self.api_url = validate_crawl_url(normalized)
        except CrawlerError as exc:
            raise ValueError(f"模型端点地址不合法或不允许访问: {exc}") from exc

    def _default_base_url(self, model_name: str) -> str:
        model = (model_name or "").lower()
        if model.startswith("deepseek"):
            return self.DEFAULT_BASE_URLS["deepseek"]
        if model.startswith(("gpt", "o1", "o3", "o4")):
            return self.DEFAULT_BASE_URLS["openai"]
        if model.startswith("kimi"):
            return self.DEFAULT_BASE_URLS["moonshot"]
        if model.startswith("qwen"):
            return self.DEFAULT_BASE_URLS["qwen"]
        if model.startswith("sonar"):
            return self.DEFAULT_BASE_URLS["perplexity"]
        return self.DEFAULT_BASE_URLS["zhipu"]

    def _normalize_chat_url(self, api_url: str) -> str:
        url = (api_url or "").rstrip("/")
        if url.endswith("/chat/completions"):
            return url
        if url.endswith("/v1") or url.endswith("/paas/v4"):
            return f"{url}/chat/completions"
        return f"{url}/chat/completions"

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.5) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        try:
            logger.info(f"[OpenAI-compatible] 请求: model={self.model_name}, url={self.api_url}")
            response = request_with_retry(
                service_name=f"llm:{self.model_name}",
                sender=lambda timeout: requests.post(self.api_url, headers=headers, json=payload, timeout=timeout, allow_redirects=False),
                timeout_env="LLM_REQUEST_TIMEOUT_SECONDS",
                default_timeout=60,
            )
            response.raise_for_status()
            result = response.json()
            self.last_usage = from_openai_usage(result.get("usage"))
            return result["choices"][0]["message"]["content"] or ""
        except requests.exceptions.RequestException as e:
            detail = getattr(e.response, "text", "")[:500] if getattr(e, "response", None) else str(e)
            logger.error(f"[OpenAI-compatible] 请求失败: {detail}")
            raise Exception(f"大模型请求失败: {detail}")
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            logger.error(f"[OpenAI-compatible] 响应解析失败: {e}")
            raise Exception("大模型响应解析失败")

    async def achat(self, messages: List[Dict[str, str]], temperature: float = 0.5,
                    web_search: bool = False) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if web_search:
            from service.llm.web_search import search_payload_extras
            payload.update(search_payload_extras(self.model_name))
        try:
            logger.info(f"[OpenAI-compatible] 请求: model={self.model_name}, url={self.api_url}, web_search={web_search}")
            response = await async_request_with_retry(
                service_name=f"llm:{self.model_name}",
                sender=lambda client: client.post(self.api_url, headers=headers, json=payload),
                timeout_env="LLM_REQUEST_TIMEOUT_SECONDS",
                default_timeout=60,
            )
            response.raise_for_status()
            result = response.json()
            self.last_usage = from_openai_usage(result.get("usage"))
            return result["choices"][0]["message"]["content"] or ""
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:500] if e.response is not None else str(e)
            logger.error(f"[OpenAI-compatible] 请求失败: {detail}")
            raise Exception(f"大模型请求失败: {detail}")
        except httpx.HTTPError as e:
            logger.error(f"[OpenAI-compatible] 请求失败: {e}")
            raise Exception(f"大模型请求失败: {e}")
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            logger.error(f"[OpenAI-compatible] 响应解析失败: {e}")
            raise Exception("大模型响应解析失败")

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.5,
    ) -> Generator[str, None, None]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        try:
            logger.info(f"[OpenAI-compatible] 流式请求: model={self.model_name}, url={self.api_url}")
            with stream_request_with_circuit(
                service_name=f"llm_stream:{self.model_name}",
                sender=lambda timeout: requests.post(self.api_url, headers=headers, json=payload, stream=True, timeout=timeout, allow_redirects=False),
                timeout_env="LLM_STREAM_TIMEOUT_SECONDS",
                default_timeout=60,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content") or ""
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
        except requests.exceptions.RequestException as e:
            detail = getattr(e.response, "text", "")[:500] if getattr(e, "response", None) else str(e)
            logger.error(f"[OpenAI-compatible] 流式请求失败: {detail}")
            raise Exception(f"大模型流式请求失败: {detail}")
