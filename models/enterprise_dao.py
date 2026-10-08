"""企业/部门成员关系的查询（Phase 3B，docs/enterprise-rbac-plan.md）。

两类调用方：
- service/access_control.py：knowledge_spaces 的可见性计算要不要把"部门管理员"算进去，
  只读，别处不要直接查这几张表，避免"谁是部门管理员"的判定逻辑散落在多个地方。
- service/auth_service.py / auth_async_service.py 的 register()：新用户自动加入默认企业。
"""
from typing import List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

# 跟 scripts/backfill_default_organization.py 用的是同一个名字——那个脚本从这里导入，
# 不要在两处各写一份，写歪了两边就对不上默认企业到底是哪一行。
DEFAULT_ORG_NAME = "默认企业"

# 部门成员/部门负责人的有效性 = 部门成员记录有效 + 部门有效 + 所在企业有效 + 本人的企业成员身份有效。
# 企业成员被停用、被移出企业或企业被停用后，残留的部门成员记录不能继续给出任何部门权限。
_TEAM_ADMIN_OF_TEAM_SQL = (
    "SELECT 1 FROM team_members tm "
    "JOIN teams t ON tm.team_id = t.id "
    "JOIN organizations o ON t.organization_id = o.id AND o.status = 'active' "
    "JOIN organization_members om ON om.organization_id = t.organization_id "
    "AND om.user_id = tm.user_id AND om.status = 'active' "
    "JOIN enterprise_role er ON tm.role_id = er.id "
    "WHERE tm.user_id = :uid AND tm.team_id = :tid AND tm.status = 'active' "
    "AND er.scope = 'team' AND er.code = 'admin' AND t.status = 'active' LIMIT 1"
)

# 不限角色等级：任意在职部门成员即可，用于"部门Agent/Skill 对本部门所有人可用"这类
# 使用类（而非管理类）判断——跟上面的"部门管理员"是两个不同的问题。
_TEAM_MEMBER_OF_TEAM_SQL = (
    "SELECT 1 FROM team_members tm JOIN teams t ON tm.team_id = t.id "
    "JOIN organizations o ON t.organization_id = o.id AND o.status = 'active' "
    "JOIN organization_members om ON om.organization_id = t.organization_id "
    "AND om.user_id = tm.user_id AND om.status = 'active' "
    "WHERE tm.user_id = :uid AND tm.team_id = :tid "
    "AND tm.status = 'active' AND t.status = 'active' LIMIT 1"
)

# 同理：任意在职企业成员，用于"企业级Agent/Skill 对全企业可用"。
_ORG_MEMBER_SQL = (
    "SELECT 1 FROM organization_members om JOIN organizations o ON om.organization_id = o.id "
    "WHERE om.user_id = :uid AND om.status = 'active' AND o.status = 'active' LIMIT 1"
)

_SPACE_IDS_WHERE_TEAM_ADMIN_SQL = (
    "SELECT ks.id FROM knowledge_spaces ks "
    "JOIN team_members tm ON tm.team_id = ks.team_id "
    "JOIN teams t ON tm.team_id = t.id "
    "JOIN organizations o ON t.organization_id = o.id AND o.status = 'active' "
    "JOIN organization_members om ON om.organization_id = t.organization_id "
    "AND om.user_id = tm.user_id AND om.status = 'active' "
    "JOIN enterprise_role er ON tm.role_id = er.id "
    "WHERE tm.user_id = :uid AND tm.status = 'active' "
    "AND er.scope = 'team' AND er.code = 'admin' AND t.status = 'active'"
)


# ---------------- 同步 ----------------

def is_team_admin_of_team(db, user_id: int, team_id: Optional[int]) -> bool:
    if team_id is None:
        return False
    return db.execute(text(_TEAM_ADMIN_OF_TEAM_SQL), {"uid": user_id, "tid": team_id}).first() is not None


def is_team_member_of_team(db, user_id: int, team_id: Optional[int]) -> bool:
    if team_id is None:
        return False
    return db.execute(text(_TEAM_MEMBER_OF_TEAM_SQL), {"uid": user_id, "tid": team_id}).first() is not None


def is_org_member(db, user_id: int) -> bool:
    return db.execute(text(_ORG_MEMBER_SQL), {"uid": user_id}).first() is not None


def list_space_ids_where_team_admin(db, user_id: int) -> List[int]:
    rows = db.execute(text(_SPACE_IDS_WHERE_TEAM_ADMIN_SQL), {"uid": user_id}).all()
    return [r[0] for r in rows]


