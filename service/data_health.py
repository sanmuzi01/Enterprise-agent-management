"""数据体检：找出库里已经存在的脏数据和前后不一致的数据。

文件导入有逐行校验（考勤、资料导入），但数据库里“已经在那儿”的问题以前没有系统检查，都是碰到了才手工查、手工修：
多出来的企业记录、没加入企业的用户、测试账号残留、部门助手配置过旧、无主的知识库……这些都会在页面上表现成
“这个人看不到部门助手”“这个知识库谁都看不到”之类的现象，很难从现象倒推回数据。

每一项检查：
  - 只读，给出数量和几条样例（ID + 名称），说明怎么处理；
  - severity：error（功能会出错，要尽快处理）/ warn（数据不一致，会让某些人看不到东西）/ info（不影响使用，建议清理）；
  - fixable：只有“补数据、不删数据、不需要人判断”的才能自动修（--fix），修完写审计；
    需要人判断的（该保留哪家企业、要不要删账号、已停用部门的助手怎么办）只报告、给出处理方法。
"""
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from sqlalchemy import text

SAMPLE_LIMIT = 5


@dataclass
class Finding:
    code: str
    title: str
    severity: str            # error / warn / info
    count: int
    samples: List[str] = field(default_factory=list)
    advice: str = ""
    fixable: bool = False

    def to_dict(self) -> Dict:
        return asdict(self)


def _enterprise_id(db) -> Optional[int]:
    return db.execute(text("SELECT id FROM organizations ORDER BY id LIMIT 1")).scalar()


def _rows(db, sql: str, **params) -> list:
    return db.execute(text(sql), params).all()


def _finding(code, title, severity, rows, advice, fixable=False, fmt=lambda r: f"#{r[0]} {r[1]}") -> Optional[Finding]:
    if not rows:
        return None
    return Finding(code, title, severity, len(rows), [fmt(r) for r in rows[:SAMPLE_LIMIT]], advice, fixable)


# ---------------------------------------------------------------- 检查项

def check_enterprise(db) -> Optional[Finding]:
    rows = _rows(db, "SELECT id, name FROM organizations ORDER BY id")
    if not rows:
        return Finding("enterprise_missing", "还没有企业记录", "error", 0, [],
                       "服务启动时会自动建好（AUTO_CREATE_ENTERPRISE=1，默认开启）；关掉了，或者以前自动建过、后来企业记录被删了（不会自动重建），"
                       "手动跑 scripts/backfill_default_organization.py --yes")
    if len(rows) > 1:
        return Finding("enterprise_multiple", "有多条企业记录（平台只服务一家企业）", "error", len(rows),
                       [f"#{r[0]} {r[1]}" for r in rows[:SAMPLE_LIMIT]],
                       "id 最小的那条是在用的企业。其余的要人工确认后合并或删除：先备份，把它们下面的部门、成员、知识库、智能体迁到在用的企业，再删记录")
    return None


def check_users_not_in_enterprise(db) -> Optional[Finding]:
    org = _enterprise_id(db)
    if org is None:
        return None
    rows = _rows(db, """
        SELECT u.id, u.name FROM `user` u
        LEFT JOIN organization_members om ON om.user_id = u.id AND om.organization_id = :o
        WHERE om.id IS NULL AND COALESCE(u.is_disabled, 0) = 0 ORDER BY u.id""", o=org)
    return _finding("users_not_in_enterprise", "在用的账号没有加入企业", "warn", rows,
                    "这些人看不到部门助手、部门知识库，也不能被分进部门。--fix 会把他们加为企业成员（平台管理员加为所有者）", fixable=True)


