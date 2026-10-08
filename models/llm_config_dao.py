from types import SimpleNamespace
from typing import Dict, List, Optional

from models.init_db import EnterpriseLlmConnection, LLMConfig


def _virtual_enterprise_configs(ciphers: Dict[str, str], own_active: set) -> list:
    """企业统一连接带来的“虚拟配置”：服务商连了，它名下所有目录里的模型都算可用（个人已有的除外）。
    只有 model_name / api_key（密文）/ api_url / is_active，足够给“挑一个可用模型”的调用方用。"""
    from service.llm.model_catalog import CHAT_MODELS, EMBEDDING_MODELS, default_api_url, provider
    rows = []
    for name in list(CHAT_MODELS) + list(EMBEDDING_MODELS):
        owner = provider(name)
        if owner == "local" or owner not in ciphers or name in own_active:
            continue
        rows.append(SimpleNamespace(id=None, user_id=None, model_name=name, api_key=ciphers[owner],
                                    api_url=default_api_url(name), is_active=1, enterprise=True))
    return rows


def active_enterprise_ciphers(db) -> Dict[str, str]:
    rows = db.query(EnterpriseLlmConnection).filter(EnterpriseLlmConnection.is_active == 1).all()
    return {r.provider: r.api_key for r in rows}


def get_own_config_by_user_and_model(db, user_id: int, model_name: str) -> Optional[LLMConfig]:
    """用户自己保存的某个模型配置（管理个人配置用：保存 / 删除 / 测试都只动这一条）。"""
    return db.query(LLMConfig).filter(LLMConfig.user_id == user_id, LLMConfig.model_name == model_name).first()


def list_own_configs_by_user(db, user_id: int) -> List[LLMConfig]:
    """用户自己保存的全部模型配置（“连接 AI 服务”页展示用）。"""
    return db.query(LLMConfig).filter(LLMConfig.user_id == user_id).all()


def get_config_by_user_and_model(db, user_id: int, model_name: str):
    """调用某个模型时用哪份配置：个人密钥（启用的）优先，其次企业统一连接，最后是个人的停用配置（让调用方判断）。"""
    own = get_own_config_by_user_and_model(db, user_id, model_name)
    if own is not None and own.is_active:
        return own
    from service.llm.model_catalog import normalize_model_name
    virtual = _virtual_enterprise_configs(active_enterprise_ciphers(db), set())
    for config in virtual:
        if config.model_name == normalize_model_name(model_name):
            return config
    return own


def list_configs_by_user(db, user_id: int) -> list:
    """用户能用的全部模型：个人配置 + 企业统一连接带来的模型。"""
    own = list_own_configs_by_user(db, user_id)
    own_active = {c.model_name for c in own if c.is_active}
    return list(own) + _virtual_enterprise_configs(active_enterprise_ciphers(db), own_active)


def create_config(db,user_id:int,model_name:str,api_key: str, api_url: str = None) -> LLMConfig:
    """创建模型配置"""
    config = LLMConfig(
        user_id = user_id,
        model_name = model_name,
        api_key = api_key,
        api_url = api_url,
        is_active =1
    )
    db.add(config)
    db.flush()
    return config

def update_config(db, config: LLMConfig, api_key: str = None, api_url: str = None, is_active: int = None) -> LLMConfig:
    """更新模型配置"""
    if api_key is not None:
        config.api_key = api_key
    if api_url is not None:
        config.api_url = api_url
    if is_active is not None:
        config.is_active = is_active
    db.flush()
    return config

def delete_config(db, config: LLMConfig) -> None:
    """删除模型配置"""
    db.delete(config)
    db.flush()
