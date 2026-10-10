"""平台审批（Phase 3D 阶段4，docs/enterprise-rbac-plan.md 9.5）。

只有企业管理员（`require_org_role_async("admin")`，owner 的 rank 更高也能过）能看待审批
列表、能决定。谁能"申请"审批不在这里开路由——申请是各个高风险操作自己在业务流程里
调 `approval_service.request_or_get_pending`（比如知识库空间删除），不是用户主动跑来
这里建一个审批单。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from models.async_db import get_async_db
from models.init_db import User
from service import approval_service
from service.enterprise_access import require_org_role_async

router = APIRouter(prefix="/approvals", tags=["平台审批"])


class ApprovalDecision(BaseModel):
    approve: bool


@router.get("/pending", summary="查看待审批列表（企业管理员）")
async def list_pending_approvals(
        limit: int = 50,
        async_db=Depends(get_async_db),
        current_user: User = Depends(require_org_role_async("admin")),
):
    return await approval_service.list_pending(async_db, limit=limit, approver_id=current_user.id)


@router.post("/{approval_id}/decide", summary="批准或拒绝一条审批单（企业管理员）")
async def decide_approval(
        approval_id: int,
        data: ApprovalDecision,
        async_db=Depends(get_async_db),
        current_user: User = Depends(require_org_role_async("admin")),
):
    return await approval_service.decide(async_db, approval_id, current_user.id, data.approve, enforce_org=True)
