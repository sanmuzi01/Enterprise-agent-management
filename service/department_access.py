"""部门业务接口统一的访问校验：同一份规则同时用于 HTTP 接口、Agent 工具和 AI 工作成果。

一次校验包含四层，任何一层不满足都拒绝（前端隐藏入口只是体验，不是权限）：
1. 企业有效、本人企业成员身份有效；
2. 部门有效、本人部门成员身份有效（见 models/enterprise_dao.py 的成员 SQL）；
3. 部门的业务类型与业务模块一致——采购模块只给采购部门，CRM 只给销售部门；
   请假、报销是所有部门通用的办公事务。
"""
from typing import Dict, Optional, Set

from sqlalchemy import select, text

from models.init_db import Team
from service.exceptions import PermissionDenied

# 业务模块 → 允许使用的部门业务类型；None 表示所有部门。
MODULE_DEPARTMENTS: Dict[str, Optional[Set[str]]] = {
    "procurement": {"procurement"},
    "crm": {"sales"},
    "finance": None,
    "leave": None,
    "ticket": None,   # 提交 IT 工单是所有部门员工都能做的；处理工单/设备台账另见 department_staff_scope
    "hr_case": None,  # 人事事项涉及所有部门（员工、负责人、办理方），具体权限见 service/hr_service.py
}
MODULE_LABELS = {"procurement": "采购业务", "crm": "客户与商机（CRM）", "finance": "费用报销", "leave": "请假",
                 "ticket": "IT 服务工单", "hr_case": "人事事项"}
DEPARTMENT_LABELS = {"hr": "人事", "procurement": "采购", "sales": "销售", "finance": "财务", "it": "IT"}


def _module_error(module: str, department_code: Optional[str]) -> str:
    allowed = "、".join(DEPARTMENT_LABELS.get(c, c) for c in sorted(MODULE_DEPARTMENTS[module] or ()))
    current = DEPARTMENT_LABELS.get(department_code, "未设置业务类型") if department_code else "未设置业务类型"
    return f"「{MODULE_LABELS[module]}」仅对{allowed}部门开放，当前部门（{current}）不能使用"


def module_allows(module: str, department_code: Optional[str]) -> bool:
    allowed = MODULE_DEPARTMENTS.get(module)
    return allowed is None or department_code in allowed


async def check_team_module_async(db, team_id: int, module: str) -> Team:
    """部门存在、有效、所在企业有效，且业务类型允许使用该模块（不检查人员身份）。"""
    team = (await db.execute(
        text("SELECT t.id, t.name, t.department_code FROM teams t "
             "JOIN organizations o ON t.organization_id = o.id AND o.status = 'active' "
             "WHERE t.id = :t AND t.status = 'active'"), {"t": team_id})).first()
    if team is None:
        raise PermissionDenied("部门不存在、已停用，或所在企业已停用")
    if not module_allows(module, team.department_code):
        raise PermissionDenied(_module_error(module, team.department_code))
    return team


async def require_team_member_async(db, user_id: int, team_id: Optional[int], module: str,
                                    message: Optional[str] = None) -> None:
    """本人必须是该部门有效成员（含企业成员身份有效），且部门类型允许该模块。"""
    from models.enterprise_dao import is_team_member_of_team_async
    if not await is_team_member_of_team_async(db, user_id, team_id):
        raise PermissionDenied(message or "不属于该部门，或企业/部门/成员身份已失效，无法使用该部门业务")
    await check_team_module_async(db, team_id, module)


def check_team_module(team_id: Optional[int], module: str) -> Optional[str]:
    """同步版（Agent 工具用）；team_id 为空时不检查（企业管理员的跨部门场景）。失败抛 ValueError，
    符合工具层"错误以文字返回给 Agent"的约定。返回部门业务类型（可能为空），工具据此告诉业务系统走哪类科目。"""
    if team_id is None:
        return None
    from models.init_db import SessionLocal
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT t.department_code FROM teams t JOIN organizations o ON t.organization_id = o.id "
                 "AND o.status = 'active' WHERE t.id = :t AND t.status = 'active'"), {"t": team_id}).first()
    finally:
        db.close()
    if row is None:
        raise ValueError("部门不存在、已停用，或所在企业已停用")
    if not module_allows(module, row[0]):
        raise ValueError(_module_error(module, row[0]))
    return row[0]


async def department_staff_scope_async(db, user_id: int, team_id: int, department_code: str, feature: str) -> dict:
    """某类部门（财务、IT）的内部业务只给该类型部门的有效成员/企业管理员。

    返回 {"organization_id", "scope"}：scope 是同一企业的全部部门 id，写进已签名的 Java 请求路径，
    Java 再按它限定一次，所以即使 scope 被误给也看不到别的企业的数据。"""
    from models.enterprise_dao import is_team_member_of_team_async
    from service import enterprise_access
    team = (await db.execute(
        text("SELECT t.id, t.organization_id, t.department_code FROM teams t "
             "JOIN organizations o ON t.organization_id = o.id AND o.status = 'active' "
             "WHERE t.id = :t AND t.status = 'active'"), {"t": team_id})).first()
    if team is None:
        raise PermissionDenied("部门不存在、已停用，或所在企业已停用")
    label = DEPARTMENT_LABELS.get(department_code, department_code)
    if team.department_code != department_code:
        raise PermissionDenied(f"{feature}仅对{label}部门开放，当前部门不是{label}部门")
    if not await is_team_member_of_team_async(db, user_id, team_id) and not await enterprise_access.is_org_admin_async(db, user_id):
        raise PermissionDenied(f"不是该{label}部门的有效成员，无法使用{feature}")
    rows = (await db.execute(text("SELECT id FROM teams WHERE organization_id = :o ORDER BY id"),
                             {"o": team.organization_id})).all()
    return {"organization_id": int(team.organization_id), "scope": [int(row[0]) for row in rows]}


def department_staff_scope(user_id: int, team_id: int, department_code: str, feature: str) -> dict:
    """`department_staff_scope_async` 的同步版（Agent 工具用），规则一致；不满足时抛 PermissionDenied。"""
    from models.enterprise_dao import is_team_member_of_team
    from models.init_db import SessionLocal
    from service import enterprise_access
    db = SessionLocal()
    try:
        team = db.execute(
            text("SELECT t.id, t.organization_id, t.department_code FROM teams t "
                 "JOIN organizations o ON t.organization_id = o.id AND o.status = 'active' "
                 "WHERE t.id = :t AND t.status = 'active'"), {"t": team_id}).first()
        if team is None:
            raise PermissionDenied("部门不存在、已停用，或所在企业已停用")
        label = DEPARTMENT_LABELS.get(department_code, department_code)
        if team.department_code != department_code:
            raise PermissionDenied(f"{feature}仅对{label}部门开放，当前部门不是{label}部门")
        if not is_team_member_of_team(db, user_id, team_id) and not enterprise_access.is_org_admin(db, user_id):
            raise PermissionDenied(f"不是该{label}部门的有效成员，无法使用{feature}")
        rows = db.execute(text("SELECT id FROM teams WHERE organization_id = :o ORDER BY id"),
                          {"o": team.organization_id}).all()
        return {"organization_id": int(team.organization_id), "scope": [int(row[0]) for row in rows]}
    finally:
        db.close()
