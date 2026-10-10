"""飞书 / 钉钉接入路由。

公开回调（平台调用，不带登录态，靠验签）：
  POST /integrations/{provider}/events          消息、通讯录事件
  POST /integrations/{provider}/card-actions    卡片按钮
管理（平台超级管理员）：
  GET  /admin/integrations                              两个平台的配置概况
  PUT  /admin/integrations/{provider}                   保存配置（密钥留空 = 不修改）
  POST /admin/integrations/{provider}/test              用凭证换一次令牌
  GET  /admin/integrations/{provider}/health            最近 24 小时回调、绑定、失败
  POST /admin/integrations/{provider}/sync-organization 同步组织架构
  GET  /admin/integrations/{provider}/bindings          人员绑定列表
  PUT  /admin/integrations/{provider}/bindings          手动绑定 / 改绑 / 解绑
"""
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models.init_db import User, get_db
from service.dependencies import get_current_admin_user
from service.exceptions import InvalidInput
from service.integrations import apps, dispatcher, identity
from service.integrations.base import IntegrationError

public_router = APIRouter(prefix="/integrations", tags=["外部协作平台回调"])
admin_router = APIRouter(prefix="/admin/integrations", tags=["外部协作平台管理"])


async def _callback(provider: str, kind: str, request: Request, background: BackgroundTasks) -> JSONResponse:
    apps.check_provider(provider)
    body = await request.body()
    outcome = await dispatcher.handle(provider, kind, dict(request.headers), dict(request.query_params), body)
    if outcome.background is not None:
        background.add_task(_run, outcome.background)       # 应答之后再处理（平台要求几秒内应答）
    return JSONResponse(outcome.body, status_code=outcome.status_code)


async def _run(job) -> None:
    # 必须是 async 函数交给 BackgroundTasks：普通函数会被丢进线程池调用，返回的协程不会被执行
    await job()


@public_router.post("/{provider}/events", summary="飞书 / 钉钉事件回调")
async def events(provider: str, request: Request, background: BackgroundTasks):
    return await _callback(provider, "events", request, background)


@public_router.post("/{provider}/card-actions", summary="飞书 / 钉钉卡片按钮回调")
async def card_actions(provider: str, request: Request, background: BackgroundTasks):
    return await _callback(provider, "card-actions", request, background)


class AppConfig(BaseModel):
    app_id: str = Field(min_length=1, max_length=120)
    app_secret: Optional[str] = Field(default=None, max_length=300, description="留空表示不修改")
    verification_token: Optional[str] = Field(default=None, max_length=200)
    encrypt_key: Optional[str] = Field(default=None, max_length=300, description="留空表示不修改")
    robot_code: Optional[str] = Field(default=None, max_length=120)
    card_template_id: Optional[str] = Field(default=None, max_length=120)
    enabled: bool = False


class BindingChange(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=120)
    local_user_id: Optional[int] = Field(default=None, description="不传表示解绑")


def _row_or_error(db, provider: str):
    row = apps.get_row_sync(db, provider)
    if row is None:
        raise InvalidInput("还没有配置这个平台，请先填写并保存应用凭证")
    return row


@admin_router.get("", summary="协作平台配置概况")
def list_apps(db: Session = Depends(get_db), _: User = Depends(get_current_admin_user)):
    return [apps.describe(apps.get_row_sync(db, p), p) for p in ("feishu", "dingtalk")]


@admin_router.put("/{provider}", summary="保存协作平台配置")
def save_app(provider: str, data: AppConfig, db: Session = Depends(get_db), admin: User = Depends(get_current_admin_user)):
    apps.check_provider(provider)
    return apps.save_sync(db, provider, admin.id, app_id=data.app_id, app_secret=data.app_secret or None,
                          verification_token=data.verification_token, encrypt_key=data.encrypt_key or None,
                          robot_code=data.robot_code, card_template_id=data.card_template_id, enabled=data.enabled)


@admin_router.post("/{provider}/test", summary="测试连接（用凭证换一次访问令牌）")
def test_app(provider: str, db: Session = Depends(get_db), _: User = Depends(get_current_admin_user)):
    apps.check_provider(provider)
    _row_or_error(db, provider)
    return dispatcher.test_connection_sync(db, provider)


@admin_router.get("/{provider}/health", summary="接入健康状况")
def health(provider: str, db: Session = Depends(get_db), _: User = Depends(get_current_admin_user)):
    apps.check_provider(provider)
    return dispatcher.health_sync(db, provider)


@admin_router.post("/{provider}/sync-organization", summary="同步组织架构")
def sync_organization(provider: str, db: Session = Depends(get_db), admin: User = Depends(get_current_admin_user)):
    apps.check_provider(provider)
    _row_or_error(db, provider)
    try:
        return dispatcher.sync_organization_sync(db, provider, admin.id)
    except IntegrationError as exc:
        raise InvalidInput(str(exc))


@admin_router.get("/{provider}/bindings", summary="人员绑定列表")
def list_bindings(provider: str, db: Session = Depends(get_db), _: User = Depends(get_current_admin_user)):
    apps.check_provider(provider)
    return identity.list_bindings_sync(db, provider, apps.enterprise_id_sync(db))


@admin_router.put("/{provider}/bindings", summary="手动绑定 / 改绑 / 解绑")
def change_binding(provider: str, data: BindingChange, db: Session = Depends(get_db),
                   admin: User = Depends(get_current_admin_user)):
    apps.check_provider(provider)
    row = _row_or_error(db, provider)
    return identity.bind_sync(db, admin.id, row.organization_id, provider, row.app_id, data.external_user_id,
                              data.local_user_id)
