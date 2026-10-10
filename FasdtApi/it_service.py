"""IT 服务台接口：员工提工单/审批（所有部门）与 IT 台（仅 IT 部门）。

权限、可见范围都由 service 层判断，不是路由级门槛——非 IT 部门的人即使直接请求 IT 台接口也会被拒绝
（见 tests/test_it_service.py）。team_id 是调用者当前所在部门（工作台当前部门）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from models.async_db import get_async_db
from models.init_db import User
from service import it_service as it
from service.dependencies import get_current_user_async

router = APIRouter(prefix="/enterprise/it", tags=["IT 服务台"])

CATEGORY = "^(INCIDENT|ACCOUNT|PERMISSION|DEVICE|OTHER)$"
PRIORITY = "^(LOW|NORMAL|HIGH|URGENT)$"


class CreateTicketBody(BaseModel):
    team_id: int
    category: str = Field(pattern=CATEGORY)
    priority: Optional[str] = Field(default=None, pattern=PRIORITY)
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=4, max_length=4000)


class CommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class DecideBody(BaseModel):
    team_id: int
    action: str = Field(pattern="^(approve|reject)$")
    note: Optional[str] = Field(default=None, max_length=500)


# ---------------------------------------------------------------- 员工侧

@router.post("/tickets")
async def create_ticket(body: CreateTicketBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.create_ticket_async(db, user.id, body.team_id, body.category, body.priority, body.title.strip(),
                                        body.description.strip())


@router.get("/tickets/mine")
async def my_tickets(db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.list_my_tickets_async(db, user.id)


@router.get("/tickets/team-pending")
async def team_pending(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.list_team_pending_async(db, user.id, team_id)


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: int, team_id: Optional[int] = None, db=Depends(get_async_db),
                     user: User = Depends(get_current_user_async)):
    return await it.get_ticket_async(db, user.id, team_id, ticket_id)


@router.post("/tickets/{ticket_id}/cancel")
async def cancel_ticket(ticket_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.cancel_ticket_async(db, user.id, ticket_id)


@router.post("/tickets/{ticket_id}/comments")
async def comment_ticket(ticket_id: int, body: CommentBody, db=Depends(get_async_db),
                         user: User = Depends(get_current_user_async)):
    return await it.comment_ticket_async(db, user.id, ticket_id, body.body.strip())


@router.post("/tickets/{ticket_id}/confirm")
async def confirm_ticket(ticket_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.confirm_ticket_async(db, user.id, ticket_id)


@router.post("/tickets/{ticket_id}/reopen")
async def reopen_ticket(ticket_id: int, body: ReasonBody, db=Depends(get_async_db),
                        user: User = Depends(get_current_user_async)):
    return await it.reopen_ticket_async(db, user.id, ticket_id, body.reason.strip())


@router.post("/tickets/{ticket_id}/decide")
async def decide_ticket(ticket_id: int, body: DecideBody, db=Depends(get_async_db),
                        user: User = Depends(get_current_user_async)):
    return await it.decide_ticket_async(db, user.id, ticket_id, body.team_id, body.action, body.note)


@router.get("/suggest")
async def suggest(text: str = Query(min_length=2, max_length=500), team_id: Optional[int] = None,
                  db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.suggest_solutions_async(db, user.id, team_id, text)


@router.get("/devices/mine")
async def my_devices(user: User = Depends(get_current_user_async)):
    return await it.list_my_devices_async(user.id)


# ---------------------------------------------------------------- IT 台

class AssignBody(BaseModel):
    team_id: int
    assignee_user_id: Optional[int] = None
    take: bool = False


class StatusBody(BaseModel):
    team_id: int
    status: str = Field(pattern="^(IN_PROGRESS|WAITING_USER)$")
    note: Optional[str] = Field(default=None, max_length=500)


class ResolveBody(BaseModel):
    team_id: int
    resolution: str = Field(min_length=4, max_length=1000)


class DeskCommentBody(BaseModel):
    team_id: int
    body: str = Field(min_length=1, max_length=2000)
    internal: bool = False


class ReclassifyBody(BaseModel):
    team_id: int
    category: str = Field(pattern=CATEGORY)
    priority: str = Field(pattern=PRIORITY)
    reason: str = Field(min_length=2, max_length=200)


class CreateDeviceBody(BaseModel):
    team_id: int
    asset_no: str = Field(min_length=1, max_length=40)
    device_type: str = Field(pattern="^(LAPTOP|DESKTOP|MONITOR|PHONE|PERIPHERAL|OTHER)$")
    model: str = Field(min_length=1, max_length=100)
    purchased_on: Optional[str] = Field(default=None, max_length=10)
    warranty_until: Optional[str] = Field(default=None, max_length=10)
    note: Optional[str] = Field(default=None, max_length=300)


class AssignDeviceBody(BaseModel):
    team_id: int
    user_id: int
    ticket_id: Optional[int] = None
    note: Optional[str] = Field(default=None, max_length=300)


class DeviceActionBody(BaseModel):
    team_id: int
    note: Optional[str] = Field(default=None, max_length=300)


@router.get("/desk/tickets")
async def desk_tickets(team_id: int, status: Optional[str] = None, assignee: Optional[str] = None, overdue: bool = False,
                       limit: int = Query(default=100, ge=1, le=200), db=Depends(get_async_db),
                       user: User = Depends(get_current_user_async)):
    return await it.desk_list_tickets_async(db, user.id, team_id, status, assignee, overdue, limit)


@router.get("/desk/tickets/{ticket_id}")
async def desk_ticket(ticket_id: int, team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.desk_get_ticket_async(db, user.id, team_id, ticket_id)


@router.get("/desk/summary")
async def desk_summary(team_id: int, days: int = Query(default=30, ge=1, le=365), db=Depends(get_async_db),
                       user: User = Depends(get_current_user_async)):
    return await it.desk_summary_async(db, user.id, team_id, days)


@router.get("/desk/staff")
async def desk_staff(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.list_it_staff_async(db, user.id, team_id)


@router.post("/desk/tickets/{ticket_id}/assign")
async def desk_assign(ticket_id: int, body: AssignBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.desk_assign_async(db, user.id, body.team_id, ticket_id, body.assignee_user_id, body.take)


@router.post("/desk/tickets/{ticket_id}/status")
async def desk_status(ticket_id: int, body: StatusBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.desk_set_status_async(db, user.id, body.team_id, ticket_id, body.status, body.note)


@router.post("/desk/tickets/{ticket_id}/resolve")
async def desk_resolve(ticket_id: int, body: ResolveBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.desk_resolve_async(db, user.id, body.team_id, ticket_id, body.resolution.strip())


@router.post("/desk/tickets/{ticket_id}/comments")
async def desk_comment(ticket_id: int, body: DeskCommentBody, db=Depends(get_async_db),
                       user: User = Depends(get_current_user_async)):
    return await it.desk_comment_async(db, user.id, body.team_id, ticket_id, body.body.strip(), body.internal)


@router.post("/desk/tickets/{ticket_id}/reclassify")
async def desk_reclassify(ticket_id: int, body: ReclassifyBody, db=Depends(get_async_db),
                          user: User = Depends(get_current_user_async)):
    return await it.desk_reclassify_async(db, user.id, body.team_id, ticket_id, body.category, body.priority,
                                          body.reason.strip())


@router.get("/desk/devices")
async def desk_devices(team_id: int, status: Optional[str] = None, type: Optional[str] = None,
                       db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.desk_list_devices_async(db, user.id, team_id, status, type)


@router.get("/desk/devices/{device_id}")
async def desk_device(device_id: int, team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.desk_get_device_async(db, user.id, team_id, device_id)


@router.post("/desk/devices")
async def desk_create_device(body: CreateDeviceBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it.desk_create_device_async(db, user.id, body.team_id, body.asset_no.strip(), body.device_type,
                                             body.model.strip(), body.purchased_on, body.warranty_until, body.note)


@router.post("/desk/devices/{device_id}/assign")
async def desk_assign_device(device_id: int, body: AssignDeviceBody, db=Depends(get_async_db),
                             user: User = Depends(get_current_user_async)):
    return await it.desk_assign_device_async(db, user.id, body.team_id, device_id, body.user_id, body.ticket_id, body.note)


@router.post("/desk/devices/{device_id}/{action}")
async def desk_device_action(device_id: int, action: str, body: DeviceActionBody, db=Depends(get_async_db),
                             user: User = Depends(get_current_user_async)):
    return await it.desk_device_action_async(db, user.id, body.team_id, device_id, action, body.note)