def enroll_in_default_organization(db, user_id: int) -> None:
    """新用户注册时自动加入默认企业（role=member）。

    找不到默认企业（还没跑过 scripts/backfill_default_organization.py——全新部署、
    CI、大部分测试库都是这样）就直接跳过，不抛异常：这是尽力而为的补全，不是注册
    流程的硬依赖，找不到不代表注册应该失败。出错也不让调用方跟着回滚——注册本身
    的事务不该被这一步拖累，独立 commit/rollback。
    """
    try:
        org_id = db.execute(
            text("SELECT id FROM organizations WHERE name=:n ORDER BY id LIMIT 1"),
            {"n": DEFAULT_ORG_NAME},
        ).scalar()
        if org_id is None:
            return
        member_role_id = db.execute(
            text("SELECT id FROM enterprise_role WHERE scope='organization' AND code='member'")
        ).scalar()
        if member_role_id is None:
            return
        db.execute(
            text(
                "INSERT INTO organization_members "
                "(organization_id, user_id, role_id, status, created_at, updated_at) "
                "VALUES (:org, :uid, :role, 'active', NOW(), NOW())"
            ),
            {"org": org_id, "uid": user_id, "role": member_role_id},
        )
        db.commit()
    except Exception:  # noqa: BLE001 —— 补全失败不影响注册主流程
        db.rollback()


# ---------------- 异步 ----------------

async def is_team_admin_of_team_async(db: AsyncSession, user_id: int, team_id: Optional[int]) -> bool:
    if team_id is None:
        return False
    res = await db.execute(text(_TEAM_ADMIN_OF_TEAM_SQL), {"uid": user_id, "tid": team_id})
    return res.first() is not None


async def is_team_member_of_team_async(db: AsyncSession, user_id: int, team_id: Optional[int]) -> bool:
    if team_id is None:
        return False
    res = await db.execute(text(_TEAM_MEMBER_OF_TEAM_SQL), {"uid": user_id, "tid": team_id})
    return res.first() is not None


async def is_org_member_async(db: AsyncSession, user_id: int) -> bool:
    res = await db.execute(text(_ORG_MEMBER_SQL), {"uid": user_id})
    return res.first() is not None


async def list_space_ids_where_team_admin_async(db: AsyncSession, user_id: int) -> List[int]:
    res = await db.execute(text(_SPACE_IDS_WHERE_TEAM_ADMIN_SQL), {"uid": user_id})
    return [row[0] for row in res.all()]


async def enroll_in_default_organization_async(db: AsyncSession, user_id: int) -> None:
    """同 enroll_in_default_organization，异步注册流程用。"""
    try:
        org_id = (
            await db.execute(
                text("SELECT id FROM organizations WHERE name=:n ORDER BY id LIMIT 1"),
                {"n": DEFAULT_ORG_NAME},
            )
        ).scalar()
        if org_id is None:
            return
        member_role_id = (
            await db.execute(text("SELECT id FROM enterprise_role WHERE scope='organization' AND code='member'"))
        ).scalar()
        if member_role_id is None:
            return
        await db.execute(
            text(
                "INSERT INTO organization_members "
                "(organization_id, user_id, role_id, status, created_at, updated_at) "
                "VALUES (:org, :uid, :role, 'active', NOW(), NOW())"
            ),
            {"org": org_id, "uid": user_id, "role": member_role_id},
        )
        await db.commit()
    except Exception:  # noqa: BLE001 —— 补全失败不影响注册主流程
        await db.rollback()


# ---------------- 知识库按部门划分 ----------------
# 管理员把知识库“划分”给部门（knowledge_space_departments，多对多）或全企业（scope_type=enterprise）：
#   - 被划分到的部门的在职成员自动只读（viewer）；部门负责人（部门管理员）可编辑文档（editor）；
#   - 全企业空间对该企业全部在职成员只读；
#   - “绝密”密级（restricted）不继承任何部门 / 全企业身份，只能由所有者、被明确加入的成员访问。
# 成员资格只给“读”，改设置 / 管成员 / 删空间始终需要空间角色或平台管理员。
_VALID_DEPARTMENT_MEMBER = (
    "JOIN teams t ON t.id = tm.team_id AND t.status = 'active' "
    "JOIN organizations o ON o.id = t.organization_id AND o.status = 'active' "
    "JOIN organization_members om ON om.organization_id = t.organization_id "
    "AND om.user_id = tm.user_id AND om.status = 'active' "
)

