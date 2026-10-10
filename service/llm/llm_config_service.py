from contextvars import ContextVar, Token
from typing import Dict,Any,List,Optional
from models.llm_config_dao import (
    create_config, delete_config, get_config_by_user_and_model, get_own_config_by_user_and_model,
    list_configs_by_user, list_own_configs_by_user, update_config,
)
from models.llm_config_async_dao import (
    create_config_async,
    delete_config_async,
    get_config_by_user_and_model_async,
    get_own_config_by_user_and_model_async,
    list_configs_by_user_async,
    list_own_configs_by_user_async,
    update_config_async,
)
from utils.crypto import encrypt, decrypt
from utils.cache import config_cache
from service.llm.model_catalog import (
    default_api_url, model_type, normalize_model_name, provider, quick_defaults,
)


def invalidate_user_config_cache(user_id: int, model_name: str = None):
    if model_name:
        config_cache.invalidate(("llm_api_config", user_id, model_name))
        config_cache.invalidate(("llm_api_key", user_id, model_name))
    config_cache.invalidate(prefix=("llm_config_list", user_id))
    config_cache.invalidate(("embedding_api_config", user_id))


def _reveal(payload):
    """缓存里的配置只带密文（和数据库里一样）；调用方要用时才在内存里解密。Redis 即使被读到，也拿不到明文密钥。"""
    if not payload:
        return payload
    revealed = dict(payload)
    if revealed.get("api_key"):
        revealed["api_key"] = decrypt(revealed["api_key"])
    return revealed


def list_configs(db,user)->list:
    """获取用户的模型配置列表（api_key 脱敏显示）"""
    def load():
        configs = list_own_configs_by_user(db, user.id)
        return [
            {
                "id": c.id,
                "model_name": c.model_name,
                "api_key": c.api_key[:10] + "****" if c.api_key else None,
                "api_url": default_api_url(c.model_name),
                "provider": provider(c.model_name),
                "kind": model_type(c.model_name),
                "is_active": c.is_active
            }
            for c in configs
        ]
    return config_cache.get_or_set(("llm_config_list", user.id), load)


async def async_list_configs(db, user) -> list:
    """异步获取用户的模型配置列表（api_key 脱敏显示）。"""

    cached = config_cache.get(("llm_config_list", user.id))
    if cached is not None:
        return cached
    configs = await list_own_configs_by_user_async(db, user.id)
    rows = [
        {
            "id": c.id,
            "model_name": c.model_name,
            "api_key": c.api_key[:10] + "****" if c.api_key else None,
            "api_url": default_api_url(c.model_name),
            "provider": provider(c.model_name),
            "kind": model_type(c.model_name),
            "is_active": c.is_active
        }
        for c in configs
    ]
    config_cache.set(("llm_config_list", user.id), rows)
    return rows

def save_config(db, user, model_name: str, api_key: str, api_url: str = None) -> Dict[str, Any]:
    """新增或更新模型配置（URL 由后端按模型名自动适配）"""
    model_name = normalize_model_name(model_name)
    api_url = default_api_url(model_name)
    encrypted_key = encrypt(api_key)
    existing = get_own_config_by_user_and_model(db, user.id, model_name)
    if existing:
        update_config(
            db,existing,api_key = encrypted_key,api_url=api_url,
        )
        db.commit()
        invalidate_user_config_cache(user.id, model_name)
        return {"message": "更新成功", "model_name": model_name}
    create_config(db, user.id, model_name, encrypted_key, api_url)
    db.commit()
    invalidate_user_config_cache(user.id, model_name)
    return  {"message": "配置成功", "model_name": model_name}


