from pydantic  import Field,BaseModel

from fastapi import APIRouter, Depends
from service.exceptions import InvalidInput, NotFound
from typing import Optional
from models.init_db import User
from models.async_db import get_async_db
from service.dependencies import get_current_user_async
from service.llm import llm_config_service
from service.llm.model_catalog import supported_models as supported_model_catalog

router =APIRouter(prefix="/llm_config", tags=["模型配置"])

class ConfigSave(BaseModel):
    model_name: str = Field(min_length=1,max_length=100)
    api_key: str = Field(min_length=1, max_length=500)
    api_url: Optional[str] = Field(default=None, max_length=500)


class QuickConnect(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    api_key: str = Field(min_length=1, max_length=500)
    capabilities: Optional[list[str]] = Field(default=None)  # ["chat","embedding"]，缺省=全部

@router.get("/enterprise_connections", summary="管理员已为全公司统一连接的模型服务商（不含密钥）")
async def enterprise_connections(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    from service.llm import enterprise_llm_service
    items = await enterprise_llm_service.list_connections(async_db)
    return [{"provider": i["provider"], "label": i["label"]} for i in items if i["connected"] and i["is_active"]]


@router.get("/list",summary="获取用户的模型配置列表")
async def list_configs(
        async_db=Depends(get_async_db),
        current_user : User =Depends(get_current_user_async)
):
    return await llm_config_service.async_list_configs(async_db, current_user)


@router.get("/supported_models", summary="获取后端支持的聊天模型")
async def supported_models(current_user: User = Depends(get_current_user_async)):
    catalog = supported_model_catalog()
    return {
        "models": [item["model_name"] for item in catalog["chat"] + catalog["embedding"]],
        "chat": catalog["chat"],
        "embedding": catalog["embedding"],
    }
@router.post("",summary="新增或更新模型配置")
async def save_config(
        config :ConfigSave,
        async_db=Depends(get_async_db),
        current_user : User = Depends(get_current_user_async)
):
    return await llm_config_service.async_save_config(
        async_db, current_user, config.model_name, config.api_key, config.api_url
    )


@router.post("/quick_connect", summary="一次连接，多项能力（普通模式）")
async def quick_connect(
        payload: QuickConnect,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    """选平台 + 粘一次 Key → 自动配好聊天 + 资料读取两条配置并逐条测试。"""
    return await llm_config_service.async_quick_connect(
        async_db, current_user, payload.provider, payload.api_key, payload.capabilities,
    )


@router.post("/{model_name}/test", summary="测试模型配置")
async def test_config(
        model_name: str,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    result = await llm_config_service.async_test_config(async_db, current_user, model_name)
    if not result.get("ok"):
        raise InvalidInput(result)
    return result


@router.delete("/{model_name}",summary="删除模型配置")
async def delete_config(
        model_name:str,
        async_db=Depends(get_async_db),
        current_user = Depends(get_current_user_async)
):
    result = await llm_config_service.async_delete_config_by_model(async_db, current_user, model_name)
    if result["message"] =="配置不存在":
        raise NotFound("配置不存在")
    return result
