"""企业统一的模型连接（管理后台“模型连接”页）。

管理员按模型服务商配置一次 API Key，全公司共用；员工不用再各自去“连接 AI 服务”里填。
- 密钥加密保存，只写不读：接口只返回末四位提示；
- 用户自己填了个人密钥的模型，仍然优先用个人的；
- 一个服务商的 Key 对它名下目录里的所有模型（聊天 + 资料读取）都生效。"""
from typing import Any, Dict, List

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from models.init_db import EnterpriseLlmConnection
from service import audit_service
from service.exceptions import InvalidInput, NotFound
from service.llm import llm_config_service
from service.llm.model_catalog import (
    CHAT_MODELS, EMBEDDING_MODELS, PROVIDER_QUICK_DEFAULTS, default_api_url, provider as provider_of,
)
from utils.cache import config_cache
from utils.crypto import decrypt, encrypt

PROVIDER_LABELS = {
    "zhipu": "智谱 AI", "openai": "OpenAI", "deepseek": "DeepSeek", "moonshot": "月之暗面 Kimi",
    "qwen": "阿里通义千问", "perplexity": "Perplexity",
}
MIN_KEY_LENGTH = 8


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider)


def invalidate_all_llm_caches() -> None:
    """企业连接一变，所有人的“该用哪把密钥”缓存都要作废。"""
    for prefix in ("llm_api_config", "llm_api_key", "embedding_api_config", "llm_config_list"):
        config_cache.invalidate(prefix=(prefix,))


def _key_hint(cipher: str) -> str:
    try:
        plain = decrypt(cipher)
    except Exception:  # noqa: BLE001
        return "****"
    return "****" + plain[-4:] if len(plain) > 8 else "****"


def _models_of(provider: str) -> Dict[str, List[str]]:
    return {
        "chat": [name for name in CHAT_MODELS if provider_of(name) == provider],
        "embedding": [name for name in EMBEDDING_MODELS if provider_of(name) == provider],
    }


async def list_connections(db) -> List[Dict[str, Any]]:
    rows = {r.provider: r for r in (await db.execute(select(EnterpriseLlmConnection))).scalars().all()}
    items = []
    for provider, defaults in PROVIDER_QUICK_DEFAULTS.items():
        row = rows.get(provider)
        models = _models_of(provider)
        items.append({
            "provider": provider,
            "label": provider_label(provider),
            "connected": row is not None,
            "is_active": bool(row.is_active) if row else False,
            "key_hint": _key_hint(row.api_key) if row else None,
            "updated_at": row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if row and row.updated_at else None,
            "chat_models": models["chat"],
            "embedding_models": models["embedding"],
            "default_chat": defaults.get("chat"),
            "default_embedding": defaults.get("embedding"),
        })
    return items


async def connected_providers(db) -> set:
    rows = (await db.execute(select(EnterpriseLlmConnection.provider).where(EnterpriseLlmConnection.is_active == 1))).all()
    return {r[0] for r in rows}


def _require_provider(provider: str) -> str:
    provider = (provider or "").strip().lower()
    if provider not in PROVIDER_QUICK_DEFAULTS:
        raise InvalidInput(f"不支持的模型服务商：{provider or '空'}")
    return provider


async def connect(db, operator_id: int, provider: str, api_key: str) -> Dict[str, Any]:
    provider = _require_provider(provider)
    key = (api_key or "").strip()
    if len(key) < MIN_KEY_LENGTH or any(ch.isspace() for ch in key):
        raise InvalidInput("API Key 看起来不对：不能含空格，长度也太短了")
    existing = (await db.execute(select(EnterpriseLlmConnection).where(EnterpriseLlmConnection.provider == provider))).scalars().first()
    if existing:
        existing.api_key = encrypt(key)
        existing.is_active = 1
        existing.updated_by = operator_id
    else:
        db.add(EnterpriseLlmConnection(provider=provider, api_key=encrypt(key), is_active=1, updated_by=operator_id))
    await db.commit()
    invalidate_all_llm_caches()
    await audit_service.record_async(operator_id, "llm.enterprise_connected", resource_type="llm_provider", resource_id=None,
                                     detail={"provider": provider, "replaced": bool(existing)})   # 不记录密钥
    return next(i for i in await list_connections(db) if i["provider"] == provider)


async def set_active(db, operator_id: int, provider: str, active: bool) -> Dict[str, Any]:
    provider = _require_provider(provider)
    row = (await db.execute(select(EnterpriseLlmConnection).where(EnterpriseLlmConnection.provider == provider))).scalars().first()
    if row is None:
        raise NotFound("这个服务商还没有连接")
    row.is_active = 1 if active else 0
    row.updated_by = operator_id
    await db.commit()
    invalidate_all_llm_caches()
    await audit_service.record_async(operator_id, "llm.enterprise_toggled", resource_type="llm_provider", resource_id=None,
                                     detail={"provider": provider, "active": bool(active)})
    return next(i for i in await list_connections(db) if i["provider"] == provider)


async def remove(db, operator_id: int, provider: str) -> Dict[str, Any]:
    provider = _require_provider(provider)
    result = await db.execute(sa_delete(EnterpriseLlmConnection).where(EnterpriseLlmConnection.provider == provider))
    if not result.rowcount:
        raise NotFound("这个服务商还没有连接")
    await db.commit()
    invalidate_all_llm_caches()
    await audit_service.record_async(operator_id, "llm.enterprise_removed", resource_type="llm_provider", resource_id=None,
                                     detail={"provider": provider})
    return {"provider": provider, "removed": True}


def friendly_error(raw: str) -> str:
    """把服务商返回的技术性报错翻成管理员看得懂的话（不带地址、不带代码路径）。"""
    text = raw or ""
    lowered = text.lower()
    if "401" in text or "403" in text or "unauthorized" in lowered or "invalid api key" in lowered or "incorrect api key" in lowered:
        return "服务商拒绝了这个密钥：请检查密钥是否填对、是否已过期或欠费"
    if "429" in text or "rate limit" in lowered or "quota" in lowered:
        return "服务商提示请求太频繁或额度已用完，请稍后再试或检查账户额度"
    if "timeout" in lowered or "timed out" in lowered or "connect" in lowered or "name or service" in lowered or "resolve" in lowered:
        return "连不上服务商：网络不通或响应超时，请检查服务器能不能访问外网"
    if "不支持的嵌入模型" in text:
        return "平台还没有接入这个资料读取模型的客户端，请联系维护平台的工程师"
    return "连接失败：" + text.replace("\n", " ")[:80]


async def test_connection(db, provider: str) -> Dict[str, Any]:
    """用企业统一的密钥真的调一次：聊天模型一次；服务商有资料读取模型的话再测一次。"""
    provider = _require_provider(provider)
    row = (await db.execute(select(EnterpriseLlmConnection).where(EnterpriseLlmConnection.provider == provider))).scalars().first()
    if row is None:
        raise NotFound("这个服务商还没有连接")
    key = decrypt(row.api_key)
    defaults = PROVIDER_QUICK_DEFAULTS[provider]
    results: Dict[str, Any] = {}
    for kind in ("chat", "embedding"):
        model_name = defaults.get(kind)
        if not model_name:
            continue
        config = {"model_name": model_name, "api_key": key, "api_url": default_api_url(model_name)}
        probe = await llm_config_service.probe_model(model_name, config)
        if not probe.get("ok"):
            probe = {**probe, "error": friendly_error(str(probe.get("error") or probe.get("message") or ""))}
        results[kind] = probe
    return {"provider": provider, "ok": all(r.get("ok") for r in results.values()), "results": results}
