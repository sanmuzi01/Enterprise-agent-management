"""部门责任执行：文本整理出的责任计划 → 负责人核对并正式指派 → 员工接受/执行/提交 → 验收人验收。

业务规则（状态机、谁能做什么、完整性检查、履责记录）在 Java 业务服务里（enterprise-business-hub 的 responsibility 包），
这里只做三件事：
1. 计算调用者此刻的身份——scope（企业全部部门）、member_teams（仍是有效成员的部门）、head_teams（负责人部门，
   企业管理员 = 全部）——写进已签名的请求路径；离开部门后立刻失效；
2. 计算"此刻可以被指派的人"（有效的部门成员 / 可验收的人）随请求体签名传给 Java，发布与变更时 Java 再复核；
3. 给返回结构补上姓名、状态中文名等展示信息。
Agent 只能建议责任人，不能替管理者指派、替员工接受、替验收人确认——这些决定只能由人在工作台或确认流程里做。
"""
import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from sqlalchemy import text

from service import enterprise_access
from service.department_access import require_team_member_async
from service.exceptions import InvalidInput, PermissionDenied
from service.hub_gateway import call_hub
from service.name_lookup import user_names

READ = ["responsibility.read"]
WRITE = ["responsibility.read", "responsibility.write"]

STATUS_LABELS = {"DRAFT": "草稿", "PENDING_ACCEPT": "待员工接受", "NEGOTIATING": "待重新协商", "IN_PROGRESS": "执行中",
                 "BLOCKED": "受阻", "PENDING_REVIEW": "待验收", "DONE": "已完成", "CANCELLED": "已取消"}
PLAN_STATUS_LABELS = {"DRAFT": "草稿（待发布）", "PUBLISHED": "进行中", "COMPLETED": "已完成", "CANCELLED": "已取消"}
PRIORITY_LABELS = {"LOW": "低", "NORMAL": "普通", "HIGH": "高", "URGENT": "紧急"}
SOURCE_LABELS = {"MEETING": "会议纪要", "CHAT": "聊天记录", "EMAIL": "邮件", "NOTICE": "通知", "OTHER": "其他材料"}
EVENT_LABELS = {"PLAN_CREATED": "生成计划草稿", "TASK_EDITED": "修改草稿", "TASK_ADDED": "新增事项", "TASK_REMOVED": "删除事项",
                "PUBLISHED": "正式指派", "ACCEPTED": "接受责任", "OBJECTED": "提出异议", "REVISED": "变更责任",
                "PROGRESS": "报告进度", "BLOCKED": "报告受阻", "UNBLOCKED": "解除受阻", "SUBMITTED": "提交成果",
                "VERIFIED": "验收通过", "REWORK": "验收退回", "EXTENSION_REQUESTED": "申请延期", "EXTENSION_APPROVED": "同意延期",
                "EXTENSION_REJECTED": "不同意延期", "TRANSFER_REQUESTED": "申请转交", "TRANSFER_REJECTED": "不同意转交",
                "CANCELLED": "取消责任", "PLAN_CANCELLED": "取消计划"}
FIELD_LABELS = {"responsible": "主责员工", "reviewer": "验收人", "dueDate": "截止日期", "acceptanceCriteria": "验收标准",
                "deliverable": "交付物", "priority": "优先级", "collaborators": "协办人"}
ACTIONS = {"accept", "object", "progress", "block", "unblock", "submit", "verify", "rework", "request-extension",
           "decide-extension", "request-transfer", "decide-transfer", "revise", "cancel"}
NEEDS_ELIGIBLE = {"revise", "decide-transfer", "block"}

_MEMBERSHIP_SQL = (
    "SELECT t.id, er.code FROM team_members tm "
    "JOIN teams t ON t.id = tm.team_id AND t.status = 'active' AND t.organization_id = :o "
    "JOIN organization_members om ON om.organization_id = t.organization_id AND om.user_id = tm.user_id AND om.status = 'active' "
    "JOIN enterprise_role er ON er.id = tm.role_id WHERE tm.user_id = :u AND tm.status = 'active'")
_TEAMS_SQL = "SELECT id FROM teams WHERE organization_id = :o AND status = 'active' ORDER BY id"
_MEMBERS_SQL = (
    "SELECT DISTINCT tm.user_id FROM team_members tm JOIN organization_members om ON om.organization_id = :o "
    "AND om.user_id = tm.user_id AND om.status = 'active' WHERE tm.team_id = :t AND tm.status = 'active' ORDER BY tm.user_id")
