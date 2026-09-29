"""部门工作台：请假闭环（里程碑1，docs/enterprise-rbac-plan.md 相关记录）。

跟 `service/tools/oa_leave.py`（Agent 工具层）调的是同一个企业业务中心
（`service/enterprise_hub_client.py::call`），但这里不走 `resolve_caller_context`
的"猜团队"逻辑——前端已经在部门工作台里显式选好了 `team_id`（部门切换器），
不需要再退回"用户在职的第一个部门"这种兜底猜测，直接对这个显式给定的
`team_id` 算权限（是不是这个部门的成员/负责人），比工具层的隐式推断更准确、
也更符合"用户当前明确在看哪个部门"这个场景。

授权判断在调用 Java 之前就做完，不依赖 Java 那边的 TeamAccessGuard 兜底
（那是纵深防御的第二道，不是唯一一道）——跟这个项目"权限来源只在 FastAPI
一处"的既有原则一致。
"""
import asyncio
import uuid
from typing import Any, Dict, List, Optional

from models.enterprise_dao import is_team_admin_of_team_async, is_team_member_of_team_async
from service import enterprise_access
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


async def _caller_admin_flags_async(db, user_id: int, team_id: Optional[int]) -> Dict[str, bool]:
    org_admin = await enterprise_access.is_org_admin_async(db, user_id)
    team_admin = await is_team_admin_of_team_async(db, user_id, team_id) if team_id is not None else False
    return {"is_org_admin": org_admin, "is_team_admin": team_admin}


async def list_my_leave_requests_async(user_id: int) -> List[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(
            hub.call, "GET", "/oa/leave/requests/mine", user_id, None,
            ["oa.leave.read"], "get_my_leave_requests",
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def create_my_leave_draft_async(
        db, user_id: int, team_id: int, leave_type_code: str,
        start_date: str, end_date: str, reason: Optional[str],
) -> Dict[str, Any]:
    if not await is_team_member_of_team_async(db, user_id, team_id):
        raise PermissionDenied("不属于该部门，无法为该部门创建请假申请")
    body = {"leaveTypeCode": leave_type_code, "startDate": start_date, "endDate": end_date, "reason": reason}
    try:
        return await asyncio.to_thread(
            hub.call, "POST", "/oa/leave/requests", user_id, team_id,
            ["oa.leave.write"], "create_leave_draft",
            json_body=body, idempotency_key=str(uuid.uuid4()),
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def submit_my_leave_request_async(user_id: int, request_id: int) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(
            hub.call, "POST", f"/oa/leave/requests/{int(request_id)}/submit", user_id, None,
            ["oa.leave.write"], "submit_leave_request",
            idempotency_key=str(uuid.uuid4()),
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def list_team_pending_leave_requests_async(db, user_id: int, team_id: int) -> List[Dict[str, Any]]:
    flags = await _caller_admin_flags_async(db, user_id, team_id)
    if not (flags["is_org_admin"] or flags["is_team_admin"]):
        raise PermissionDenied("不是该部门负责人或企业管理员，无法查看待审批列表")
    try:
        return await asyncio.to_thread(
            hub.call, "GET", f"/oa/leave/requests/team-pending?teamId={int(team_id)}", user_id, team_id,
            ["oa.leave.read"], "get_team_pending_leave_requests",
            is_org_admin=flags["is_org_admin"], is_team_admin=flags["is_team_admin"],
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def decide_leave_request_async(
        db, user_id: int, request_id: int, team_id: int, action: str, note: Optional[str],
) -> Dict[str, Any]:
    if action not in ("approve", "reject"):
        raise InvalidInput(f"未知的处理动作: {action}")
    flags = await _caller_admin_flags_async(db, user_id, team_id)
    if not (flags["is_org_admin"] or flags["is_team_admin"]):
        raise PermissionDenied("不是该部门负责人或企业管理员，无法处理该申请")
    try:
        return await asyncio.to_thread(
            hub.call, "POST", f"/oa/leave/requests/{int(request_id)}/{action}", user_id, team_id,
            ["oa.leave.approve"], f"{action}_leave_request",
            is_org_admin=flags["is_org_admin"], is_team_admin=flags["is_team_admin"],
            json_body={"note": note}, idempotency_key=str(uuid.uuid4()),
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)