async def async_save_config(db, user, model_name: str, api_key: str, api_url: str = None) -> Dict[str, Any]:
    """异步新增或更新模型配置（URL 由后端按模型名自动适配）。"""

    model_name = normalize_model_name(model_name)
    api_url = default_api_url(model_name)
    encrypted_key = encrypt(api_key)
    existing = await get_own_config_by_user_and_model_async(db, user.id, model_name)
    if existing:
        await update_config_async(db, existing, api_key=encrypted_key, api_url=api_url)
        await db.commit()
        invalidate_user_config_cache(user.id, model_name)
        return {"message": "更新成功", "model_name": model_name}
    await create_config_async(db, user.id, model_name, encrypted_key, api_url)
    await db.commit()
    invalidate_user_config_cache(user.id, model_name)
    return {"message": "配置成功", "model_name": model_name}


async def async_quick_connect(
    db, user, provider_key: str, api_key: str,
    capabilities: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """「一次连接，多项能力」：选平台 + 粘一次 Key，自动配好聊天 + 资料读取两条配置。

    - capabilities 缺省 = 平台支持的全部；只传 ["chat"] 则不配资料读取。
    - 两条配置在一个事务里落库（要么都成、要么都不动），再逐条连通性测试。
    - 返回 {provider, saved:[model_name...], skipped:[...], results:{model_name: 测试结果}}。
    """
    defaults = quick_defaults(provider_key)
    want = set(capabilities or ["chat", "embedding"])
    targets: List[str] = []
    skipped: List[Dict[str, str]] = []
    for kind in ("chat", "embedding"):
        if kind not in want:
            continue
        model_name = defaults.get(kind)
        if model_name:
            targets.append(normalize_model_name(model_name))
        else:
            skipped.append({"capability": kind, "reason": "该平台暂不支持"})

    if not targets:
        return {"provider": provider_key, "saved": [], "skipped": skipped, "results": {}}

    encrypted_key = encrypt(api_key)
    for model_name in targets:
        api_url = default_api_url(model_name)
        existing = await get_own_config_by_user_and_model_async(db, user.id, model_name)
        if existing:
            await update_config_async(db, existing, api_key=encrypted_key, api_url=api_url)
        else:
            await create_config_async(db, user.id, model_name, encrypted_key, api_url)
    await db.commit()
    for model_name in targets:
        invalidate_user_config_cache(user.id, model_name)

    results: Dict[str, Any] = {}
    for model_name in targets:
        try:
            results[model_name] = await async_test_config(db, user, model_name)
        except Exception as e:  # noqa: BLE001 —— 测试失败不回滚已保存的配置
            results[model_name] = {"ok": False, "model_name": model_name,
                                   "message": "连接测试失败", "error": str(e)[:300]}
    return {"provider": provider_key, "saved": targets, "skipped": skipped, "results": results}


def delete_config_by_model(db,user,model_name:str)->Dict[str, Any]:
    """删除模型配置"""
    config = get_own_config_by_user_and_model(db, user.id, model_name)
    if not config:
        return {"message": "配置不存在"}
    delete_config(db, config)
    db.commit()
    invalidate_user_config_cache(user.id, model_name)
    return {"message": "删除成功", "model_name": model_name}


async def async_delete_config_by_model(db, user, model_name: str) -> Dict[str, Any]:
    """异步删除模型配置。"""

    model_name = normalize_model_name(model_name)
    config = await get_own_config_by_user_and_model_async(db, user.id, model_name)
    if not config:
        return {"message": "配置不存在"}
    await delete_config_async(db, config)
    await db.commit()
    invalidate_user_config_cache(user.id, model_name)
    return {"message": "删除成功", "model_name": model_name}


def _own_api_key(db,user_id:int,model_name:str)->str:
    """获取解密后的 API Key（供 LLM Factory 调用）"""
    def load():
        config = get_config_by_user_and_model(db, user_id, model_name)
        if not config or not config.is_active:
            return None
        return config.api_key          # 密文；见 _reveal
    cipher = config_cache.get_or_set(("llm_api_key", user_id, model_name), load)
    return decrypt(cipher) if cipher else None

def _own_api_config(db, user_id: int, model_name: str):
    """获取当前用户某个模型的完整调用配置。"""
    def load():
        config = get_config_by_user_and_model(db, user_id, model_name)
        if not config or not config.is_active:
            return None
        return {
            "model_name": config.model_name,
            "api_key": config.api_key,
            "api_url": default_api_url(config.model_name),
        }
    return _reveal(config_cache.get_or_set(("llm_api_config", user_id, model_name), load))


async def _own_api_config_async(db, user_id: int, model_name: str):
    """异步获取当前用户某个模型的完整调用配置。"""

    model_name = normalize_model_name(model_name)
    cached = config_cache.get(("llm_api_config", user_id, model_name))
    if cached is not None:
        return _reveal(cached)
    config = await get_config_by_user_and_model_async(db, user_id, model_name)
    if not config or not config.is_active:
        return None
    payload = {
        "model_name": config.model_name,
        "api_key": config.api_key,
        "api_url": default_api_url(config.model_name),
    }
    config_cache.set(("llm_api_config", user_id, model_name), payload)
    return _reveal(payload)


async def _own_api_key_async(db, user_id: int, model_name: str) -> str:
    """`get_api_key` 的 async 版：命中缓存直接返回，否则走 async DAO 再回填。"""
    model_name = normalize_model_name(model_name)
    cached = config_cache.get(("llm_api_key", user_id, model_name))
    if cached is not None:
        return decrypt(cached)
    config = await get_config_by_user_and_model_async(db, user_id, model_name)
    cipher = config.api_key if (config and config.is_active) else None
    config_cache.set(("llm_api_key", user_id, model_name), cipher)
    return decrypt(cipher) if cipher else None


async def async_get_first_embedding_config(db, user_id: int):
    """`get_first_embedding_config` 的 async 版（缓存键一致）。"""
    cached = config_cache.get(("embedding_api_config", user_id))
    if cached is not None:
        return _reveal(cached)
    priority = [
        "embedding-3", "embedding-2",
        "text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002",
        "BAAI/bge-small-zh-v1.5", "BAAI/bge-base-zh-v1.5", "BAAI/bge-large-zh-v1.5",
    ]
    configs = await list_configs_by_user_async(db, user_id)
    active = {c.model_name: c for c in configs if c.is_active}
    payload = None
    for model_name in priority:
        config = active.get(model_name)
        if config:
            payload = {
                "model_name": config.model_name,
                "api_key": config.api_key,
                "api_url": default_api_url(config.model_name),
            }
            break
    if payload is None:
        glm_config = active.get("glm-4")
        if glm_config:
            payload = {
                "model_name": "embedding-3",
                "api_key": glm_config.api_key,
                "api_url": default_api_url("embedding-3"),
            }
    config_cache.set(("embedding_api_config", user_id), payload)
    return _reveal(payload)


def get_first_embedding_config(db, user_id: int):
    """按优先级查找当前用户可用的向量模型配置。"""
    def load():
        priority = [
            "embedding-3",
            "embedding-2",
            "text-embedding-3-small",
            "text-embedding-3-large",
            "text-embedding-ada-002",
            "BAAI/bge-small-zh-v1.5",
            "BAAI/bge-base-zh-v1.5",
            "BAAI/bge-large-zh-v1.5",
        ]
        configs = list_configs_by_user(db, user_id)
        active = {c.model_name: c for c in configs if c.is_active}
        for model_name in priority:
            config = active.get(model_name)
            if config:
                return {
                    "model_name": config.model_name,
                    "api_key": config.api_key,
                    "api_url": default_api_url(config.model_name),
                }
        glm_config = active.get("glm-4")
        if glm_config:
            return {
                "model_name": "embedding-3",
                "api_key": glm_config.api_key,
                "api_url": default_api_url("embedding-3"),
            }
        return None
    return _reveal(config_cache.get_or_set(("embedding_api_config", user_id), load))


def test_config(db, user, model_name: str) -> Dict[str, Any]:
    """测试当前用户已保存的模型配置是否可用。"""
    import time

    model_name = normalize_model_name(model_name)
    api_config = get_api_config(db, user.id, model_name)
    if not api_config:
        return {
            "ok": False,
            "model_name": model_name,
            "message": "配置不存在或已停用",
        }

    started = time.time()
    kind = model_type(model_name)
    try:
        if kind == "embedding":
            from service.rag.embedding.factory import EmbeddingFactory

            client = EmbeddingFactory.create(
                model_name=api_config["model_name"],
                api_key=api_config["api_key"],
                api_url=api_config.get("api_url"),
            )
            vector = client.embed_query("连接测试")
            elapsed_ms = int((time.time() - started) * 1000)
            return {
                "ok": True,
                "model_name": model_name,
                "kind": "embedding",
                "message": "向量模型连接正常",
                "elapsed_ms": elapsed_ms,
                "dimension": len(vector),
            }

        from service.llm.factory import LLMFactory

        client = LLMFactory.create(
            model_name=api_config["model_name"],
            api_key=api_config["api_key"],
            api_url=api_config.get("api_url"),
        )
        answer = client.chat([
            {"role": "system", "content": "你只需要用中文简短回复。"},
            {"role": "user", "content": "请回复：连接正常"},
        ], temperature=0)
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "ok": True,
            "model_name": model_name,
            "kind": "chat",
            "message": "聊天模型连接正常",
            "elapsed_ms": elapsed_ms,
            "preview": (answer or "")[:100],
        }
    except Exception as e:
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "ok": False,
            "model_name": model_name,
            "kind": kind,
            "message": "连接测试失败",
            "elapsed_ms": elapsed_ms,
            "error": str(e)[:500],
        }


