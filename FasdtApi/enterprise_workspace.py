from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from models.async_db import get_async_db
from models.init_db import User
from service import crm_workspace_service as crm_workspace
from service import department_workspace_service as dept_workspace
from service import procurement_workspace_service as procurement_workspace
from service.dependencies import get_current_user_async
from service.enterprise_workspace_service import get_workspace

router = APIRouter(prefix="/enterprise", tags=["企业工作台"])


@router.get("/workspace")
async def workspace(db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await get_workspace(db, user.id)


# ============================================================================
# 部门工作台：请假闭环（里程碑1）。不挂在 /admin 下——任何登录用户都能调，
# 权限判断（是不是这个部门的成员/负责人）在 service 层做，不是路由级门槛。
# ============================================================================

class CreateLeaveDraftBody(BaseModel):
    team_id: int
    leave_type_code: str = Field(min_length=1, max_length=40)
    start_date: str
    end_date: str
    reason: Optional[str] = Field(default=None, max_length=500)


class LeaveDecisionBody(BaseModel):
    team_id: int
    action: str = Field(pattern="^(approve|reject)$")
    note: Optional[str] = Field(default=None, max_length=500)


@router.get("/oa/leave/mine")
async def my_leave_requests(user: User = Depends(get_current_user_async)):
    return await dept_workspace.list_my_leave_requests_async(user.id)


@router.post("/oa/leave/mine")
async def create_my_leave_draft(
        body: CreateLeaveDraftBody,
        db=Depends(get_async_db), user: User = Depends(get_current_user_async),
):
    return await dept_workspace.create_my_leave_draft_async(
        db, user.id, body.team_id, body.leave_type_code, body.start_date, body.end_date, body.reason,
    )


@router.post("/oa/leave/{request_id}/submit")
async def submit_my_leave_request(request_id: int, user: User = Depends(get_current_user_async)):
    return await dept_workspace.submit_my_leave_request_async(user.id, request_id)


@router.get("/oa/leave/team-pending")
async def team_pending_leave_requests(
        team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async),
):
    return await dept_workspace.list_team_pending_leave_requests_async(db, user.id, team_id)


@router.post("/oa/leave/{request_id}/decide")
async def decide_leave_request(
        request_id: int, body: LeaveDecisionBody,
        db=Depends(get_async_db), user: User = Depends(get_current_user_async),
):
    return await dept_workspace.decide_leave_request_async(
        db, user.id, request_id, body.team_id, body.action, body.note,
    )


# ============================================================================
# 部门工作台：采购闭环（里程碑2）。跟请假路由同一套设计，权限判断在 service 层做。
# ============================================================================

class PurchaseLineItem(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0)


class CreatePurchaseDraftBody(BaseModel):
    team_id: int
    lines: List[PurchaseLineItem]


class PurchaseDecisionBody(BaseModel):
    team_id: int
    action: str = Field(pattern="^(approve|reject)$")
    note: Optional[str] = Field(default=None, max_length=500)


@router.get("/procurement/mine")
async def my_purchase_requests(user: User = Depends(get_current_user_async)):
    return await procurement_workspace.list_my_purchase_requests_async(user.id)


@router.post("/procurement/mine")
async def create_my_purchase_draft(
        body: CreatePurchaseDraftBody,
        db=Depends(get_async_db), user: User = Depends(get_current_user_async),
):
    lines: List[Dict[str, Any]] = [line.model_dump() for line in body.lines]
    return await procurement_workspace.create_my_purchase_draft_async(db, user.id, body.team_id, lines)


@router.post("/procurement/{request_id}/submit")
async def submit_my_purchase_request(request_id: int, user: User = Depends(get_current_user_async)):
    return await procurement_workspace.submit_my_purchase_request_async(user.id, request_id)


@router.get("/procurement/team-pending")
async def team_pending_purchase_requests(
        team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async),
):
    return await procurement_workspace.list_team_pending_purchase_requests_async(db, user.id, team_id)


@router.post("/procurement/{request_id}/decide")
async def decide_purchase_request(
        request_id: int, body: PurchaseDecisionBody,
        db=Depends(get_async_db), user: User = Depends(get_current_user_async),
):
    return await procurement_workspace.decide_purchase_request_async(
        db, user.id, request_id, body.team_id, body.action, body.note,
    )


# ============================================================================
# 部门工作台：CRM 客户跟进/商机闭环（里程碑3）。跟请假/采购不同，CRM 没有审批
# 环节，权限只判断"是不是本部门成员"。
# ============================================================================

class CreateFollowupBody(BaseModel):
    team_id: int
    content: str = Field(min_length=1, max_length=1000)


class UpsertOpportunityBody(BaseModel):
    team_id: int
    opportunity_id: Optional[int] = None
    stage: str
    amount: float = Field(gt=0)


@router.get("/crm/customers")
async def team_customers(
        team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async),
):
    return await crm_workspace.list_team_customers_async(db, user.id, team_id)


@router.get("/crm/customers/{customer_id}")
async def customer_summary(
        customer_id: int, team_id: int,
        db=Depends(get_async_db), user: User = Depends(get_current_user_async),
):
    return await crm_workspace.get_customer_summary_async(db, user.id, team_id, customer_id)


@router.post("/crm/customers/{customer_id}/followups")
async def create_followup(
        customer_id: int, body: CreateFollowupBody,
        db=Depends(get_async_db), user: User = Depends(get_current_user_async),
):
    return await crm_workspace.create_followup_draft_async(db, user.id, body.team_id, customer_id, body.content)


@router.post("/crm/followups/{followup_id}/confirm")
async def confirm_followup(followup_id: int, user: User = Depends(get_current_user_async)):
    return await crm_workspace.confirm_followup_async(user.id, followup_id)


@router.post("/crm/customers/{customer_id}/opportunities")
async def upsert_opportunity(
        customer_id: int, body: UpsertOpportunityBody,
        db=Depends(get_async_db), user: User = Depends(get_current_user_async),
):
    return await crm_workspace.upsert_opportunity_async(
        db, user.id, body.team_id, customer_id, body.opportunity_id, body.stage, body.amount,
    )
