from typing import Dict, Optional

from langchain_openai import ChatOpenAI

from service.llm.llm_config_service import get_api_config
from utils.logger_handler import get_logger

logger = get_logger("llm_adapter")

PROVIDER_BASE_URLS = {
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "deepseek": "https://api.deepseek.com/v1",
    "openai": None,
    "moonshot": "https://api.moonshot.cn/v1",
}

MODEL_PROVIDER_REGISTRY = {
    "glm": "zhipu",
    "deepseek": "deepseek",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "kimi": "moonshot",
}


def _detect_provider(model_name: str) -> str:
    model = model_name.lower()
    for prefix, provider in MODEL_PROVIDER_REGISTRY.items():
        if model.startswith(prefix):
            return provider
    return "zhipu"


def create_langchain_llm(
    db,
    user_id: int,
    model_name: str,
    temperature: float = 0.7,
    api_url: Optional[str] = None,
) -> ChatOpenAI:
    """创建支持用户隔离配置的 OpenAI-compatible LangChain LLM。"""
    from service.llm import offline_demo
    if model_name == offline_demo.MODEL_NAME and offline_demo.enabled():
        return offline_demo.langchain_model()
    api_config = get_api_config(db, user_id, model_name)
    if not api_config:
        raise ValueError(f"请先在【模型配置】中配置 {model_name} 的 API Key")

    provider = _detect_provider(model_name)
    base_url = api_url or api_config.get("api_url") or PROVIDER_BASE_URLS.get(provider)
    # model_catalog.CHAT_MODELS[...]["api_url"]（api_config 的来源）存的是完整的
    # chat/completions 端点 URL——GLMClient/OpenAICompatibleClient 直接拿去用没问题，
    # 但 LangChain 的 ChatOpenAI(base_url=...) 认的是"API 根路径"，自己会再拼一次
    # /chat/completions，不去掉这个后缀就会拼成 .../chat/completions/chat/completions，
    # 请求 404——所有绑了工具/技能、走 ReAct 引擎的智谱模型 Agent 之前都会撞上这个。
    if base_url:
        stripped = base_url.rstrip("/")
        if stripped.endswith("/chat/completions"):
            base_url = stripped[: -len("/chat/completions")]
    llm_kwargs = {
        "model": model_name,
        "api_key": api_config["api_key"],
        "temperature": temperature,
        "timeout": 60,
        "max_retries": 2,
        "stream_usage": True,
    }
    if base_url:
        llm_kwargs["base_url"] = base_url
    logger.info(f"创建 LangChain LLM: model={model_name}, provider={provider}, base_url={base_url or 'default'}")
    return ChatOpenAI(**llm_kwargs)


def get_provider_info(model_name: str) -> Dict[str, str]:
    provider = _detect_provider(model_name)
    return {
        "model_name": model_name,
        "provider": provider,
        "base_url": PROVIDER_BASE_URLS.get(provider, "default"),
    }
