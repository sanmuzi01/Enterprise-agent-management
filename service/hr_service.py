"""人事入转调离：HR 发起 → 规则检查 → 员工所在部门负责人批准 → 跨部门办理清单 → HR 办结 → 落实系统变更。

调用者身份在这里计算（企业/部门/成员身份都必须有效），以 scopeTeamIds / roles / headTeamIds 写进已签名的
Java 请求路径（见 enterprise-business-hub 的 HrActor）：
- scope：调用者所在企业的全部部门；
- roles：调用者作为人事/IT/财务类型部门有效成员持有的办理角色（HR/IT/FINANCE）；
- headTeamIds：调用者作为负责人的部门；企业管理员视为全部部门的负责人。
Java 只信这三个集合，不自己判断；所以这里算错就是越权——算法集中在 actor_for，测试逐项覆盖。
"""
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from models.enterprise_dao import is_team_admin_of_team_async
from service import audit_service, enterprise_access
from service.department_access import require_team_member_async
from service.exceptions import InvalidInput, PermissionDenied
from service.hub_gateway import call_hub
from service.name_lookup import team_names, user_names

READ = ["hr.case.read"]
WRITE = ["hr.case.read", "hr.case.write"]
ROLE_OF_DEPARTMENT = {"hr": "HR", "it": "IT", "finance": "FINANCE"}
OWNER_LABELS = {"HR": "人事", "IT": "IT", "FINANCE": "财务", "MANAGER": "部门负责人", "EMPLOYEE": "员工本人"}
STATUS_LABELS = {"PENDING_APPROVAL": "待负责人批准", "IN_PROGRESS": "办理中", "COMPLETED": "已办结",
                 "REJECTED": "未获批准", "CANCELLED": "已撤销"}


_TEAMS_SQL = "SELECT id FROM teams WHERE organization_id = :o AND status = 'active' ORDER BY id"
_MEMBERSHIP_SQL = (
    "SELECT t.id, t.department_code, er.code FROM team_members tm "
    "JOIN teams t ON t.id = tm.team_id AND t.status = 'active' AND t.organization_id = :o "
    "JOIN organization_members om ON om.organization_id = t.organization_id AND om.user_id = tm.user_id AND om.status = 'active' "
    "JOIN enterprise_role er ON er.id = tm.role_id "
    "WHERE tm.user_id = :u AND tm.status = 'active'")


def _build_actor(org_id, teams, rows, org_admin: bool) -> Dict[str, Any]:
    scope = [int(r[0]) for r in teams]
    roles = sorted({ROLE_OF_DEPARTMENT[r[1]] for r in rows if r[1] in ROLE_OF_DEPARTMENT})
    heads = scope if org_admin else sorted({int(r[0]) for r in rows if r[2] == "admin"})
    return {"organization_id": int(org_id), "scope": scope, "roles": roles, "heads": heads, "org_admin": org_admin}


async def actor_for(db, user_id: int, team_id: int) -> Dict[str, Any]:
    """以当前所在部门（team_id）所属的企业为范围，计算调用者在人事事项里的身份。"""
    await require_team_member_async(db, user_id, team_id, "hr_case", message="不属于该部门，或企业/部门/成员身份已失效")
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


def scoped(path: str, actor: Dict[str, Any], **params: Any) -> str:
    return f"{path}?{_query(actor, **params)}"


def _query(actor: Dict[str, Any], **params: Any) -> str:
    parts = ["scopeTeamIds=" + ",".join(str(t) for t in actor["scope"])]
    if actor["roles"]:
        parts.append("roles=" + ",".join(actor["roles"]))
    if actor["heads"]:
        parts.append("headTeamIds=" + ",".join(str(t) for t in actor["heads"]))
    from urllib.parse import urlencode
    extra = {k: v for k, v in params.items() if v is not None}
    return "&".join(parts) + ("&" + urlencode(extra) if extra else "")


async def _call(db, user_id: int, team_id: int, method: str, path: str, scopes: List[str], operation: str,
                body: Optional[Dict[str, Any]] = None, write: bool = False, actor: Optional[Dict[str, Any]] = None,
                **params: Any) -> Any:
    actor = actor or await actor_for(db, user_id, team_id)
    return await call_hub(method, f"{path}?{_query(actor, **params)}", user_id, team_id, scopes, operation, body, write)


