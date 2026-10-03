"""企业/部门成员关系的查询（Phase 3B，docs/enterprise-rbac-plan.md）。

两类调用方：
- service/access_control.py：knowledge_spaces 的可见性计算要不要把"部门管理员"算进去，
  只读，别处不要直接查这几张表，避免"谁是部门管理员"的判定逻辑散落在多个地方。
- service/auth_service.py / auth_async_service.py 的 register()：新用户自动加入默认企业。
"""
from typing import List, Optional

from sqlalchemy import text
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
