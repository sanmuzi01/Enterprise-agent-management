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
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models.init_db import User, get_db
from models.async_db import get_async_db
from service.dependencies import get_current_admin_user, get_current_admin_user_async, get_current_user
from service.exceptions import InvalidInput
from service.integrations import apps, dispatcher, identity, oauth, readiness, self_binding
from service.integrations.base import IntegrationError

public_router = APIRouter(prefix="/integrations", tags=["外部协作平台回调"])
admin_router = APIRouter(prefix="/admin/integrations", tags=["外部协作平台管理"])
me_router = APIRouter(prefix="/me/integrations", tags=["我的飞书 / 钉钉"])


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


@admin_router.get("/{provider}/directory", summary="按部门的人员与外部账号对应表")
def binding_directory(provider: str, db: Session = Depends(get_db), _: User = Depends(get_current_admin_user)):
    apps.check_provider(provider)
    return identity.directory_sync(db, provider, apps.enterprise_id_sync(db))


@admin_router.put("/{provider}/bindings", summary="手动绑定 / 改绑 / 解绑")
def change_binding(provider: str, data: BindingChange, db: Session = Depends(get_db),
                   admin: User = Depends(get_current_admin_user)):
    apps.check_provider(provider)
    row = _row_or_error(db, provider)
    return identity.bind_sync(db, admin.id, row.organization_id, provider, row.app_id, data.external_user_id,
                              data.local_user_id)


# ------------------------------------------------------------------ 员工自助绑定

@me_router.get("", summary="我的飞书 / 钉钉接入状态")
def my_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return self_binding.status_sync(db, user.id)


@me_router.post("/{provider}/bind-code", summary="领取绑定码（10 分钟有效）")
def my_bind_code(provider: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    apps.check_provider(provider)
    return self_binding.create_code_sync(db, user.id, provider)


@me_router.delete("/{provider}/binding", summary="解绑我的飞书 / 钉钉账号")
def my_unbind(provider: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return self_binding.unbind_self_sync(db, user.id, provider)


@me_router.get("/{provider}/oauth/start", summary="一键授权绑定：返回授权页地址")
def my_oauth_start(provider: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    apps.check_provider(provider)
    return oauth.start_sync(db, user.id, provider)


@public_router.get("/{provider}/oauth/callback", summary="飞书 / 钉钉授权回调")
def oauth_callback(provider: str, code: str = "", state: str = "", db: Session = Depends(get_db)):
    from urllib.parse import urlencode
    apps.check_provider(provider)
    result = oauth.callback_sync(db, provider, code, state)
    query = urlencode({"provider": provider, "result": "ok" if result["ok"] else "error", "message": result["message"]})
    return RedirectResponse(f"{oauth.web_base_url()}/settings/integrations?{query}", status_code=302)


# ------------------------------------------------------------------ 管理员：自检与邀请

@admin_router.get("/{provider}/readiness", summary="接入自检：员工为什么还用不了")
def provider_readiness(provider: str, db: Session = Depends(get_db), _: User = Depends(get_current_admin_user)):
    apps.check_provider(provider)
    return readiness.readiness_sync(db, provider)


@admin_router.post("/{provider}/invite-unbound", summary="提醒还没绑定的员工")
async def invite_unbound(provider: str, db=Depends(get_async_db), admin: User = Depends(get_current_admin_user_async)):
    apps.check_provider(provider)
    return await readiness.invite_unbound(db, admin.id, provider)