async def _enrich(db, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    users = await user_names(db, [i for r in rows for i in (r.get("employeeUserId"), r.get("initiatorUserId"), r.get("approverUserId"))]
                             + [t.get("doneBy") for r in rows for t in r.get("tasks", [])])
    teams = await team_names(db, [i for r in rows for i in (r.get("teamId"), r.get("targetTeamId"))])
    for row in rows:
        row["employeeName"] = users.get(row.get("employeeUserId"))
        row["initiatorName"] = users.get(row.get("initiatorUserId"))
        row["approverName"] = users.get(row.get("approverUserId"))
        row["teamName"] = teams.get(row.get("teamId"))
        row["targetTeamName"] = teams.get(row.get("targetTeamId"))
        row["statusLabel"] = STATUS_LABELS.get(row.get("status"), row.get("status"))
        for task in row.get("tasks", []):
            task["ownerLabel"] = OWNER_LABELS.get(task["owner"], task["owner"])
            task["doneByName"] = users.get(task.get("doneBy"))
        if "owner" in row:
            row["ownerLabel"] = OWNER_LABELS.get(row["owner"], row["owner"])
    return rows


async def _validated_body(db, actor: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """员工必须是该部门的有效成员；调岗目标部门必须是同企业的有效部门。员工是否负责人由这里算，不信任前端。"""
    employee, team = int(body["employee_user_id"]), int(body["team_id"])
    if team not in actor["scope"]:
        raise PermissionDenied("部门不在你所在企业内")
    member = (await db.execute(text(
        "SELECT 1 FROM team_members tm JOIN organization_members om ON om.user_id = tm.user_id "
        "AND om.organization_id = :o AND om.status = 'active' WHERE tm.team_id = :t AND tm.user_id = :u AND tm.status = 'active'"),
        {"o": actor["organization_id"], "t": team, "u": employee})).first()
    if member is None:
        raise InvalidInput("该员工不是这个部门的有效成员；入职请先由管理员把员工加入部门")
    target = body.get("target_team_id")
    if body["case_type"] == "TRANSFER":
        if target is None:
            raise InvalidInput("调岗需要选择目标部门")
        if int(target) not in actor["scope"]:
            raise InvalidInput("目标部门不存在或已停用")
    return {"caseType": body["case_type"], "employeeUserId": employee, "teamId": team,
            "targetTeamId": int(target) if body["case_type"] == "TRANSFER" else None,
            "position": body.get("position"), "effectiveDate": body["effective_date"], "reason": body.get("reason"),
            "employeeIsHead": await is_team_admin_of_team_async(db, employee, team)}


# ---------------------------------------------------------------- 查询

async def actor_summary_async(db, user_id: int, team_id: int) -> Dict[str, Any]:
    actor = await actor_for(db, user_id, team_id)
    return {"roles": actor["roles"], "is_hr": "HR" in actor["roles"], "is_head": bool(actor["heads"]), "org_admin": actor["org_admin"]}


async def list_cases_async(db, user_id: int, team_id: int, view: str = "hr", status: Optional[str] = None,
                           case_type: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = await _call(db, user_id, team_id, "GET", "/hr/cases", READ, "list_hr_cases", view=view, status=status, type=case_type)
    return await _enrich(db, rows)


async def get_case_async(db, user_id: int, team_id: int, case_id: int) -> Dict[str, Any]:
    row = await _call(db, user_id, team_id, "GET", f"/hr/cases/{int(case_id)}", READ, "get_hr_case")
    return (await _enrich(db, [row]))[0]


async def my_tasks_async(db, user_id: int, team_id: int) -> List[Dict[str, Any]]:
    rows = await _call(db, user_id, team_id, "GET", "/hr/cases/my-tasks", READ, "my_hr_tasks")
    return await _enrich(db, rows)


async def summary_async(db, user_id: int, team_id: int) -> Dict[str, Any]:
    data = await _call(db, user_id, team_id, "GET", "/hr/cases/summary", READ, "hr_case_summary")
    data["narrative"] = narrative_for(data)
    return data


def narrative_for(summary: Dict[str, Any]) -> str:
    labels = {"ONBOARDING": "入职", "PROBATION": "转正", "TRANSFER": "调岗", "OFFBOARDING": "离职"}
    by_type = summary.get("byType", {})
    active = [f"{labels[k]} {v.get('PENDING_APPROVAL', 0) + v.get('IN_PROGRESS', 0)} 件" for k, v in by_type.items()
              if v.get("PENDING_APPROVAL", 0) + v.get("IN_PROGRESS", 0)]
    parts = ["进行中的人事事项：" + ("、".join(active) if active else "无") + "。"]
    done = sum(v.get("COMPLETED", 0) for v in by_type.values())
    if done:
        avg = summary.get("avgDaysToComplete")
        parts.append(f"已办结 {done} 件" + (f"，平均 {avg:g} 天办完。" if avg is not None else "。"))
    if summary.get("overdueTasks"):
        parts.append(f"有 {summary['overdueTasks']} 项办理任务已过期限，请催办。")
    if summary.get("effectPending"):
        parts.append(f"{summary['effectPending']} 件调岗/离职已办结但系统变更还没落实（部门归属或账号停用），请企业管理员处理。")
    return "".join(parts)


# ---------------------------------------------------------------- 发起与流转

async def precheck_async(db, user_id: int, team_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
    actor = await actor_for(db, user_id, team_id)
    payload = await _validated_body(db, actor, body)
    return await _call(db, user_id, team_id, "POST", "/hr/cases/precheck", READ, "precheck_hr_case", payload, actor=actor)


async def create_case_async(db, user_id: int, team_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
    actor = await actor_for(db, user_id, team_id)
    if "HR" not in actor["roles"]:
        raise PermissionDenied("只有人事部门成员能发起人事事项")
    payload = await _validated_body(db, actor, body)
    row = await _call(db, user_id, team_id, "POST", "/hr/cases", WRITE, "create_hr_case", payload, True, actor=actor)
    return (await _enrich(db, [row]))[0]


async def act_async(db, user_id: int, team_id: int, case_id: int, action: str, note: Optional[str] = None) -> Dict[str, Any]:
    if action not in ("approve", "reject", "cancel", "complete", "recheck"):
        raise InvalidInput(f"未知的操作: {action}")
    row = await _call(db, user_id, team_id, "POST", f"/hr/cases/{int(case_id)}/{action}", WRITE, f"{action}_hr_case",
                      {"note": note}, action != "recheck")
    return (await _enrich(db, [row]))[0]


async def finish_task_async(db, user_id: int, team_id: int, case_id: int, task_id: int, result: str,
                            note: Optional[str]) -> Dict[str, Any]:
    if result not in ("done", "skip"):
        raise InvalidInput(f"未知的操作: {result}")
    row = await _call(db, user_id, team_id, "POST", f"/hr/cases/{int(case_id)}/tasks/{int(task_id)}/{result}", WRITE,
                      f"{result}_hr_task", {"note": note}, True)
    return (await _enrich(db, [row]))[0]


async def apply_effect_async(db, user_id: int, team_id: int, case_id: int) -> Dict[str, Any]:
    """落实系统变更：调岗把员工从原部门移到目标部门；离职停用员工的企业成员身份（所有部门权限随之失效）。
    这是改权限的操作，只给企业管理员；先改本库，再通知业务系统标记已落实——后一步失败时可以重试，
    本库的变更是幂等的（再执行一次结果相同）。"""
    actor = await actor_for(db, user_id, team_id)
    if not actor["org_admin"]:
        raise PermissionDenied("落实部门归属和账号停用需要企业管理员操作")
    case = await _call(db, user_id, team_id, "GET", f"/hr/cases/{int(case_id)}", READ, "get_hr_case", actor=actor)
    if case["status"] != "COMPLETED" or not case["effectPending"]:
        raise InvalidInput("这个事项没有待落实的系统变更")
    employee = int(case["employeeUserId"])
    if employee == user_id:
        raise InvalidInput("不能为自己落实调岗或离职，请由其他管理员操作")
    if case["caseType"] == "TRANSFER":
        member_role = (await db.execute(text("SELECT id FROM enterprise_role WHERE scope = 'team' AND code = 'member'"))).scalar()
        # 一人一部门：锁住员工，再把唯一一条部门归属原地调到目标部门。旧实现只删
        # case 里的来源部门，历史上若已有第三个部门归属会继续残留。
        await db.execute(text("SELECT id FROM `user` WHERE id = :u FOR UPDATE"), {"u": employee})
        existing = (await db.execute(text("SELECT id FROM team_members WHERE user_id = :u FOR UPDATE"),
                                     {"u": employee})).first()
        if existing:
            await db.execute(text("UPDATE team_members SET team_id = :t, role_id = :r, status = 'active', "
                                  "updated_at = UTC_TIMESTAMP() WHERE id = :i"),
                             {"t": case["targetTeamId"], "r": member_role, "i": existing[0]})
        else:
            await db.execute(text("INSERT INTO team_members (team_id, user_id, role_id, status, created_at, updated_at) "
                                  "VALUES (:t, :u, :r, 'active', UTC_TIMESTAMP(), UTC_TIMESTAMP())"),
                             {"t": case["targetTeamId"], "u": employee, "r": member_role})
        detail = {"case_id": case_id, "from_team": case["teamId"], "to_team": case["targetTeamId"]}
    else:
        role = (await db.execute(text(
            "SELECT er.code FROM organization_members om JOIN enterprise_role er ON er.id = om.role_id "
            "WHERE om.organization_id = :o AND om.user_id = :u"), {"o": actor["organization_id"], "u": employee})).scalar()
        if role in ("owner", "admin"):
            raise InvalidInput("该员工是企业管理员/所有者，请先在企业管理里转交管理权限再停用")
        await db.execute(text("UPDATE organization_members SET status = 'disabled' WHERE organization_id = :o AND user_id = :u"),
                         {"o": actor["organization_id"], "u": employee})
        detail = {"case_id": case_id, "disabled_user": employee}
    await db.commit()
    await audit_service.record_async(user_id, f"hr.effect_{case['caseType'].lower()}", resource_type="hr_case",
                                     resource_id=case_id, detail=detail)
    row = await _call(db, user_id, team_id, "POST", f"/hr/cases/{int(case_id)}/effect-applied", WRITE, "hr_effect_applied",
                      {"note": None}, True, actor=actor)
    return (await _enrich(db, [row]))[0]


async def candidates_async(db, user_id: int, team_id: int) -> Dict[str, Any]:
    """发起事项时的下拉选项：本企业的有效部门与各部门成员（只给 HR）。"""
    actor = await actor_for(db, user_id, team_id)
    if "HR" not in actor["roles"]:
        raise PermissionDenied("只有人事部门成员能发起人事事项")
    teams = (await db.execute(text("SELECT id, name, department_code FROM teams WHERE organization_id = :o AND status = 'active' ORDER BY id"),
                              {"o": actor["organization_id"]})).all()
    members = (await db.execute(text(
        "SELECT tm.team_id, u.id, u.name, er.code FROM team_members tm JOIN teams t ON t.id = tm.team_id AND t.organization_id = :o "
        "JOIN `user` u ON u.id = tm.user_id JOIN enterprise_role er ON er.id = tm.role_id "
        "JOIN organization_members om ON om.organization_id = :o AND om.user_id = tm.user_id AND om.status = 'active' "
        "WHERE tm.status = 'active' ORDER BY tm.team_id, u.id"), {"o": actor["organization_id"]})).all()
    return {"teams": [{"id": int(t[0]), "name": t[1], "department_code": t[2]} for t in teams],
            "members": [{"team_id": int(m[0]), "user_id": int(m[1]), "name": m[2], "is_head": m[3] == "admin"} for m in members]}


async def team_pending_approval_async(db, user_id: int, team_id: int) -> List[Dict[str, Any]]:
    """该部门里等待负责人批准的人事事项（提醒用；只返回调用者有权批准的）。"""
    rows = await list_cases_async(db, user_id, team_id, view="approval")
    return [r for r in rows if r.get("teamId") == team_id]
