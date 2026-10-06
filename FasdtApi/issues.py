"""问题中心接口。管理员：全部问题的查看与处理；部门负责人：只读本部门的问题（安全类问题只给管理员）。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from models.async_db import get_async_db
from models.enterprise_dao import is_team_admin_of_team_async
from models.init_db import SessionLocal, User
from service import audit_service
from service.dependencies import get_current_admin_user_async, get_current_user_async
from service.exceptions import NotFound, PermissionDenied
from service.observability import issues as svc

admin_router = APIRouter(prefix="/admin/issues", tags=["问题中心（管理员）"])
dept_router = APIRouter(prefix="/enterprise/issues", tags=["问题中心（部门）"])


class ActionBody(BaseModel):
    action: str = Field(pattern="^(acknowledge|investigate|mitigate|resolve|verify|assign|note|severity)$")
    note: str = Field(default="", max_length=1000)
    responsible_user_id: Optional[int] = None
    root_cause: str = Field(default="", max_length=4000)
    resolution: str = Field(default="", max_length=4000)
    fix_version: str = Field(default="", max_length=80)
    severity: str = Field(default="", max_length=10)


def _sync(fn, *args, **kwargs):
    def run():
        db = SessionLocal()
        try:
            return fn(db, *args, **kwargs)
        finally:
            db.close()
    return run_in_threadpool(run)


@admin_router.get("")
async def list_issues(status: Optional[str] = None, severity: Optional[str] = None, department_id: Optional[int] = None,
                      keyword: Optional[str] = Query(default=None, max_length=80), limit: int = 100,
                      _: User = Depends(get_current_admin_user_async)):
    return await _sync(svc.list_issues, status, severity, department_id, keyword, limit)


@admin_router.get("/summary")
async def issue_summary(_: User = Depends(get_current_admin_user_async)):
    return await _sync(svc.summary)


@admin_router.get("/agent-health")
async def agent_health(days: int = Query(default=7, ge=1, le=90), _: User = Depends(get_current_admin_user_async)):
    from service.observability import agent_runs
    return await _sync(agent_runs.agent_health, days)


@admin_router.get("/{issue_id}")
async def issue_detail(issue_id: int, _: User = Depends(get_current_admin_user_async)):
    return await _sync(svc.get_issue, issue_id)


@admin_router.post("/{issue_id}/actions")
async def issue_action(issue_id: int, body: ActionBody, admin: User = Depends(get_current_admin_user_async)):
    result = await _sync(svc.transition, issue_id, admin.id, body.action, note=body.note,
                         responsible_user_id=body.responsible_user_id, root_cause=body.root_cause,
                         resolution=body.resolution, fix_version=body.fix_version, severity=body.severity)
    await audit_service.record_async(admin.id, f"issue.{body.action}", resource_type="system_issue", resource_id=issue_id)
    return result


@dept_router.get("")
async def department_issues(team_id: int, status: Optional[str] = None, db=Depends(get_async_db),
                            user: User = Depends(get_current_user_async)):
    if not await is_team_admin_of_team_async(db, user.id, team_id):
        raise PermissionDenied("只有部门负责人能查看本部门的问题")
    rows = await _sync(svc.list_issues, status, None, team_id, None, 100)
    return [r for r in rows if r["category"] != "security"]


@dept_router.get("/{issue_id}")
async def department_issue_detail(issue_id: int, team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    if not await is_team_admin_of_team_async(db, user.id, team_id):
        raise PermissionDenied("只有部门负责人能查看本部门的问题")
    data = await _sync(svc.get_issue, issue_id)
    if data["department_id"] != team_id or data["category"] == "security":
        raise NotFound("问题不存在")
    for item in data["occurrences"]:   # 部门负责人看到处理进展，不看技术细节
        item.pop("detail", None)
    data.pop("links", None)
    return data


events_router = APIRouter(prefix="/admin/events", tags=["事件与死信（管理员）"])


class DiscardBody(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


@events_router.get("/stats")
async def event_stats(_: User = Depends(get_current_admin_user_async)):
    from service.events import outbox
    return await _sync(outbox.stats)


@events_router.get("/dead-letters")
async def dead_letters(status: Optional[str] = "pending", _: User = Depends(get_current_admin_user_async)):
    from service.events import outbox
    return await _sync(outbox.list_dead_letters, status or None)


@events_router.get("/dead-letters/{dead_id}")
async def dead_letter_detail(dead_id: int, _: User = Depends(get_current_admin_user_async)):
    from service.events import outbox
    return await _sync(outbox.get_dead_letter, dead_id)


@events_router.post("/dead-letters/{dead_id}/redeliver")
async def redeliver_dead_letter(dead_id: int, admin: User = Depends(get_current_admin_user_async)):
    from service.events import outbox
    result = await _sync(outbox.redeliver, dead_id, admin.id)
    await audit_service.record_async(admin.id, "event.dead_letter_redeliver", resource_type="dead_letter", resource_id=dead_id)
    return result


@events_router.post("/dead-letters/{dead_id}/discard")
async def discard_dead_letter(dead_id: int, body: DiscardBody, admin: User = Depends(get_current_admin_user_async)):
    from service.events import outbox
    result = await _sync(outbox.discard, dead_id, admin.id, body.reason)
    await audit_service.record_async(admin.id, "event.dead_letter_discard", resource_type="dead_letter", resource_id=dead_id,
                                     detail={"reason": body.reason})
    return result