_ADMINS_SQL = (
    "SELECT om.user_id FROM organization_members om JOIN enterprise_role er ON er.id = om.role_id "
    "WHERE om.organization_id = :o AND om.status = 'active' AND er.code IN ('owner', 'admin') ORDER BY om.user_id")


# ---------------------------------------------------------------- 身份与可指派名单

def _build_actor(org_id, teams, rows, org_admin: bool) -> Dict[str, Any]:
    scope = [int(r[0]) for r in teams]
    members = sorted({int(r[0]) for r in rows})
    heads = scope if org_admin else sorted({int(r[0]) for r in rows if r[1] == "admin"})
    return {"organization_id": int(org_id), "scope": scope, "members": members, "heads": heads, "org_admin": org_admin}


async def actor_for(db, user_id: int, team_id: int) -> Dict[str, Any]:
    """以当前所在部门所属的企业为范围，计算调用者在责任协同里的身份。部门成员身份或企业身份失效就拒绝。"""
    await require_team_member_async(db, user_id, team_id, "responsibility", message="不属于该部门，或企业/部门/成员身份已失效")
    org_id = (await db.execute(text("SELECT organization_id FROM teams WHERE id = :t"), {"t": team_id})).scalar()
    teams = (await db.execute(text(_TEAMS_SQL), {"o": org_id})).all()
    rows = (await db.execute(text(_MEMBERSHIP_SQL), {"o": org_id, "u": user_id})).all()
    return _build_actor(org_id, teams, rows, await enterprise_access.is_org_admin_async(db, user_id))


def actor_for_sync(user_id: int, team_id: int) -> Dict[str, Any]:
    """`actor_for` 的同步版（Agent 工具用），规则一致；不满足时抛 PermissionDenied。"""
    from models.enterprise_dao import is_team_member_of_team
    from models.init_db import SessionLocal
    db = SessionLocal()
    try:
        if not is_team_member_of_team(db, user_id, team_id):
            raise PermissionDenied("不属于该部门，或企业/部门/成员身份已失效")
        org_id = db.execute(text("SELECT organization_id FROM teams WHERE id = :t"), {"t": team_id}).scalar()
        teams = db.execute(text(_TEAMS_SQL), {"o": org_id}).all()
        rows = db.execute(text(_MEMBERSHIP_SQL), {"o": org_id, "u": user_id}).all()
        return _build_actor(org_id, teams, rows, enterprise_access.is_org_admin(db, user_id))
    finally:
        db.close()


def query(actor: Dict[str, Any], **params: Any) -> str:
    parts = {"scopeTeamIds": ",".join(str(t) for t in actor["scope"])}
    if actor["members"]:
        parts["memberTeamIds"] = ",".join(str(t) for t in actor["members"])
    if actor["heads"]:
        parts["headTeamIds"] = ",".join(str(t) for t in actor["heads"])
    parts.update({k: v for k, v in params.items() if v is not None})
    return urlencode(parts, safe=",")


def scoped(path: str, actor: Dict[str, Any], **params: Any) -> str:
    return f"{path}?{query(actor, **params)}"


async def eligible_async(db, organization_id: int, team_id: int) -> Dict[str, List[int]]:
    """此刻可以被指派的人：memberIds = 该部门有效成员（主责/协办）；reviewerIds = 成员 + 企业所有者/管理员（验收人）。"""
    members = [int(r[0]) for r in (await db.execute(text(_MEMBERS_SQL), {"o": organization_id, "t": team_id})).all()]
    admins = [int(r[0]) for r in (await db.execute(text(_ADMINS_SQL), {"o": organization_id})).all()]
    return {"memberIds": members, "reviewerIds": sorted(set(members) | set(admins))}


def eligible_sync(organization_id: int, team_id: int) -> Dict[str, List[int]]:
    from models.init_db import SessionLocal
    db = SessionLocal()
    try:
        members = [int(r[0]) for r in db.execute(text(_MEMBERS_SQL), {"o": organization_id, "t": team_id}).all()]
        admins = [int(r[0]) for r in db.execute(text(_ADMINS_SQL), {"o": organization_id}).all()]
    finally:
        db.close()
    return {"memberIds": members, "reviewerIds": sorted(set(members) | set(admins))}


