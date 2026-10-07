import httpx
import requests
import json
from typing import Dict,List,Generator
from service.llm.base import BaseLLM
from service.llm.usage import from_openai_usage
from service.http_resilience import async_request_with_retry, request_with_retry, stream_request_with_circuit
from utils.logger_handler import get_logger
logger = get_logger("glm_client")

class GLMClient(BaseLLM):
    DEFAULT_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    def __init__(self,api_key,api_url=None,model_name="glm-4"):
        super().__init__(api_key, api_url, model_name)
        raw_url = api_url or self.DEFAULT_API_URL
        # api_url 可能是用户在「模型连接」里自填的自定义端点——出站前必须做和其它
        # 用户可控 URL（Webhook、企业接口连接器、组件 HTTP 数据源）同样的 SSRF 校验，
        # 否则模型的回答会把内网/云元数据接口的响应原样"读"给用户，等于一条数据泄露通道。
        from service.web_crawler_service import CrawlerError, validate_crawl_url
        try:
            self.api_url = validate_crawl_url(raw_url)
        except CrawlerError as exc:
            raise ValueError(f"模型端点地址不合法或不允许访问: {exc}") from exc
    async def achat(self, messages, temperature=0.5, web_search: bool = False) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",  # API Key 认证
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model_name,  # 模型名：glm-4 / glm-4-flash 等
            "messages": messages,  # 对话历史：system + user + assistant
            "temperature": temperature,  # 创造性：0=保守, 1=天马行空
            "stream": False  # False = 一次性返回
        }
        if web_search:
            from service.llm.web_search import search_payload_extras
            payload.update(search_payload_extras(self.model_name))
        try:
            response = await async_request_with_retry(
                service_name=f"llm:{self.model_name}",
                sender=lambda client: client.post(self.api_url, headers=headers, json=payload),
                timeout_env="LLM_REQUEST_TIMEOUT_SECONDS",
                default_timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            self.last_usage = from_openai_usage(result.get("usage"))
            return result["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            raise Exception(f"大模型请求失败: {e}")
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise Exception(f"大模型响应解析失败: {e}")
    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.5) -> str:
        headers = {
        "Authorization": f"Bearer {self.api_key}",   # API Key 认证
        "Content-Type": "application/json"
        }
        payload = {
                "model": self.model_name,      # 模型名：glm-4 / glm-4-flash 等
                "messages": messages,          # 对话历史：system + user + assistant
                "temperature": temperature,    # 创造性：0=保守, 1=天马行空
                "stream": False                # False = 一次性返回
        }
        try:
            logger.info(f"[GLM] 请求: model={self.model_name}, messages={len(messages)}条, temp={temperature}")
            response = request_with_retry(
                service_name=f"llm:{self.model_name}",
                sender=lambda timeout: requests.post(self.api_url, headers=headers, json=payload, timeout=timeout, allow_redirects=False),
                timeout_env="LLM_REQUEST_TIMEOUT_SECONDS",
                default_timeout=60,
            )
        # HTTP 状态码异常（4xx/5xx）直接抛
            response.raise_for_status()
        # 解析响应
            result = response.json()
            self.last_usage = from_openai_usage(result.get("usage"))
            content = result["choices"][0]["message"]["content"]
            logger.info(f"[GLM] 响应成功: {len(content)}字")
            return content
        except requests.exceptions.RequestException as e:
            logger.error(f"[GLM] 请求失败: {e}")
            raise Exception(f"大模型请求失败{e}")
        except(KeyError,IndexError,json.JSONDecodeError) as e:
            logger.error(f"[GLM] 响应解析失败: {e}, 原始响应: {response.text[:500]}")
            raise Exception("大模型响应解析失败")

    #流式对话
    def stream_chat(
            self,messages:List[Dict[str,str]],
            temperature:float=0.5) ->Generator[str,None,None]:
        """流式对话：逐字 yield 返回，适用于打字机效果:return: 生成器，每次产出一段文字（可能是字、词或片段）"""
        headers = {
            "Authorization":f"Bearer {self.api_key}",
            "Content-Type": "application/json"
            }
        payload ={
            "model": self.model_name,
            "messages":messages,
            "temperature":temperature,
            "stream":True
        }
        try:
            logger.info(f"[GLM] 流式请求: model={self.model_name}")
            with stream_request_with_circuit(
                service_name=f"llm_stream:{self.model_name}",
                sender=lambda timeout: requests.post(self.api_url, headers=headers, json=payload, stream=True, timeout=timeout, allow_redirects=False),
                timeout_env="LLM_STREAM_TIMEOUT_SECONDS",
                default_timeout=60,
            ) as response:
                response.raise_for_status()
        # SSE 格式：每行以 "data: " 开头，最后一行为 "data: [DONE]"
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip()=="[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices",[{}])[0].get("delta",{})
                            content = delta.get("content","")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
            logger.info("[GLM] 流式响应完成")
        except requests.exceptions.RequestException as e:
            logger.error(f"[GLM] 流式请求失败: {e}")
            raise Exception(f"大模型流式请求失败: {e}")