async def async_test_config(db, user, model_name: str) -> Dict[str, Any]:
    """异步测试当前用户已保存的模型配置是否可用。"""
    model_name = normalize_model_name(model_name)
    api_config = await async_get_api_config(db, user.id, model_name)
    if not api_config:
        return {
            "ok": False,
            "model_name": model_name,
            "message": "配置不存在或已停用",
        }

    return await probe_model(model_name, api_config)


async def probe_model(model_name: str, api_config: Dict[str, Any]) -> Dict[str, Any]:
    """用给定的调用配置真的调一次模型，返回连通性测试结果（个人配置和企业统一连接共用）。"""
    import time

    model_name = normalize_model_name(model_name)
    started = time.time()
    kind = model_type(model_name)
    try:
        if kind == "embedding":
            from service.rag.embedding.factory import EmbeddingFactory

            client = EmbeddingFactory.create(
                model_name=api_config["model_name"],
                api_key=api_config["api_key"],
                api_url=api_config.get("api_url"),
            )
            vector = await client.aembed_query("连接测试")
            elapsed_ms = int((time.time() - started) * 1000)
            return {
                "ok": True,
                "model_name": model_name,
                "kind": "embedding",
                "message": "向量模型连接正常",
                "elapsed_ms": elapsed_ms,
                "dimension": len(vector),
            }

        from service.llm.factory import LLMFactory

        client = LLMFactory.create(
            model_name=api_config["model_name"],
            api_key=api_config["api_key"],
            api_url=api_config.get("api_url"),
        )
        answer = await client.achat([
            {"role": "system", "content": "你只需要用中文简短回复。"},
            {"role": "user", "content": "请回复：连接正常"},
        ], temperature=0)
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "ok": True,
            "model_name": model_name,
            "kind": "chat",
            "message": "聊天模型连接正常",
            "elapsed_ms": elapsed_ms,
            "preview": (answer or "")[:100],
        }
    except Exception as e:
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "ok": False,
            "model_name": model_name,
            "kind": kind,
            "message": "连接测试失败",
            "elapsed_ms": elapsed_ms,
            "error": str(e)[:500],
        }