# 员工别名（真实姓名、工号、常用叫法 → 平台账号）：人事导入考勤时确认过的对应，和整理责任时负责人手动选过的对应，
# 存在同一张表里（attendance_alias，按企业唯一）。平台账号名常常是 zhangsan、demo_emp，会议纪要里写的却是“张三”。
_ALIAS_SQL = "SELECT user_id, alias FROM attendance_alias WHERE organization_id = :o ORDER BY id"


def _alias_map(rows) -> Dict[int, List[str]]:
    result: Dict[int, List[str]] = {}
    for uid, alias in rows:
        result.setdefault(int(uid), []).append(alias)
    return result


async def candidates_async(db, user_id: int, team_id: int) -> Dict[str, Any]:
    """整理文本和指派时的下拉/姓名匹配范围：本部门有效成员（主责/协办）与可验收的人，带上他们的员工别名。"""
    actor = await actor_for(db, user_id, team_id)
    eligible = await eligible_async(db, actor["organization_id"], team_id)
    heads = {int(r[0]) for r in (await db.execute(text(
        "SELECT tm.user_id FROM team_members tm JOIN enterprise_role er ON er.id = tm.role_id "
        "WHERE tm.team_id = :t AND tm.status = 'active' AND er.code = 'admin'"), {"t": team_id})).all()}
    names = await user_names(db, eligible["reviewerIds"])
    aliases = _alias_map((await db.execute(text(_ALIAS_SQL), {"o": actor["organization_id"]})).all())
    members = [{"user_id": u, "name": names.get(u) or f"用户 {u}", "is_head": u in heads, "aliases": aliases.get(u, [])}
               for u in eligible["memberIds"]]
    reviewers = [{"user_id": u, "name": names.get(u) or f"用户 {u}", "aliases": aliases.get(u, [])} for u in eligible["reviewerIds"]]
    return {"members": members, "reviewers": reviewers,
            "me": {"user_id": user_id, "is_head": team_id in actor["heads"]}}


def candidates_sync(user_id: int, team_id: int) -> Dict[str, Any]:
    """`candidates_async` 的同步版（Agent 工具匹配姓名用）。"""
    from models.init_db import SessionLocal
    actor = actor_for_sync(user_id, team_id)
    eligible = eligible_sync(actor["organization_id"], team_id)
    db = SessionLocal()
    try:
        names = _names_sync(db, eligible["reviewerIds"])
        heads = {int(r[0]) for r in db.execute(text(
            "SELECT tm.user_id FROM team_members tm JOIN enterprise_role er ON er.id = tm.role_id "
            "WHERE tm.team_id = :t AND tm.status = 'active' AND er.code = 'admin'"), {"t": team_id}).all()}
        aliases = _alias_map(db.execute(text(_ALIAS_SQL), {"o": actor["organization_id"]}).all())
    finally:
        db.close()
    return {"members": [{"user_id": u, "name": names.get(u) or f"用户 {u}", "is_head": u in heads, "aliases": aliases.get(u, [])}
                        for u in eligible["memberIds"]],
            "reviewers": [{"user_id": u, "name": names.get(u) or f"用户 {u}", "aliases": aliases.get(u, [])}
                          for u in eligible["reviewerIds"]],
            "organization_id": actor["organization_id"], "actor": actor, "eligible": eligible}


def _names_sync(db, ids: List[int]) -> Dict[int, str]:
    unique = sorted({int(i) for i in ids if i is not None})
    if not unique:
        return {}
    marks = ",".join(f":i{n}" for n in range(len(unique)))
    rows = db.execute(text(f"SELECT id, name FROM `user` WHERE id IN ({marks})"), {f"i{n}": v for n, v in enumerate(unique)}).all()
    return {int(r[0]): r[1] for r in rows}


def enrich_sync(data: Any) -> Any:
    """`_enrich` 的同步版：补上姓名与中文状态（Agent 工具返回给模型/用户看）。"""
    from models.init_db import SessionLocal
    ids: set = set()
    _collect_ids(data, ids)
    db = SessionLocal()
    try:
        _decorate(data, _names_sync(db, list(ids)))
    finally:
        db.close()
    return data