def check_department_members_without_enterprise(db) -> Optional[Finding]:
    rows = _rows(db, """
        SELECT tm.user_id, CONCAT(u.name, ' @ ', t.name) FROM team_members tm
        JOIN teams t ON t.id = tm.team_id JOIN `user` u ON u.id = tm.user_id
        LEFT JOIN organization_members om ON om.user_id = tm.user_id AND om.organization_id = t.organization_id AND om.status = 'active'
        WHERE tm.status = 'active' AND om.id IS NULL ORDER BY tm.user_id""")
    return _finding("department_members_without_enterprise", "部门里的成员在企业里已停用或不存在", "warn", rows,
                    "部门权限以有效的企业成员身份为前提，这些人在部门里其实什么都用不了。确认是离职就在「组织架构」里移出部门，"
                    "误停用就在「用户管理」里恢复企业角色")


def check_published_agents_on_disabled_teams(db) -> Optional[Finding]:
    rows = _rows(db, """
        SELECT a.id, CONCAT(a.name, ' @ ', t.name) FROM agent a JOIN teams t ON t.id = a.team_id
        WHERE a.lifecycle_status = 'published' AND t.status <> 'active' ORDER BY a.id""")
    return _finding("published_agents_on_disabled_teams", "已停用部门的智能体仍是已发布状态", "error", rows,
                    "停用部门时本应同步停用它的智能体。在「企业智能体」里把它们停用，或先启用部门")


def check_department_agents_outdated(db) -> Optional[Finding]:
    from models.init_db import Agent
    from service import agent_admin_service
    from service.enterprise_agent_templates import get_template, template_id_for_department
    outdated = []
    for agent_id, team_name, code in _rows(db, """
            SELECT a.id, t.name, t.department_code FROM agent a JOIN teams t ON t.id = a.team_id
            WHERE a.agent_type = 'department' AND a.lifecycle_status <> 'retired' AND t.status = 'active'
              AND (a.department_code <=> t.department_code) ORDER BY a.id"""):
        try:
            template = get_template(template_id_for_department(code))
        except Exception:  # noqa: BLE001 —— 未知的业务类型交给别的检查
            continue
        missing = agent_admin_service.template_skill_missing_tools(db.get(Agent, agent_id), template)
        if missing:
            outdated.append((agent_id, f"{team_name}：缺 {len(missing)} 项能力"))
    return _finding("department_agents_outdated", "部门助手缺少模板里新增的能力", "warn", outdated,
                    "老助手是按当时的模板建的。在「组织架构」里选中部门，点“一键修复”补上（原有配置保留，会写审计）")


def check_spaces_without_enterprise(db) -> Optional[Finding]:
    if _enterprise_id(db) is None:
        return None
    rows = _rows(db, "SELECT id, name FROM knowledge_spaces WHERE organization_id IS NULL ORDER BY id")
    return _finding("spaces_without_enterprise", "知识库空间没有归属企业", "warn", rows,
                    "划分给部门 / 全企业时会出问题。--fix 会把它们挂到企业下（只改归属，不改划分和成员）", fixable=True)


def check_orphan_skill_configs(db) -> Optional[Finding]:
    from service.skills import loader as skill_loader
    folder = os.path.join(skill_loader.SKILLS_ROOT, "enterprise")
    if not os.path.isdir(folder):
        return None
    ids = []
    for name in os.listdir(folder):
        m = re.fullmatch(r"agent_(\d+)\.yml", name)
        if m:
            ids.append(int(m.group(1)))
    if not ids:
        return None
    existing = {r[0] for r in db.execute(text("SELECT id FROM agent WHERE id IN :ids").bindparams(
        __import__("sqlalchemy").bindparam("ids", expanding=True)), {"ids": ids}).all()}
    orphans = sorted(set(ids) - existing)
    return _finding("orphan_skill_configs", "专业技能配置文件对应的智能体已经不存在", "info",
                    [(i, f"skills/enterprise/agent_{i}.yml") for i in orphans],
                    "不影响使用。确认这些智能体是被删除的（不是数据库还没恢复完）之后，可以把文件移走归档",
                    fmt=lambda r: r[1])


