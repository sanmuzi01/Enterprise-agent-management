"""部门工作台：采购闭环（里程碑2）。

跟 `service/department_workspace_service.py`（请假闭环，里程碑1）是同一种设计：不走
`resolve_caller_context` 的"猜团队"逻辑——前端已经在部门工作台里显式选好了 `team_id`
（部门切换器），直接对这个显式给定的 `team_id` 算权限（是不是这个部门的成员/负责人），
授权判断在调用 Java 之前就做完，不依赖 Java 那边的 TeamAccessGuard 兜底（那是纵深防御的
第二道，不是唯一一道）。

跟请假模块分开成独立文件，不追加进 `department_workspace_service.py`：Java 侧本来就是
`LeaveService`/`ProcurementService` 分开、`oa_leave.py`/`procurement.py` 分开，这里继续
保持"一个业务域一个文件"的既有惯例；`_translate_hub_error`/`_caller_admin_flags_async`
两个小 helper 各自独立一份（跟 Java 测试类之间故意不共享 signedHeaders 基类是同一种
项目惯例），共享的基础设施（`enterprise_access.is_org_admin_async`、
`models.enterprise_dao.is_team_member_of_team_async`/`is_team_admin_of_team_async`、
`enterprise_hub_client.call`）直接 import 复用。
"""
import asyncio
import uuid
from typing import Any, Dict, List, Optional

from models.enterprise_dao import is_team_admin_of_team_async
from service.department_access import check_team_module_async, require_team_member_async
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
    if team_id is not None:
        await check_team_module_async(db, team_id, "procurement")
    org_admin = await enterprise_access.is_org_admin_async(db, user_id)
    team_admin = await is_team_admin_of_team_async(db, user_id, team_id) if team_id is not None else False
    return {"is_org_admin": org_admin, "is_team_admin": team_admin}


async def list_my_purchase_requests_async(user_id: int) -> List[Dict[str, Any]]:
    # team_id 传 None——Java 端 GET /requests/mine 不需要 team 上下文（不调
    # requireTeamId），跟"我的请假"完全对称：一个人的采购申请不分部门查，天然靠
    # requesterUserId 过滤。
    try:
        return await asyncio.to_thread(
            hub.call, "GET", "/procurement/requests/mine", user_id, None,
            ["procurement.read"], "get_my_purchase_requests",
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def create_my_purchase_draft_async(
        db, user_id: int, team_id: int, lines: List[Dict[str, Any]],
) -> Dict[str, Any]:
    await require_team_member_async(db, user_id, team_id, "procurement", message="不属于该部门，无法为该部门创建采购申请")
    try:
        return await asyncio.to_thread(
            hub.call, "POST", "/procurement/requests", user_id, team_id,
            ["procurement.write"], "create_purchase_draft",
            json_body={"lines": lines}, idempotency_key=str(uuid.uuid4()),
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def submit_my_purchase_request_async(user_id: int, request_id: int) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(
            hub.call, "POST", f"/procurement/requests/{int(request_id)}/submit", user_id, None,
            ["procurement.write"], "submit_purchase_request",
            idempotency_key=str(uuid.uuid4()),
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def list_team_pending_purchase_requests_async(db, user_id: int, team_id: int) -> List[Dict[str, Any]]:
    flags = await _caller_admin_flags_async(db, user_id, team_id)
    if not (flags["is_org_admin"] or flags["is_team_admin"]):
        raise PermissionDenied("不是该部门负责人或企业管理员，无法查看待审批列表")
    try:
        return await asyncio.to_thread(
            hub.call, "GET", f"/procurement/requests/team-pending?teamId={int(team_id)}", user_id, team_id,
            ["procurement.read"], "get_team_pending_purchase_requests",
            is_org_admin=flags["is_org_admin"], is_team_admin=flags["is_team_admin"],
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def decide_purchase_request_async(
        db, user_id: int, request_id: int, team_id: int, action: str, note: Optional[str],
) -> Dict[str, Any]:
    if action not in ("approve", "reject"):
        raise InvalidInput(f"未知的处理动作: {action}")
    flags = await _caller_admin_flags_async(db, user_id, team_id)
    if not (flags["is_org_admin"] or flags["is_team_admin"]):
        raise PermissionDenied("不是该部门负责人或企业管理员，无法处理该申请")
    try:
        return await asyncio.to_thread(
            hub.call, "POST", f"/procurement/requests/{int(request_id)}/{action}", user_id, team_id,
            ["procurement.approve"], f"{action}_purchase_request",
            is_org_admin=flags["is_org_admin"], is_team_admin=flags["is_team_admin"],
            json_body={"note": note}, idempotency_key=str(uuid.uuid4()),
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)
