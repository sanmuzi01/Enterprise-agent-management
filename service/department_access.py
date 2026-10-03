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
}
MODULE_LABELS = {"procurement": "采购业务", "crm": "客户与商机（CRM）", "finance": "费用报销", "leave": "请假"}
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


def check_team_module(team_id: Optional[int], module: str) -> None:
    """同步版（Agent 工具用）；team_id 为空时不检查（企业管理员的跨部门场景）。失败抛 ValueError，
    符合工具层"错误以文字返回给 Agent"的约定。"""
    if team_id is None:
        return
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
