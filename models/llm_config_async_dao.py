"""模型配置异步 DAO。"""

from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.init_db import EnterpriseLlmConnection, LLMConfig
from models.llm_config_dao import _virtual_enterprise_configs


async def active_enterprise_ciphers_async(db: AsyncSession) -> Dict[str, str]:
    result = await db.execute(select(EnterpriseLlmConnection).where(EnterpriseLlmConnection.is_active == 1))
    return {row.provider: row.api_key for row in result.scalars().all()}


async def get_own_config_by_user_and_model_async(
        db: AsyncSession, user_id: int, model_name: str,
) -> Optional[LLMConfig]:
    """用户自己保存的某个模型配置（管理个人配置用）。"""
    result = await db.execute(select(LLMConfig).where(LLMConfig.user_id == user_id, LLMConfig.model_name == model_name))
    return result.scalars().first()


async def list_own_configs_by_user_async(db: AsyncSession, user_id: int) -> List[LLMConfig]:
    result = await db.execute(select(LLMConfig).where(LLMConfig.user_id == user_id).order_by(LLMConfig.id.desc()))
    return list(result.scalars().all())


async def get_config_by_user_and_model_async(db: AsyncSession, user_id: int, model_name: str):
    """调用某个模型时用哪份配置：个人密钥（启用的）优先，其次企业统一连接，最后是个人的停用配置。"""
    own = await get_own_config_by_user_and_model_async(db, user_id, model_name)
    if own is not None and own.is_active:
        return own
    from service.llm.model_catalog import normalize_model_name
    wanted = normalize_model_name(model_name)
    for config in _virtual_enterprise_configs(await active_enterprise_ciphers_async(db), set()):
        if config.model_name == wanted:
            return config
    return own


async def list_configs_by_user_async(db: AsyncSession, user_id: int) -> list:
    """用户能用的全部模型：个人配置 + 企业统一连接带来的模型。"""
    own = await list_own_configs_by_user_async(db, user_id)
    own_active = {c.model_name for c in own if c.is_active}
    return list(own) + _virtual_enterprise_configs(await active_enterprise_ciphers_async(db), own_active)


async def create_config_async(
        db: AsyncSession,
        user_id: int,
        model_name: str,
        api_key: str,
        api_url: str = None,
) -> LLMConfig:
    config = LLMConfig(
        user_id=user_id,
        model_name=model_name,
        api_key=api_key,
        api_url=api_url,
        is_active=1,
    )
    db.add(config)
    await db.flush()
    return config


async def update_config_async(
        db: AsyncSession,
        config: LLMConfig,
        api_key: str = None,
        api_url: str = None,
        is_active: int = None,
) -> LLMConfig:
    if api_key is not None:
        config.api_key = api_key
    if api_url is not None:
        config.api_url = api_url
    if is_active is not None:
        config.is_active = is_active
    await db.flush()
    return config


async def delete_config_async(db: AsyncSession, config: LLMConfig) -> None:
    await db.delete(config)
    await db.flush()