_READER_SPACE_IDS_SQL = (
    "SELECT ksd.space_id FROM knowledge_space_departments ksd "
    "JOIN knowledge_spaces ks ON ks.id = ksd.space_id AND ks.scope_type = 'department' AND ks.sensitivity <> 'restricted' "
    "JOIN team_members tm ON tm.team_id = ksd.team_id "
    + _VALID_DEPARTMENT_MEMBER +
    "WHERE tm.user_id = :uid AND tm.status = 'active' "
    "UNION "
    "SELECT ks.id FROM knowledge_spaces ks "
    "JOIN organizations o ON o.id = ks.organization_id AND o.status = 'active' "
    "JOIN organization_members om ON om.organization_id = ks.organization_id AND om.status = 'active' "
    "WHERE ks.scope_type = 'enterprise' AND ks.sensitivity <> 'restricted' AND om.user_id = :uid"
)

# 这个用户在“该空间被划分到的部门”里的角色代码（可能有多行）
_GRANTED_TEAM_ROLES_SQL = (
    "SELECT er.code FROM knowledge_space_departments ksd "
    "JOIN team_members tm ON tm.team_id = ksd.team_id "
    + _VALID_DEPARTMENT_MEMBER +
    "JOIN enterprise_role er ON er.id = tm.role_id AND er.scope = 'team' "
    "WHERE ksd.space_id = :sid AND tm.user_id = :uid AND tm.status = 'active'"
)

_ENTERPRISE_MEMBER_OF_SQL = (
    "SELECT 1 FROM organization_members om JOIN organizations o ON o.id = om.organization_id AND o.status = 'active' "
    "WHERE om.organization_id = :org AND om.user_id = :uid AND om.status = 'active' LIMIT 1"
)


def _role_from(space, granted_codes, enterprise_member: bool):
    """纯逻辑：划分带来的角色。granted_codes = 用户在被划分部门里的角色代码列表。"""
    if space is None or space.sensitivity == "restricted":
        return None
    if space.scope_type == "department" and granted_codes:
        return "editor" if "admin" in granted_codes else "viewer"
    if space.scope_type == "enterprise" and enterprise_member:
        return "viewer"
    return None


def inherited_space_role(db, user_id: int, space) -> Optional[str]:
    """划分带来的角色：None / viewer / editor。不含所有者、显式成员、旧的 team_id 部门负责人这几条来源。"""
    if space is None or space.sensitivity == "restricted":
        return None
    codes = []
    if space.scope_type == "department":
        codes = [r[0] for r in db.execute(text(_GRANTED_TEAM_ROLES_SQL), {"sid": space.id, "uid": user_id}).all()]
    enterprise = (space.scope_type == "enterprise" and space.organization_id is not None
                  and db.execute(text(_ENTERPRISE_MEMBER_OF_SQL), {"org": space.organization_id, "uid": user_id}).first() is not None)
    return _role_from(space, codes, enterprise)


def list_reader_space_ids(db, user_id: int) -> List[int]:
    """被划分给本人所在部门、或划分给全企业的空间编号。"""
    return [r[0] for r in db.execute(text(_READER_SPACE_IDS_SQL), {"uid": user_id}).all()]


async def inherited_space_role_async(db: AsyncSession, user_id: int, space) -> Optional[str]:
    if space is None or space.sensitivity == "restricted":
        return None
    codes = []
    if space.scope_type == "department":
        res = await db.execute(text(_GRANTED_TEAM_ROLES_SQL), {"sid": space.id, "uid": user_id})
        codes = [r[0] for r in res.all()]
    enterprise = False
    if space.scope_type == "enterprise" and space.organization_id is not None:
        res = await db.execute(text(_ENTERPRISE_MEMBER_OF_SQL), {"org": space.organization_id, "uid": user_id})
        enterprise = res.first() is not None
    return _role_from(space, codes, enterprise)


async def list_reader_space_ids_async(db: AsyncSession, user_id: int) -> List[int]:
    res = await db.execute(text(_READER_SPACE_IDS_SQL), {"uid": user_id})
    return [row[0] for row in res.all()]


async def get_team_names_async(db: AsyncSession, team_ids) -> dict:
    ids = sorted({int(t) for t in team_ids if t is not None})
    if not ids:
        return {}
    res = await db.execute(text("SELECT id, name FROM teams WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)), {"ids": ids})
    return {r[0]: r[1] for r in res.all()}


async def get_space_departments_async(db: AsyncSession, space_ids) -> dict:
    """{space_id: [{id, name}, ...]}：这些空间被划分给了哪些部门。"""
    ids = sorted({int(s) for s in space_ids})
    if not ids:
        return {}
    res = await db.execute(text(
        "SELECT ksd.space_id, t.id, t.name FROM knowledge_space_departments ksd JOIN teams t ON t.id = ksd.team_id "
        "WHERE ksd.space_id IN :ids ORDER BY t.id").bindparams(bindparam("ids", expanding=True)), {"ids": ids})
    out: dict = {}
    for space_id, team_id, name in res.all():
        out.setdefault(space_id, []).append({"id": team_id, "name": name})
    return out
