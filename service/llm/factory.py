from service.llm.base import BaseLLM
from service.llm.glm_client import GLMClient
from service.llm.openai_compatible_client import OpenAICompatibleClient
from utils.logger_handler import get_logger

logger = get_logger("llm_factory")


class LLMFactory:
    """大模型工厂：根据模型名称创建对应客户端。"""

    _MODEL_MAP = {
        "glm-4": GLMClient,
        "glm-4-flash": GLMClient,
        "glm-4-plus": GLMClient,
        "glm-4v": GLMClient,
        "deepseek-chat": OpenAICompatibleClient,
        "deepseek-reasoner": OpenAICompatibleClient,
        "deepseek-coder": OpenAICompatibleClient,
        "gpt-4o": OpenAICompatibleClient,
        "gpt-4o-mini": OpenAICompatibleClient,
        "o3-mini": OpenAICompatibleClient,
        "o4-mini": OpenAICompatibleClient,
        "kimi-k2-0711-preview": OpenAICompatibleClient,
        "kimi-latest": OpenAICompatibleClient,
        "qwen-plus": OpenAICompatibleClient,
        "qwen-turbo": OpenAICompatibleClient,
        "qwen-max": OpenAICompatibleClient,
        "qwen-long": OpenAICompatibleClient,
    }

    @classmethod
    def create(cls, model_name: str, api_key: str, api_url: str = None) -> BaseLLM:
        from service.llm import offline_demo
        if model_name == offline_demo.MODEL_NAME and offline_demo.enabled():
            return offline_demo.OfflineDemoClient(api_key=api_key, api_url=api_url, model_name=model_name)
        client_class = cls._MODEL_MAP.get(model_name) or OpenAICompatibleClient
        logger.info(f"创建大模型客户端: {model_name} ({client_class.__name__})")
        return client_class(api_key=api_key, api_url=api_url, model_name=model_name)

    @classmethod
    def list_supported_models(cls) -> list[str]:
        return sorted(cls._MODEL_MAP.keys())