# ---------------------------------------------------------------- 调用 Java 与展示信息

async def _call(db, user_id: int, team_id: int, method: str, path: str, scopes: List[str], operation: str,
                body: Optional[Dict[str, Any]] = None, write: bool = False, actor: Optional[Dict[str, Any]] = None,
                idempotency_key: Optional[str] = None, **params: Any) -> Any:
    actor = actor or await actor_for(db, user_id, team_id)
    return await call_hub(method, scoped(path, actor, **params), user_id, team_id, scopes, operation, body, write,
                          idempotency_key=idempotency_key)


def _collect_ids(value: Any, ids: set) -> None:
    keys = {"responsibleUserId", "reviewerUserId", "assignedByUserId", "waitingOnUserId", "createdBy", "publishedBy",
            "actorUserId", "submittedBy", "userId"}
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, int):
                ids.add(item)
            elif key == "collaboratorUserIds" and isinstance(item, list):
                ids.update(i for i in item if isinstance(i, int))
            elif key == "detail" and isinstance(item, str) and item.startswith("{"):
                try:
                    _collect_detail_ids(json.loads(item), ids)
                except ValueError:
                    pass
            else:
                _collect_ids(item, ids)
    elif isinstance(value, list):
        for item in value:
            _collect_ids(item, ids)


def _collect_detail_ids(detail: Dict[str, Any], ids: set) -> None:
    for key in ("responsible", "waitingOn"):
        if isinstance(detail.get(key), int):
            ids.add(detail[key])
    for change in detail.get("changes", []) or []:
        if change.get("field") in ("responsible", "reviewer", "collaborators"):
            for end in ("from", "to"):
                v = change.get(end)
                for i in (v if isinstance(v, list) else [v]):
                    if isinstance(i, (int, str)) and str(i).isdigit():
                        ids.add(int(i))


def _decorate(value: Any, names: Dict[int, str]) -> None:
    if isinstance(value, list):
        for item in value:
            _decorate(item, names)
        return
    if not isinstance(value, dict):
        return
    nm = lambda i: names.get(i) if i is not None else None   # noqa: E731
    if "status" in value and "planId" in value and "seq" in value:       # 责任事项
        value["statusLabel"] = STATUS_LABELS.get(value["status"], value["status"])
        value["priorityLabel"] = PRIORITY_LABELS.get(value.get("priority"), value.get("priority"))
        value["responsibleName"] = nm(value.get("responsibleUserId"))
        value["reviewerName"] = nm(value.get("reviewerUserId"))
        value["assignedByName"] = nm(value.get("assignedByUserId"))
        value["waitingOnName"] = nm(value.get("waitingOnUserId"))
        value["collaboratorNames"] = [names.get(i) or f"用户 {i}" for i in value.get("collaboratorUserIds", [])]
    if "sourceType" in value and "tasks" in value:                        # 计划
        value["statusLabel"] = PLAN_STATUS_LABELS.get(value["status"], value["status"])
        value["sourceLabel"] = SOURCE_LABELS.get(value["sourceType"], value["sourceType"])
        value["createdByName"] = nm(value.get("createdBy"))
        value["publishedByName"] = nm(value.get("publishedBy"))
    if "sourceType" in value and "taskCount" in value:                    # 计划摘要
        value["statusLabel"] = PLAN_STATUS_LABELS.get(value["status"], value["status"])
        value["sourceLabel"] = SOURCE_LABELS.get(value["sourceType"], value["sourceType"])
        value["createdByName"] = nm(value.get("createdBy"))
    if "type" in value and "actorUserId" in value:                        # 事件
        value["typeLabel"] = EVENT_LABELS.get(value["type"], value["type"])
        value["actorName"] = nm(value["actorUserId"])
        raw = value.get("detail")
        parsed = None
        if isinstance(raw, str) and raw.startswith("{"):
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
        if parsed is not None:
            for change in parsed.get("changes", []) or []:
                change["fieldLabel"] = FIELD_LABELS.get(change.get("field"), change.get("field"))
                if change.get("field") in ("responsible", "reviewer"):
                    for end in ("from", "to"):
                        v = change.get(end)
                        change[end + "Text"] = names.get(int(v)) if v is not None and str(v).isdigit() else v
                elif change.get("field") == "collaborators":
                    for end in ("from", "to"):
                        change[end + "Text"] = "、".join(names.get(int(i)) or f"用户 {i}" for i in (change.get(end) or []))
                else:
                    for end in ("from", "to"):
                        change[end + "Text"] = change.get(end)
            if isinstance(parsed.get("waitingOn"), int):
                parsed["waitingOnName"] = names.get(parsed["waitingOn"])
            value["detailData"] = parsed
    if "submissionNo" in value:
        value["submittedByName"] = nm(value.get("submittedBy"))
    for item in value.values():
        if isinstance(item, (dict, list)):
            _decorate(item, names)


