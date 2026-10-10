"""CRM Copilot 路由（部门工作台 → 销售）。权限：只有该部门的有效成员能用，客户必须属于这个部门（业务系统校验）。

  POST /enterprise/crm-copilot/ingest/email          上传 .eml（转发进 CRM 的邮件）
  POST /enterprise/crm-copilot/ingest/calendar       上传 .ics（会议）
  POST /enterprise/crm-copilot/activities            手工记录：电话纪要、跟进、报价、待办……
  GET  /enterprise/crm-copilot/activities            待归属 / 全部活动
  POST /enterprise/crm-copilot/activities/{id}/assign  指定客户（记住对应关系）
  POST /enterprise/crm-copilot/activities/{id}/ignore  不属于任何客户
  GET  /enterprise/crm-copilot/customers/{cid}/timeline
  GET  /enterprise/crm-copilot/customers/{cid}/summary ；POST .../summary/refresh（增量更新）
  POST /enterprise/crm-copilot/customers/{cid}/risks/scan ；GET /enterprise/crm-copilot/risks
  POST /enterprise/crm-copilot/risks/{id}/dismiss
  GET  /enterprise/crm-copilot/suggestions ；POST /enterprise/crm-copilot/suggestions/{id}/decide
  GET/PUT/DELETE /enterprise/crm-copilot/mailbox ；POST /enterprise/crm-copilot/mailbox/sync
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from models.async_db import get_async_db
from models.init_db import User
from service.crm import activities, insights, mailbox
from service.crm.sources import parse_calendar, parse_email
from service.dependencies import get_current_user_async
from service.exceptions import InvalidInput

router = APIRouter(prefix="/enterprise/crm-copilot", tags=["CRM Copilot"])

MAX_UPLOAD = 25 * 1024 * 1024


async def _read(file: UploadFile, suffixes: tuple) -> bytes:
    name = (file.filename or "").lower()
    if not name.endswith(suffixes):
        raise InvalidInput(f"只支持 {'、'.join(suffixes)} 文件")
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise InvalidInput("文件超过 25MB")
    return content


@router.post("/ingest/email", summary="上传邮件（.eml）进 CRM")
async def ingest_email(team_id: int = Form(...), customer_id: Optional[int] = Form(None), file: UploadFile = File(...),
                       db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    import asyncio
    draft = await asyncio.to_thread(parse_email, await _read(file, (".eml",)))
    return await activities.ingest(db, user.id, team_id, draft, "email", explicit_customer_id=customer_id)


@router.post("/ingest/calendar", summary="上传会议（.ics）进 CRM")
async def ingest_calendar(team_id: int = Form(...), file: UploadFile = File(...),
                          db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    drafts = parse_calendar(await _read(file, (".ics",)))
    if not drafts:
        raise InvalidInput("日历文件里没有会议")
    directory = await activities.load_directory(db, user.id, team_id)
    results = [await activities.ingest(db, user.id, team_id, d, "calendar", directory=directory) for d in drafts[:100]]
    return {"created": sum(not r["duplicate"] for r in results), "duplicates": sum(r["duplicate"] for r in results),
            "items": [r["activity"] for r in results]}


class ManualActivity(BaseModel):
    team_id: int
    customer_id: int
    activity_type: Literal["call", "followup", "quote", "meeting", "chat", "todo"]
    title: str = Field(default="", max_length=300)
    content: str = Field(default="", max_length=20000)
    occurred_at: Optional[str] = None


@router.post("/activities", summary="手工记录客户活动")
async def add_activity(body: ManualActivity, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await activities.add_manual(db, user.id, body.team_id, body.customer_id, body.activity_type, body.title,
                                       body.content, body.occurred_at)


@router.get("/activities", summary="客户活动（默认只看待归属的）")
async def list_activities(team_id: int, status: str = "pending", db=Depends(get_async_db),
                          user: User = Depends(get_current_user_async)):
    return await activities.list_activities(db, user.id, team_id, status)


class AssignBody(BaseModel):
    team_id: int
    customer_id: int
    remember: bool = True


@router.post("/activities/{activity_id}/assign", summary="指定活动属于哪个客户")
async def assign(activity_id: int, body: AssignBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await activities.assign(db, user.id, body.team_id, activity_id, body.customer_id, body.remember)


class TeamBody(BaseModel):
    team_id: int


@router.post("/activities/{activity_id}/ignore", summary="不属于任何客户")
async def ignore(activity_id: int, body: TeamBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await activities.ignore(db, user.id, body.team_id, activity_id)


@router.get("/customers/{customer_id}/timeline", summary="客户时间线")
async def timeline(customer_id: int, team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await insights.timeline(db, user.id, team_id, customer_id)


@router.get("/customers/{customer_id}/summary", summary="客户摘要（最新一版）")
async def summary(customer_id: int, team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await insights.get_summary(db, user.id, team_id, customer_id)


class ModelBody(BaseModel):
    team_id: int
    model_name: Optional[str] = Field(default=None, max_length=100)


@router.post("/customers/{customer_id}/summary/refresh", summary="更新客户摘要（增量）")
async def refresh_summary(customer_id: int, body: ModelBody, db=Depends(get_async_db),
                          user: User = Depends(get_current_user_async)):
    return await insights.refresh_summary(db, user.id, body.team_id, customer_id, body.model_name)


@router.post("/customers/{customer_id}/risks/scan", summary="扫描客户风险")
async def scan(customer_id: int, body: ModelBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await insights.scan_customer(db, user.id, body.team_id, customer_id, body.model_name)


@router.get("/risks", summary="本部门未解除的风险")
async def risks(team_id: int, customer_id: Optional[int] = None, db=Depends(get_async_db),
                user: User = Depends(get_current_user_async)):
    return await insights.list_risks(db, user.id, team_id, customer_id)


@router.post("/risks/{finding_id}/dismiss", summary="关闭一条风险")
async def dismiss(finding_id: int, body: TeamBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await insights.dismiss_risk(db, user.id, body.team_id, finding_id)


@router.get("/suggestions", summary="待处理的下一步建议")
async def suggestions(team_id: int, customer_id: Optional[int] = None, db=Depends(get_async_db),
                      user: User = Depends(get_current_user_async)):
    return await insights.list_suggestions(db, user.id, team_id, customer_id)


class DecideBody(BaseModel):
    team_id: int
    decision: Literal["create", "edit_create", "ignore", "snooze"]
    title: Optional[str] = Field(default=None, max_length=300)
    due_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    remind_days: Optional[int] = Field(default=None, ge=1, le=30)


@router.post("/suggestions/{suggestion_id}/decide", summary="处理建议：确认创建 / 修改后创建 / 忽略 / 稍后提醒")
async def decide(suggestion_id: int, body: DecideBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await insights.decide_suggestion(db, user.id, body.team_id, suggestion_id, body.decision, body.title,
                                            body.due_date, body.remind_days)


@router.get("/mailbox", summary="我连接的邮箱")
async def get_mailbox(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return {"account": await mailbox.get_account(db, user.id, team_id)}


class MailboxBody(BaseModel):
    team_id: int
    imap_host: str = Field(min_length=1, max_length=200)
    imap_port: int = Field(default=993, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=200)
    password: Optional[str] = Field(default=None, max_length=500, description="留空表示不修改")
    folder: str = Field(default="CRM", min_length=1, max_length=100)
    enabled: bool = True


@router.put("/mailbox", summary="连接邮箱（IMAP，只读指定文件夹）")
async def save_mailbox(body: MailboxBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return {"account": await mailbox.save_account(db, user.id, body.team_id, imap_host=body.imap_host,
                                                  username=body.username, password=body.password,
                                                  imap_port=body.imap_port, folder=body.folder, enabled=body.enabled)}


@router.delete("/mailbox", summary="断开邮箱")
async def delete_mailbox(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    await mailbox.delete_account(db, user.id, team_id)
    return {"ok": True}


@router.post("/mailbox/sync", summary="立即同步邮箱")
async def sync_mailbox(body: TeamBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await mailbox.sync(db, user.id, body.team_id)