def check_orphan_private_skills(db) -> Optional[Finding]:
    """企业助手专属的“专业业务技能”记录，对应的助手已经被删了（以前删助手只解绑不删，见 agent_service.delete）。"""
    rows = _rows(db, r"""
        SELECT s.id, s.name FROM skill s
        WHERE s.config_file LIKE 'enterprise/agent\_%.yml'
          AND NOT EXISTS (SELECT 1 FROM agent a WHERE s.config_file = CONCAT('enterprise/agent_', a.id, '.yml'))
        ORDER BY s.id""")
    return _finding("orphan_private_skills", "没有主人的助手专属技能记录", "info", rows,
                    "删除助手时遗留的，会出现在技能列表里但没有任何助手使用。确认后在「技能管理」里删除，或用 SQL 删除这些 skill 及其 skill_version")


def check_test_accounts(db) -> Optional[Finding]:
    rows = _rows(db, r"SELECT id, name FROM `user` WHERE name LIKE 'rt\_%' ORDER BY id")
    return _finding("test_accounts_left", "残留的自动化测试账号", "info", rows,
                    "测试中途被打断会留下 rt_ 开头的账号。生产库里不应该有；确认后用 scripts/purge_test_users.py 清理")


CHECKS: List[Callable] = [
    check_enterprise, check_users_not_in_enterprise, check_department_members_without_enterprise,
    check_published_agents_on_disabled_teams, check_department_agents_outdated, check_spaces_without_enterprise,
    check_orphan_skill_configs, check_orphan_private_skills, check_test_accounts,
]


def run_checks(db) -> List[Finding]:
    findings = []
    for check in CHECKS:
        result = check(db)
        if result is not None:
            findings.append(result)
    order = {"error": 0, "warn": 1, "info": 2}
    return sorted(findings, key=lambda f: order.get(f.severity, 3))


# ---------------------------------------------------------------- 自动修复（只补数据，不删数据）

def fix_users_not_in_enterprise(db) -> int:
    from models.init_db import User
    from service.admin_service import is_admin_user
    org = _enterprise_id(db)
    if org is None:
        return 0
    roles = dict(_rows(db, "SELECT code, id FROM enterprise_role WHERE scope='organization' AND code IN ('owner', 'member')"))
    fixed = 0
    for uid, _name in _rows(db, """
            SELECT u.id, u.name FROM `user` u
            LEFT JOIN organization_members om ON om.user_id = u.id AND om.organization_id = :o
            WHERE om.id IS NULL AND COALESCE(u.is_disabled, 0) = 0""", o=org):
        role = roles["owner"] if is_admin_user(db.get(User, uid)) else roles["member"]
        db.execute(text("INSERT INTO organization_members (organization_id, user_id, role_id, status, created_at, updated_at) "
                        "VALUES (:o, :u, :r, 'active', NOW(), NOW())"), {"o": org, "u": uid, "r": role})
        fixed += 1
    return fixed


def fix_spaces_without_enterprise(db) -> int:
    org = _enterprise_id(db)
    if org is None:
        return 0
    return int(db.execute(text("UPDATE knowledge_spaces SET organization_id = :o WHERE organization_id IS NULL"), {"o": org}).rowcount or 0)


FIXES: Dict[str, Callable] = {
    "users_not_in_enterprise": fix_users_not_in_enterprise,
    "spaces_without_enterprise": fix_spaces_without_enterprise,
}


def apply_fixes(db, findings: List[Finding], operator_id: int = 0) -> Dict[str, int]:
    """只修 fixable 的项；整体一个事务，修完写一条审计（谁、修了哪几类、各多少条）。"""
    from service import audit_service
    result = {}
    for finding in findings:
        fix = FIXES.get(finding.code)
        if finding.fixable and fix:
            result[finding.code] = fix(db)
    db.commit()
    if result:
        audit_service.record(operator_id, "data_health.fixed", resource_type="database", detail=result)
    return result
