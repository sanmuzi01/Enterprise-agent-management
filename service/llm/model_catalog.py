from typing import Dict, List

PROVIDER_DEFAULT_API_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "moonshot": "https://api.moonshot.cn/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "perplexity": "https://api.perplexity.ai",
    "local": "",
}

PROVIDER_PREFIX_RULES = (
    (("deepseek",), "deepseek"),
    (("gpt", "o1", "o3", "o4", "text-embedding"), "openai"),
    (("kimi",), "moonshot"),
    (("qwen",), "qwen"),
    (("sonar",), "perplexity"),
    (("baai/",), "local"),
)


CHAT_MODELS: Dict[str, Dict[str, str]] = {
    "glm-4": {"provider": "zhipu", "api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions"},
    "glm-4-flash": {"provider": "zhipu", "api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions"},
    "glm-4-plus": {"provider": "zhipu", "api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions"},
    "glm-4v": {"provider": "zhipu", "api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions"},
    "deepseek-chat": {"provider": "deepseek", "api_url": "https://api.deepseek.com/v1"},
    "deepseek-reasoner": {"provider": "deepseek", "api_url": "https://api.deepseek.com/v1"},
    "deepseek-coder": {"provider": "deepseek", "api_url": "https://api.deepseek.com/v1"},
    "gpt-4o": {"provider": "openai", "api_url": "https://api.openai.com/v1"},
    "gpt-4o-mini": {"provider": "openai", "api_url": "https://api.openai.com/v1"},
    "o3-mini": {"provider": "openai", "api_url": "https://api.openai.com/v1"},
    "o4-mini": {"provider": "openai", "api_url": "https://api.openai.com/v1"},
    "kimi-k2-0711-preview": {"provider": "moonshot", "api_url": "https://api.moonshot.cn/v1"},
    "kimi-latest": {"provider": "moonshot", "api_url": "https://api.moonshot.cn/v1"},
    "qwen-plus": {"provider": "qwen", "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    "qwen-turbo": {"provider": "qwen", "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    "qwen-max": {"provider": "qwen", "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    "qwen-long": {"provider": "qwen", "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    # 搜索式模型：本身就联网，用于组件的「联网检索」数据源
    "sonar": {"provider": "perplexity", "api_url": "https://api.perplexity.ai"},
    "sonar-pro": {"provider": "perplexity", "api_url": "https://api.perplexity.ai"},
    "gpt-4o-search-preview": {"provider": "openai", "api_url": "https://api.openai.com/v1"},
    "gpt-4o-mini-search-preview": {"provider": "openai", "api_url": "https://api.openai.com/v1"},
}

# 离线演示模型：只在 OFFLINE_DEMO_MODEL=1 且非生产环境时出现（见 service/llm/offline_demo.py）
from service.llm import offline_demo  # noqa: E402

if offline_demo.enabled():
    CHAT_MODELS[offline_demo.MODEL_NAME] = {"provider": "local", "api_url": ""}

EMBEDDING_MODELS: Dict[str, Dict[str, str]] = {
    "embedding-3": {"provider": "zhipu", "api_url": "https://open.bigmodel.cn/api/paas/v4/embeddings"},
    "embedding-2": {"provider": "zhipu", "api_url": "https://open.bigmodel.cn/api/paas/v4/embeddings"},
    "text-embedding-3-small": {"provider": "openai", "api_url": "https://api.openai.com/v1/embeddings"},
    "text-embedding-3-large": {"provider": "openai", "api_url": "https://api.openai.com/v1/embeddings"},
    "text-embedding-ada-002": {"provider": "openai", "api_url": "https://api.openai.com/v1/embeddings"},
    "BAAI/bge-small-zh-v1.5": {"provider": "local", "api_url": ""},
    "BAAI/bge-base-zh-v1.5": {"provider": "local", "api_url": ""},
    "BAAI/bge-large-zh-v1.5": {"provider": "local", "api_url": ""},
}


# 「一次连接，多项能力」：每个平台一套推荐默认（聊天 + 资料读取），
# 前端普通模式和后端 quick_connect 共用这份口径。embedding 为 None = 该平台只做聊天。
PROVIDER_QUICK_DEFAULTS: Dict[str, Dict[str, str]] = {
    "zhipu": {"chat": "glm-4", "embedding": "embedding-3"},
    "openai": {"chat": "gpt-4o-mini", "embedding": "text-embedding-3-small"},
    "deepseek": {"chat": "deepseek-chat", "embedding": None},
    "moonshot": {"chat": "kimi-latest", "embedding": None},
    "qwen": {"chat": "qwen-plus", "embedding": None},
    "perplexity": {"chat": "sonar", "embedding": None},
}


def quick_defaults(provider_key: str) -> Dict[str, str]:
    """平台推荐默认模型 {chat, embedding}；未知平台回退智谱。"""
    return PROVIDER_QUICK_DEFAULTS.get((provider_key or "").strip().lower(),
                                       PROVIDER_QUICK_DEFAULTS["zhipu"])


def normalize_model_name(model_name: str) -> str:
    value = (model_name or "").strip()
    for known in list(CHAT_MODELS.keys()) + list(EMBEDDING_MODELS.keys()):
        if known.lower() == value.lower():
            return known
    return value


def model_type(model_name: str) -> str:
    name = normalize_model_name(model_name)
    return "embedding" if name in EMBEDDING_MODELS else "chat"


def provider(model_name: str) -> str:
    name = normalize_model_name(model_name)
    meta = EMBEDDING_MODELS.get(name) or CHAT_MODELS.get(name) or {}
    if meta.get("provider"):
        return meta["provider"]
    lowered = name.lower()
    for prefixes, provider_name in PROVIDER_PREFIX_RULES:
        if lowered.startswith(prefixes):
            return provider_name
    return "zhipu"


def default_api_url(model_name: str) -> str:
    name = normalize_model_name(model_name)
    meta = EMBEDDING_MODELS.get(name) or CHAT_MODELS.get(name)
    if meta is not None:
        return meta["api_url"]
    return PROVIDER_DEFAULT_API_URLS.get(provider(name), PROVIDER_DEFAULT_API_URLS["zhipu"])


def supported_models() -> Dict[str, List[Dict[str, str]]]:
    def rows(source: Dict[str, Dict[str, str]], kind: str):
        return [
            {"model_name": name, "provider": meta["provider"], "kind": kind}
            for name, meta in source.items()
        ]

    return {
        "chat": rows(CHAT_MODELS, "chat"),
        "embedding": rows(EMBEDDING_MODELS, "embedding"),
    }
