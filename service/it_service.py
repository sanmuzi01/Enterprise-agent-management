"""IT 服务台：员工提工单、部门负责人审批，IT 部门人员接单处理、管理设备台账。

权限在 FastAPI 这层先判断、再签 scope 调 Java：
- 员工侧（it.ticket.*）：本部门有效成员即可，所有部门通用；审批只给申请人所在部门的负责人/企业管理员；
- IT 台（it.desk.*）：只给 IT 类型部门（department_code = it）的有效成员/企业管理员，可见范围限定在同一企业；
- 指派对象必须是同一企业里 IT 部门的有效成员，设备只能发给同一企业的有效成员；
- IT 人员不能处理自己提交的工单、申请人不能批准自己的工单，这些在 Java 里强制。
"""
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from models.enterprise_dao import is_team_admin_of_team_async
from service import enterprise_access
from service.department_access import (check_team_module_async, department_staff_scope_async,
                                       require_team_member_async)
from service.exceptions import InvalidInput, PermissionDenied
from service.hub_gateway import call_hub, scoped_path
from service.name_lookup import team_names, user_names

TICKET_READ = ["it.ticket.read"]
TICKET_WRITE = ["it.ticket.read", "it.ticket.write"]
TICKET_APPROVE = ["it.ticket.read", "it.ticket.approve"]
DESK_READ = ["it.desk.read"]
DESK_WRITE = ["it.desk.read", "it.desk.write"]

CATEGORY_LABELS = {"INCIDENT": "故障", "ACCOUNT": "账号申请", "PERMISSION": "权限申请", "DEVICE": "设备申请", "OTHER": "咨询/其他"}
STATUS_LABELS = {"PENDING_APPROVAL": "待部门批准", "OPEN": "待接单", "IN_PROGRESS": "处理中", "WAITING_USER": "等待你补充",
                 "RESOLVED": "已解决待确认", "CLOSED": "已关闭", "CANCELLED": "已撤销", "REJECTED": "未获批准"}
PRIORITY_LABELS = {"LOW": "低", "NORMAL": "普通", "HIGH": "高", "URGENT": "紧急"}


# ====================================================================== 共用