# ------------------------------------------------------------------ 企业助手用企业的模型连接

_enterprise_owners: ContextVar[tuple] = ContextVar("llm_enterprise_credentials", default=())


def is_enterprise_agent(agent) -> bool:
    return getattr(agent, "agent_type", None) in ("central", "department")


def use_agent_credentials(agent, enterprise_admins=()) -> Token:
    """企业助手（中央 / 部门）运行期间：调用人自己没配这个模型时，用企业的模型连接——
    依次找助手创建人、企业所有者 / 管理员（enterprise_admins，调用方按顺序查好），用第一个配了这个模型的人的连接。
    企业助手是企业提供给员工用的，不能要求每个员工自带 Key；Key 只在服务端用，员工看不到。
    员工自己配了同一个模型时仍优先用员工自己的。个人助手不受影响。用完必须 reset_agent_credentials(token)。"""
    owners = ()
    if is_enterprise_agent(agent):
        owners = tuple(dict.fromkeys([agent.user_id, *enterprise_admins]))
    return _enterprise_owners.set(owners)


def reset_agent_credentials(token: Token) -> None:
    try:
        _enterprise_owners.reset(token)
    except ValueError:          # 在别的上下文里收尾（例如流式响应被丢弃后回收）：这个上下文本来就要结束了
        pass


