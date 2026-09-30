"""部门工作台：CRM 客户跟进/商机闭环（里程碑3）。

跟 `procurement_workspace_service.py` 同款设计——独立文件、独立的
`_translate_hub_error`，权限判断在 FastAPI 这层显式做、调 Java 之前就拦截。
跟请假/采购最大的不同：CRM **没有审批/负责人概念**（`TeamAccessGuard` 在 Java
的 crm 包里零引用），任何本部门成员都能操作本部门客户，所以这里只判断"是不是
本部门成员"（`is_team_member_of_team_async`），不需要 `is_org_admin`/
`is_team_admin` 这层 admin flags。
"""
import asyncio
import uuid
from typing import Any, Dict, List, Optional

from models.enterprise_dao import is_team_member_of_team_async
from service import enterprise_hub_client as hub
from service.exceptions import AppError, Conflict, InvalidInput, NotFound, PermissionDenied, UpstreamError


def _translate_hub_error(exc: hub.EnterpriseHubError) -> AppError:
    status = exc.status_code
    if status == 400:
        return InvalidInput(exc.detail)
    if status == 403:
        return PermissionDenied(exc.detail)
    if status == 404:
        return NotFound(exc.detail)
    if status == 409:
        return Conflict(exc.detail)
    return UpstreamError(exc.detail)


async def list_team_customers_async(db, user_id: int, team_id: int) -> List[Dict[str, Any]]:
    if not await is_team_member_of_team_async(db, user_id, team_id):
        raise PermissionDenied("不属于该部门，无法查看部门客户列表")
    try:
        return await asyncio.to_thread(
            hub.call, "GET", "/crm/customers", user_id, team_id,
            ["crm.read"], "list_team_customers",
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def get_customer_summary_async(db, user_id: int, team_id: int, customer_id: int) -> Dict[str, Any]:
    if not await is_team_member_of_team_async(db, user_id, team_id):
        raise PermissionDenied("不属于该部门，无法查看客户详情")
    try:
        return await asyncio.to_thread(
            hub.call, "GET", f"/crm/customers/{int(customer_id)}", user_id, team_id,
            ["crm.read"], "get_customer_summary",
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def create_followup_draft_async(
        db, user_id: int, team_id: int, customer_id: int, content: str,
) -> Dict[str, Any]:
    if not await is_team_member_of_team_async(db, user_id, team_id):
        raise PermissionDenied("不属于该部门，无法为该客户创建跟进记录")
    try:
        return await asyncio.to_thread(
            hub.call, "POST", f"/crm/customers/{int(customer_id)}/followups", user_id, team_id,
            ["crm.write"], "create_followup_draft",
            json_body={"content": content}, idempotency_key=str(uuid.uuid4()),
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def confirm_followup_async(user_id: int, followup_id: int) -> Dict[str, Any]:
    # 不需要 team_id/权限查询——Java 侧靠 authorUserId 校验这条跟进记录是不是
    # 调用者自己创建的，跟 submit_my_leave_request_async 是同一个道理。
    try:
        return await asyncio.to_thread(
            hub.call, "POST", f"/crm/followups/{int(followup_id)}/confirm", user_id, None,
            ["crm.write"], "submit_customer_followup",
            idempotency_key=str(uuid.uuid4()),
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def upsert_opportunity_async(
        db, user_id: int, team_id: int, customer_id: int, opportunity_id: Optional[int],
        stage: str, amount: float,
) -> Dict[str, Any]:
    if not await is_team_member_of_team_async(db, user_id, team_id):
        raise PermissionDenied("不属于该部门，无法维护该客户的商机")
    body: Dict[str, Any] = {"stage": stage, "amount": amount}
    if opportunity_id is not None:
        body["opportunityId"] = opportunity_id
    try:
        return await asyncio.to_thread(
            hub.call, "POST", f"/crm/customers/{int(customer_id)}/opportunities", user_id, team_id,
            ["crm.write"], "create_or_update_opportunity",
            json_body=body, idempotency_key=str(uuid.uuid4()),
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)