async def _enrich(db, data: Any) -> Any:
    ids: set = set()
    _collect_ids(data, ids)
    _decorate(data, await user_names(db, list(ids)))
    return data


# ---------------------------------------------------------------- 查询

async def mine_async(db, user_id: int, team_id: int) -> Dict[str, Any]:
    return await _call(db, user_id, team_id, "GET", "/responsibility/mine", READ, "responsibility_mine", teamId=team_id)


async def list_tasks_async(db, user_id: int, team_id: int, view: str = "mine", status: Optional[str] = None,
                           all_teams: bool = False) -> List[Dict[str, Any]]:
    # 等我验收的不限当前部门：企业管理员可能在别的部门担任验收人
    rows = await _call(db, user_id, team_id, "GET", "/responsibility/tasks", READ, "list_responsibilities", view=view,
                       status=status, teamId=None if all_teams or view == "review" else team_id, limit=200)
    return await _enrich(db, rows)


async def get_task_async(db, user_id: int, team_id: int, task_id: int) -> Dict[str, Any]:
    row = await _call(db, user_id, team_id, "GET", f"/responsibility/tasks/{int(task_id)}", READ, "get_responsibility")
    return await _enrich(db, row)


async def list_plans_async(db, user_id: int, team_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = await _call(db, user_id, team_id, "GET", "/responsibility/plans", READ, "list_responsibility_plans",
                       status=status, teamId=team_id, limit=100)
    return await _enrich(db, rows)


async def get_plan_async(db, user_id: int, team_id: int, plan_id: int) -> Dict[str, Any]:
    row = await _call(db, user_id, team_id, "GET", f"/responsibility/plans/{int(plan_id)}", READ, "get_responsibility_plan")
    return await _enrich(db, row)


async def summary_async(db, user_id: int, team_id: int) -> Dict[str, Any]:
    data = await _call(db, user_id, team_id, "GET", "/responsibility/summary", READ, "responsibility_summary", teamId=team_id)
    await _enrich(db, data)
    ids: set = set()
    for row in data.get("load", []):
        ids.add(row["userId"])
    for key in ("overdue", "waitingAccept", "blocked", "pendingReview"):
        for row in data.get(key, []):
            if row.get("responsibleUserId"):
                ids.add(row["responsibleUserId"])
            if row.get("reviewerUserId"):
                ids.add(row["reviewerUserId"])
            if row.get("waitingOn"):
                ids.add(row["waitingOn"])
    names = await user_names(db, list(ids))
    for row in data.get("load", []):
        row["name"] = names.get(row["userId"]) or f"用户 {row['userId']}"
    for key in ("overdue", "waitingAccept", "blocked", "pendingReview"):
        for row in data.get(key, []):
            row["responsibleName"] = names.get(row.get("responsibleUserId"))
            row["reviewerName"] = names.get(row.get("reviewerUserId"))
            row["waitingOnName"] = names.get(row.get("waitingOn"))
            row["statusLabel"] = STATUS_LABELS.get(row.get("status"), row.get("status"))
    data["narrative"] = narrative_for(data)
    return data


def narrative_for(summary: Dict[str, Any]) -> str:
    """部门周报文字：只陈述系统里的事实（数量、期限、状态），不评价员工，也不根据在线时长等数据推测绩效。"""
    week, by_status, metrics = summary.get("week", {}), summary.get("byStatus", {}), summary.get("metrics", {})
    parts = []
    if week.get("due"):
        parts.append(f"本周（{week['from']} 至 {week['to']}）到期的责任 {week['due']} 项，已验收完成 {week['done']} 项"
                     f"（其中按时 {week['doneOnTime']} 项）。")
    else:
        parts.append("本周没有到期的责任。")
    active = [f"{STATUS_LABELS[k]} {v} 项" for k, v in by_status.items() if v and k not in ("DONE", "CANCELLED")]
    parts.append("当前进行中的责任：" + ("、".join(active) if active else "无") + "。")
    if summary.get("overdueCount"):
        parts.append(f"已逾期 {summary['overdueCount']} 项，请关注。")
    waiting = summary.get("waitingAccept") or []
    if waiting:
        longest = max((w.get("hoursWaiting") or 0) for w in waiting)
        parts.append(f"{len(waiting)} 项还没有被员工接受（最久已等 {longest} 小时）。")
    if summary.get("blockedOver2Days"):
        parts.append(f"{summary['blockedOver2Days']} 项受阻超过两天，需要协调。")
    overloaded = [r["name"] for r in summary.get("load", []) if r.get("overloaded")]
    if overloaded:
        parts.append("同时承担较多责任的员工：" + "、".join(overloaded) + "，请确认是否需要调整。")
    facts = []
    if metrics.get("firstPassRate") is not None:
        facts.append(f"首次验收通过率 {metrics['firstPassRate']:g}%")
    if metrics.get("onTimeSubmitRate") is not None:
        facts.append(f"按时提交率 {metrics['onTimeSubmitRate']:g}%")
    if metrics.get("acceptMedianHours") is not None:
        facts.append(f"接受责任的中位时间 {metrics['acceptMedianHours']:g} 小时")
    if facts:
        parts.append("、".join(facts) + "。")
    return "".join(parts)


# ---------------------------------------------------------------- 写入

def task_payload(task: Dict[str, Any]) -> Dict[str, Any]:
    return {"title": task.get("title"), "responsibleUserId": task.get("responsible_user_id"),
            "collaboratorUserIds": list(task.get("collaborator_user_ids") or []),
            "reviewerUserId": task.get("reviewer_user_id"), "dueDate": task.get("due_date") or None,
            "deliverable": task.get("deliverable") or None, "acceptanceCriteria": task.get("acceptance_criteria") or None,
            "priority": task.get("priority") or "NORMAL", "evidence": task.get("evidence") or None,
            "dependsOnSeq": list(task.get("depends_on_seq") or []),
            "aiResponsibleUserId": task.get("ai_responsible_user_id")}


async def create_plan_async(db, user_id: int, team_id: int, body: Dict[str, Any],
                           idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    actor = await actor_for(db, user_id, team_id)
    if not body.get("tasks"):
        raise InvalidInput("没有可以生成的责任事项")
    payload = {"teamId": team_id, "title": body["title"], "sourceType": body.get("source_type") or "OTHER",
               "sourceText": body.get("source_text"), "summary": body.get("summary"),
               "decisions": body.get("decisions") or [], "unresolved": body.get("unresolved") or [],
               "tasks": [task_payload(t) for t in body["tasks"]], "automationWorkId": body.get("automation_work_id"),
               "eligible": await eligible_async(db, actor["organization_id"], team_id)}
    row = await _call(db, user_id, team_id, "POST", "/responsibility/plans", WRITE, "create_responsibility_plan", payload, True,
                      actor=actor, idempotency_key=idempotency_key)
    return await _enrich(db, row)


async def edit_plan_async(db, user_id: int, team_id: int, plan_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
    row = await _call(db, user_id, team_id, "POST", f"/responsibility/plans/{int(plan_id)}/edit", WRITE, "edit_responsibility_plan",
                      {"title": body.get("title"), "summary": body.get("summary"), "unresolved": body.get("unresolved") or []}, True)
    return await _enrich(db, row)


async def _plan_team(db, user_id: int, team_id: int, plan_id: int, actor: Dict[str, Any]) -> int:
    plan = await _call(db, user_id, team_id, "GET", f"/responsibility/plans/{int(plan_id)}", READ, "get_responsibility_plan", actor=actor)
    return int(plan["teamId"])


async def edit_task_async(db, user_id: int, team_id: int, task_id: int, task: Dict[str, Any]) -> Dict[str, Any]:
    actor = await actor_for(db, user_id, team_id)
    detail = await _call(db, user_id, team_id, "GET", f"/responsibility/tasks/{int(task_id)}", READ, "get_responsibility", actor=actor)
    eligible = await eligible_async(db, actor["organization_id"], int(detail["task"]["teamId"]))
    row = await _call(db, user_id, team_id, "POST", f"/responsibility/tasks/{int(task_id)}/edit", WRITE, "edit_responsibility_draft",
                      {"task": task_payload(task), "eligible": eligible}, True, actor=actor)
    return await _enrich(db, row)


async def add_task_async(db, user_id: int, team_id: int, plan_id: int, task: Dict[str, Any]) -> Dict[str, Any]:
    actor = await actor_for(db, user_id, team_id)
    plan_team = await _plan_team(db, user_id, team_id, plan_id, actor)
    eligible = await eligible_async(db, actor["organization_id"], plan_team)
    row = await _call(db, user_id, team_id, "POST", f"/responsibility/plans/{int(plan_id)}/tasks", WRITE, "add_responsibility_draft",
                      {"task": task_payload(task), "eligible": eligible}, True, actor=actor)
    return await _enrich(db, row)


async def remove_task_async(db, user_id: int, team_id: int, task_id: int) -> Dict[str, Any]:
    row = await _call(db, user_id, team_id, "POST", f"/responsibility/tasks/{int(task_id)}/remove", WRITE, "remove_responsibility_draft",
                      None, True)
    return await _enrich(db, row)


async def publish_async(db, user_id: int, team_id: int, plan_id: int, note: Optional[str] = None) -> Dict[str, Any]:
    """正式指派：只有部门负责人或企业管理员；此刻再算一次有效成员名单，Java 复核每个人仍然有效。"""
    actor = await actor_for(db, user_id, team_id)
    plan_team = await _plan_team(db, user_id, team_id, plan_id, actor)
    if plan_team not in actor["heads"]:
        raise PermissionDenied("只有部门负责人或企业管理员能正式指派责任")
    eligible = await eligible_async(db, actor["organization_id"], plan_team)
    row = await _call(db, user_id, team_id, "POST", f"/responsibility/plans/{int(plan_id)}/publish", WRITE, "publish_responsibility_plan",
                      {"eligible": eligible, "note": note}, True, actor=actor)
    return await _enrich(db, row)


async def cancel_plan_async(db, user_id: int, team_id: int, plan_id: int, reason: Optional[str]) -> Dict[str, Any]:
    row = await _call(db, user_id, team_id, "POST", f"/responsibility/plans/{int(plan_id)}/cancel", WRITE, "cancel_responsibility_plan",
                      {"reason": reason}, True)
    return await _enrich(db, row)


async def act_async(db, user_id: int, team_id: int, task_id: int, action: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if action not in ACTIONS:
        raise InvalidInput(f"未知的操作: {action}")
    actor = await actor_for(db, user_id, team_id)
    payload = {"note": body.get("note"), "reason": body.get("reason"), "percent": body.get("percent"),
               "proposedDate": body.get("proposed_date"), "approve": body.get("approve"),
               "waitingOnUserId": body.get("waiting_on_user_id"), "summary": body.get("summary"), "link": body.get("link"),
               "responsibleUserId": body.get("responsible_user_id"), "reviewerUserId": body.get("reviewer_user_id"),
               "dueDate": body.get("due_date"), "deliverable": body.get("deliverable"),
               "acceptanceCriteria": body.get("acceptance_criteria"), "collaboratorUserIds": body.get("collaborator_user_ids"),
               "priority": body.get("priority")}
    if action in NEEDS_ELIGIBLE:
        detail = await _call(db, user_id, team_id, "GET", f"/responsibility/tasks/{int(task_id)}", READ, "get_responsibility", actor=actor)
        payload["eligible"] = await eligible_async(db, actor["organization_id"], int(detail["task"]["teamId"]))
    row = await _call(db, user_id, team_id, "POST", f"/responsibility/tasks/{int(task_id)}/{action}", WRITE,
                      f"responsibility_{action.replace('-', '_')}", payload, True, actor=actor)
    return await _enrich(db, row)


async def team_tasks_for_reminders(db, head_user_id: int, team_id: int) -> List[Dict[str, Any]]:
    """提醒用：以部门负责人的身份读取本部门所有已发布的责任（不补姓名，提醒自己查）。"""
    return await _call(db, head_user_id, team_id, "GET", "/responsibility/tasks", READ, "responsibility_reminders",
                       view="team", teamId=team_id, limit=300)