def _fallback_owners(user_id: int) -> tuple:
    return tuple(o for o in _enterprise_owners.get() if o and o != user_id)


def get_api_key(db, user_id: int, model_name: str) -> str:
    """获取解密后的 API Key（供 LLM Factory 调用）。企业助手运行中、本人没配时用企业的连接（见 use_agent_credentials）。"""
    key = _own_api_key(db, user_id, model_name)
    for owner in (() if key else _fallback_owners(user_id)):
        key = _own_api_key(db, owner, model_name)
        if key:
            break
    return key


def get_api_config(db, user_id: int, model_name: str):
    """获取某个模型的完整调用配置：本人的；企业助手运行中、本人没配时用企业的连接。"""
    config = _own_api_config(db, user_id, model_name)
    for owner in (() if config else _fallback_owners(user_id)):
        config = _own_api_config(db, owner, model_name)
        if config:
            break
    return config


async def async_get_api_config(db, user_id: int, model_name: str):
    config = await _own_api_config_async(db, user_id, model_name)
    for owner in (() if config else _fallback_owners(user_id)):
        config = await _own_api_config_async(db, owner, model_name)
        if config:
            break
    return config


async def async_get_api_key(db, user_id: int, model_name: str) -> str:
    key = await _own_api_key_async(db, user_id, model_name)
    for owner in (() if key else _fallback_owners(user_id)):
        key = await _own_api_key_async(db, owner, model_name)
        if key:
            break
    return key


async def enterprise_admin_ids_async(db, agent) -> list:
    """企业助手所属企业的所有者和管理员（所有者在前）。平台只服务一家企业，助手没写企业时用这家企业。"""
    if not is_enterprise_agent(agent):
        return []
    from sqlalchemy import text
    org = getattr(agent, "organization_id", None) or (await db.execute(
        text("SELECT id FROM organizations ORDER BY id LIMIT 1"))).scalar()
    if org is None:
        return []
    rows = (await db.execute(text(
        "SELECT om.user_id FROM organization_members om JOIN enterprise_role r ON r.id = om.role_id "
        "WHERE om.organization_id = :o AND om.status = 'active' AND r.scope = 'organization' AND r.code IN ('owner', 'admin') "
        "ORDER BY r.code = 'owner' DESC, om.user_id"), {"o": org})).all()
    return [row[0] for row in rows] + await _platform_admin_ids_async(db)


async def _platform_admin_ids_async(db) -> list:
    """平台管理员（部署、配置系统的人；模型连接通常由他们统一配置），排在企业所有者 / 管理员之后。"""
    import os
    from sqlalchemy import select
    from models.init_db import Role, User, association_table
    from service.admin_service import ADMIN_ROLE_NAMES
    names = [n.strip() for n in os.getenv("ADMIN_USER_NAMES", "admin").split(",") if n.strip()]
    by_role = (await db.execute(select(association_table.c.user_id).join(Role, Role.id == association_table.c.role_id)
                                .where(Role.role_name.in_(ADMIN_ROLE_NAMES)))).scalars().all()
    by_name = (await db.execute(select(User.id).where(User.name.in_(names)))).scalars().all()
    return sorted({*by_role, *by_name})