async def _enrich_tickets(db, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    users = await user_names(db, [i for r in rows for i in (r.get("requesterUserId"), r.get("assigneeUserId"))])
    teams = await team_names(db, [r.get("teamId") for r in rows])
    for row in rows:
        row["requesterName"] = users.get(row.get("requesterUserId"))
        row["assigneeName"] = users.get(row.get("assigneeUserId"))
        row["teamName"] = teams.get(row.get("teamId"))
        row["categoryLabel"] = CATEGORY_LABELS.get(row.get("category"), row.get("category"))
        row["statusLabel"] = STATUS_LABELS.get(row.get("status"), row.get("status"))
        row["priorityLabel"] = PRIORITY_LABELS.get(row.get("priority"), row.get("priority"))
    return rows


async def _enrich_detail(db, ticket: Dict[str, Any]) -> Dict[str, Any]:
    await _enrich_tickets(db, [ticket])
    authors = await user_names(db, [c["authorUserId"] for c in ticket.get("comments", [])] + [ticket.get("approverUserId")])
    for comment in ticket.get("comments", []):
        comment["authorName"] = authors.get(comment["authorUserId"])
    ticket["approverName"] = authors.get(ticket.get("approverUserId"))
    return ticket


async def _caller_flags(db, user_id: int, team_id: Optional[int]) -> Dict[str, bool]:
    if team_id is not None:
        await check_team_module_async(db, team_id, "ticket")
    return {"is_org_admin": await enterprise_access.is_org_admin_async(db, user_id),
            "is_team_admin": await is_team_admin_of_team_async(db, user_id, team_id) if team_id is not None else False}


# ====================================================================== 员工侧

async def create_ticket_async(db, user_id: int, team_id: int, category: str, priority: Optional[str], title: str,
                              description: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    await require_team_member_async(db, user_id, team_id, "ticket", message="不属于该部门，无法以该部门身份提交工单")
    body = {"category": category, "priority": priority, "title": title, "description": description}
    ticket = await call_hub("POST", "/it/tickets", user_id, team_id, TICKET_WRITE, "create_it_ticket", body, True,
                            idempotency_key=idempotency_key)
    from service import it_self_service
    await it_self_service.on_ticket_created(db, user_id, category, f"{title} {description}")   # 7 天内自助“已解决”的同一问题又来报修：记为重新打开
    return await _enrich_detail(db, ticket)


async def list_my_tickets_async(db, user_id: int) -> List[Dict[str, Any]]:
    rows = await call_hub("GET", "/it/tickets/mine", user_id, None, TICKET_READ, "get_my_it_tickets")
    return await _enrich_tickets(db, rows)


async def get_ticket_async(db, user_id: int, team_id: Optional[int], ticket_id: int) -> Dict[str, Any]:
    flags = await _caller_flags(db, user_id, team_id)
    ticket = await call_hub("GET", f"/it/tickets/{int(ticket_id)}", user_id, team_id, TICKET_READ, "get_it_ticket",
                            **flags)
    return await _enrich_detail(db, ticket)


async def _own_action(db, user_id: int, ticket_id: int, action: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ticket = await call_hub("POST", f"/it/tickets/{int(ticket_id)}/{action}", user_id, None, TICKET_WRITE,
                            f"{action}_it_ticket", body, True)
    return await _enrich_detail(db, ticket)


async def cancel_ticket_async(db, user_id: int, ticket_id: int) -> Dict[str, Any]:
    return await _own_action(db, user_id, ticket_id, "cancel")


async def comment_ticket_async(db, user_id: int, ticket_id: int, body: str) -> Dict[str, Any]:
    return await _own_action(db, user_id, ticket_id, "comments", {"body": body, "internal": False})


async def confirm_ticket_async(db, user_id: int, ticket_id: int) -> Dict[str, Any]:
    return await _own_action(db, user_id, ticket_id, "confirm")


async def reopen_ticket_async(db, user_id: int, ticket_id: int, reason: str) -> Dict[str, Any]:
    ticket = await _own_action(db, user_id, ticket_id, "reopen", {"reason": reason})
    from service import it_self_service
    await it_self_service.on_ticket_reopened(db, ticket_id)          # 自助转出的工单被重开：影响质量指标
    return ticket


async def list_team_pending_async(db, user_id: int, team_id: int) -> List[Dict[str, Any]]:
    flags = await _caller_flags(db, user_id, team_id)
    if not (flags["is_org_admin"] or flags["is_team_admin"]):
        raise PermissionDenied("不是该部门负责人或企业管理员，无法查看待审批的 IT 工单")
    rows = await call_hub("GET", scoped_path("/it/tickets/team-pending", None, teamId=int(team_id)), user_id, team_id,
                          TICKET_READ, "get_team_pending_it_tickets", **flags)
    return await _enrich_tickets(db, rows)


async def decide_ticket_async(db, user_id: int, ticket_id: int, team_id: int, action: str,
                              note: Optional[str]) -> Dict[str, Any]:
    if action not in ("approve", "reject"):
        raise InvalidInput(f"未知的处理动作: {action}")
    flags = await _caller_flags(db, user_id, team_id)
    if not (flags["is_org_admin"] or flags["is_team_admin"]):
        raise PermissionDenied("不是该部门负责人或企业管理员，无法处理该 IT 工单")
    ticket = await call_hub("POST", f"/it/tickets/{int(ticket_id)}/{action}", user_id, team_id, TICKET_APPROVE,
                            f"{action}_it_ticket", {"note": note}, True, **flags)
    return await _enrich_detail(db, ticket)


async def suggest_solutions_async(db, user_id: int, team_id: Optional[int], text_: str) -> Dict[str, Any]:
    """提交前的自助建议：规则分类 + 自助解决方案。只读，不创建任何数据。"""
    if team_id is not None:
        await require_team_member_async(db, user_id, team_id, "ticket")
    from urllib.parse import quote
    classification = await call_hub("GET", f"/it/classify?text={quote(text_)}", user_id, team_id, TICKET_READ, "classify_it_ticket")
    articles = await call_hub("GET", f"/it/kb/suggest?text={quote(text_)}", user_id, team_id, TICKET_READ, "suggest_it_solutions")
    classification["categoryLabel"] = CATEGORY_LABELS.get(classification["category"])
    classification["priorityLabel"] = PRIORITY_LABELS.get(classification["priority"])
    return {"classification": classification, "articles": articles}


async def list_my_devices_async(user_id: int) -> List[Dict[str, Any]]:
    return await call_hub("GET", "/it/devices/mine", user_id, None, TICKET_READ, "get_my_devices")


# ====================================================================== IT 台

async def _desk(db, user_id: int, team_id: int) -> Dict[str, Any]:
    return await department_staff_scope_async(db, user_id, team_id, "it", "IT 服务台")


async def _desk_call(db, method: str, path: str, user_id: int, team_id: int, ctx: Dict[str, Any], scopes: List[str],
                     operation: str, body: Optional[Dict[str, Any]] = None, write: bool = False, **params: Any) -> Any:
    return await call_hub(method, scoped_path(path, ctx["scope"], **params), user_id, team_id, scopes, operation, body, write)


async def desk_list_tickets_async(db, user_id: int, team_id: int, status: Optional[str] = None,
                                  assignee: Optional[str] = None, overdue: bool = False, limit: int = 100) -> List[Dict[str, Any]]:
    ctx = await _desk(db, user_id, team_id)
    rows = await _desk_call(db, "GET", "/it/desk/tickets", user_id, team_id, ctx, DESK_READ, "it_desk_list",
                            status=status, assignee=assignee, overdue="true" if overdue else None, limit=limit)
    return await _enrich_tickets(db, rows)


async def desk_get_ticket_async(db, user_id: int, team_id: int, ticket_id: int) -> Dict[str, Any]:
    ctx = await _desk(db, user_id, team_id)
    ticket = await _desk_call(db, "GET", f"/it/desk/tickets/{int(ticket_id)}", user_id, team_id, ctx, DESK_READ, "it_desk_get")
    return await _enrich_detail(db, ticket)


async def _is_it_staff(db, org_id: int, user_id: int) -> bool:
    row = (await db.execute(text(
        "SELECT 1 FROM team_members tm JOIN teams t ON t.id = tm.team_id AND t.status = 'active' "
        "AND t.department_code = 'it' AND t.organization_id = :o "
        "JOIN organization_members om ON om.organization_id = t.organization_id AND om.user_id = tm.user_id "
        "AND om.status = 'active' WHERE tm.user_id = :u AND tm.status = 'active' LIMIT 1"),
        {"o": org_id, "u": user_id})).first()
    return row is not None


async def list_it_staff_async(db, user_id: int, team_id: int) -> List[Dict[str, Any]]:
    ctx = await _desk(db, user_id, team_id)
    rows = (await db.execute(text(
        "SELECT DISTINCT u.id, u.name FROM team_members tm JOIN teams t ON t.id = tm.team_id AND t.status = 'active' "
        "AND t.department_code = 'it' AND t.organization_id = :o "
        "JOIN organization_members om ON om.organization_id = t.organization_id AND om.user_id = tm.user_id "
        "AND om.status = 'active' JOIN `user` u ON u.id = tm.user_id WHERE tm.status = 'active' ORDER BY u.id"),
        {"o": ctx["organization_id"]})).all()
    return [{"id": int(r[0]), "name": r[1]} for r in rows]


async def desk_assign_async(db, user_id: int, team_id: int, ticket_id: int, assignee_user_id: Optional[int],
                            take: bool = False) -> Dict[str, Any]:
    ctx = await _desk(db, user_id, team_id)
    target = user_id if take else assignee_user_id
    if target is not None and target != user_id and not await _is_it_staff(db, ctx["organization_id"], target):
        raise InvalidInput("只能指派给 IT 部门的有效成员")
    if target == user_id and not await _is_it_staff(db, ctx["organization_id"], user_id) \
            and not await enterprise_access.is_org_admin_async(db, user_id):
        raise InvalidInput("只能指派给 IT 部门的有效成员")
    ticket = await _desk_call(db, "POST", f"/it/desk/tickets/{int(ticket_id)}/assign", user_id, team_id, ctx, DESK_WRITE,
                              "it_desk_assign", {"assigneeUserId": target}, True)
    return await _enrich_detail(db, ticket)


async def desk_set_status_async(db, user_id: int, team_id: int, ticket_id: int, status: str, note: Optional[str]) -> Dict[str, Any]:
    ctx = await _desk(db, user_id, team_id)
    ticket = await _desk_call(db, "POST", f"/it/desk/tickets/{int(ticket_id)}/status", user_id, team_id, ctx, DESK_WRITE,
                              "it_desk_status", {"status": status, "note": note}, True)
    return await _enrich_detail(db, ticket)


async def desk_resolve_async(db, user_id: int, team_id: int, ticket_id: int, resolution: str) -> Dict[str, Any]:
    ctx = await _desk(db, user_id, team_id)
    ticket = await _desk_call(db, "POST", f"/it/desk/tickets/{int(ticket_id)}/resolve", user_id, team_id, ctx, DESK_WRITE,
                              "it_desk_resolve", {"resolution": resolution}, True)
    return await _enrich_detail(db, ticket)


async def desk_comment_async(db, user_id: int, team_id: int, ticket_id: int, body: str, internal: bool) -> Dict[str, Any]:
    ctx = await _desk(db, user_id, team_id)
    ticket = await _desk_call(db, "POST", f"/it/desk/tickets/{int(ticket_id)}/comments", user_id, team_id, ctx, DESK_WRITE,
                              "it_desk_comment", {"body": body, "internal": internal}, True)
    return await _enrich_detail(db, ticket)


async def desk_reclassify_async(db, user_id: int, team_id: int, ticket_id: int, category: str, priority: str,
                                reason: str) -> Dict[str, Any]:
    ctx = await _desk(db, user_id, team_id)
    ticket = await _desk_call(db, "POST", f"/it/desk/tickets/{int(ticket_id)}/reclassify", user_id, team_id, ctx, DESK_WRITE,
                              "it_desk_reclassify", {"category": category, "priority": priority, "reason": reason}, True)
    return await _enrich_detail(db, ticket)


def _hours(value: Any) -> str:
    return "—" if value is None else f"{float(value):g} 小时"


def narrative_for(summary: Dict[str, Any]) -> str:
    """服务台小结：只复述汇总里的数字。"""
    status = summary.get("byStatus", {})
    active = sum(int(status.get(k, 0)) for k in ("OPEN", "IN_PROGRESS", "WAITING_USER"))
    parts = [f"当前处理中的工单 {active} 张（待接单 {summary.get('unassigned', 0)} 张，已超时 {summary.get('overdue', 0)} 张）。"]
    if status.get("PENDING_APPROVAL"):
        parts.append(f"另有 {status['PENDING_APPROVAL']} 张在等部门负责人批准。")
    effect = summary.get("effect") or {}
    days = summary.get("days", 30)
    if effect.get("resolved"):
        parts.append(f"近 {days} 天解决 {effect['resolved']} 张，SLA 达成率 {effect['slaMetRate']:g}%，"
                     f"平均首次响应 {_hours(effect.get('avgFirstResponseHours'))}，平均解决 {_hours(effect.get('avgResolveHours'))}。")
    else:
        parts.append(f"近 {days} 天还没有解决的工单。")
    if effect.get("reopenedTickets"):
        parts.append(f"其中 {effect['reopenedTickets']} 张被重新打开过，建议回看处理方案。")
    return "".join(parts)


async def desk_summary_async(db, user_id: int, team_id: int, days: int = 30) -> Dict[str, Any]:
    ctx = await _desk(db, user_id, team_id)
    summary = await _desk_call(db, "GET", "/it/desk/summary", user_id, team_id, ctx, DESK_READ, "it_desk_summary", days=days)
    names = await user_names(db, [row["userId"] for row in summary.get("load", [])])
    for row in summary.get("load", []):
        row["userName"] = names.get(int(row["userId"]))
    summary["narrative"] = narrative_for(summary)
    return summary


# ---------------- 设备 ----------------

async def _enrich_devices(db, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    users = await user_names(db, [r.get("assigneeUserId") for r in rows]
                             + [e.get(k) for r in rows for e in r.get("events", []) for k in ("actorUserId", "subjectUserId")])
    for row in rows:
        row["assigneeName"] = users.get(row.get("assigneeUserId"))
        for event in row.get("events", []):
            event["actorName"] = users.get(event.get("actorUserId"))
            event["subjectName"] = users.get(event.get("subjectUserId"))
    return rows


async def desk_list_devices_async(db, user_id: int, team_id: int, status: Optional[str] = None,
                                  device_type: Optional[str] = None) -> List[Dict[str, Any]]:
    ctx = await _desk(db, user_id, team_id)
    rows = await _desk_call(db, "GET", "/it/desk/devices", user_id, team_id, ctx, DESK_READ, "it_desk_devices",
                            status=status, type=device_type)
    return await _enrich_devices(db, rows)


async def desk_get_device_async(db, user_id: int, team_id: int, device_id: int) -> Dict[str, Any]:
    ctx = await _desk(db, user_id, team_id)
    device = await _desk_call(db, "GET", f"/it/desk/devices/{int(device_id)}", user_id, team_id, ctx, DESK_READ, "it_desk_device")
    return (await _enrich_devices(db, [device]))[0]


async def desk_create_device_async(db, user_id: int, team_id: int, asset_no: str, device_type: str, model: str,
                                   purchased_on: Optional[str], warranty_until: Optional[str], note: Optional[str]) -> Dict[str, Any]:
    ctx = await _desk(db, user_id, team_id)
    body = {"assetNo": asset_no, "deviceType": device_type, "model": model, "managingTeamId": team_id,
            "purchasedOn": purchased_on or None, "warrantyUntil": warranty_until or None, "note": note}
    device = await _desk_call(db, "POST", "/it/desk/devices", user_id, team_id, ctx, DESK_WRITE, "it_desk_create_device", body, True)
    return (await _enrich_devices(db, [device]))[0]


async def desk_assign_device_async(db, user_id: int, team_id: int, device_id: int, holder_user_id: int,
                                   ticket_id: Optional[int], note: Optional[str]) -> Dict[str, Any]:
    ctx = await _desk(db, user_id, team_id)
    member = (await db.execute(text(
        "SELECT 1 FROM organization_members WHERE organization_id = :o AND user_id = :u AND status = 'active'"),
        {"o": ctx["organization_id"], "u": holder_user_id})).first()
    if member is None:
        raise InvalidInput("设备只能发给本企业的有效成员")
    device = await _desk_call(db, "POST", f"/it/desk/devices/{int(device_id)}/assign", user_id, team_id, ctx, DESK_WRITE,
                              "it_desk_assign_device", {"userId": holder_user_id, "ticketId": ticket_id, "note": note}, True)
    return (await _enrich_devices(db, [device]))[0]


async def desk_device_action_async(db, user_id: int, team_id: int, device_id: int, action: str,
                                   note: Optional[str]) -> Dict[str, Any]:
    if action not in ("return", "repair", "repair-done", "retire"):
        raise InvalidInput(f"未知的设备操作: {action}")
    ctx = await _desk(db, user_id, team_id)
    device = await _desk_call(db, "POST", f"/it/desk/devices/{int(device_id)}/{action}", user_id, team_id, ctx, DESK_WRITE,
                              f"it_desk_device_{action}", {"note": note}, True)
    return (await _enrich_devices(db, [device]))[0]
