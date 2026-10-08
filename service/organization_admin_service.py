"""企业组织管理后台服务（部门增删/成员分配/部门负责人/企业角色/权限关系查看）。

补的是这个产品缺口：数据库早就有 Organization/Team/EnterpriseRole/OrganizationMember/
TeamMember（Phase 3B/3D，见 docs/enterprise-rbac-plan.md），但一直没有对应的管理入口——
建部门、分配员工、设负责人、调角色全部只能靠迁移脚本或直接改库。这里补上。

网关权限：跟 `FasdtApi/admin.py` 的其它所有端点一样，走平台超级管理员
（`get_current_admin_user_async`），不是企业内部的 `require_org_role("admin")`——
项目现在是单企业私有部署（`docs/enterprise-rbac-plan.md` 9.6 节：Organization 表长期
只有1行），操作这个后台的人本来就是平台管理员本人，不需要再引入一套并行的权限入口。
`is_org_admin`/`is_team_admin`（`service/enterprise_access.py`）仍然是聊天/审批链路里
判断"企业管理员"/"部门负责人"的唯一依据——这里只是**给这两个角色赋值的地方**，跟它们
"被谁读取判断权限"是两件事。

只支持单企业部署：所有函数都对"当前唯一的 Organization"操作，多企业不是这次要解决的
范围（跟 Team.organization_id 的字段设计一致，留着字段但不做多企业管理 UI）。
"""
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from models.init_db import (
    Agent,
    EnterpriseRole,
    KnowledgeSpace,
    Organization,
    OrganizationMember,
    Team,
    TeamMember,
    User,
)
from service import audit_service
from service.exceptions import Conflict, InvalidInput, NotFound


async def _get_default_organization(db) -> Organization:
    result = await db.execute(select(Organization).order_by(Organization.id).limit(1))
    org = result.scalar_one_or_none()
    if org is None:
        raise NotFound("企业尚未初始化（没有 Organization 记录），先跑 scripts/backfill_default_organization.py")
    return org


async def _role_id(db, scope: str, code: str) -> int:
    result = await db.execute(
        select(EnterpriseRole.id).where(EnterpriseRole.scope == scope, EnterpriseRole.code == code)
    )
    role_id = result.scalar_one_or_none()
    if role_id is None:
        raise InvalidInput(f"未知的{scope}角色代码: {code}")
    return role_id


async def _role_row_by_id(db, role_id: int) -> Optional[EnterpriseRole]:
    result = await db.execute(select(EnterpriseRole).where(EnterpriseRole.id == role_id))
    return result.scalar_one_or_none()


async def list_enterprise_roles(db) -> Dict[str, List[Dict]]:
    """给前端角色选择器用的角色目录，按 scope 分组。"""
    result = await db.execute(select(EnterpriseRole).order_by(EnterpriseRole.scope, EnterpriseRole.rank))
    roles = result.scalars().all()
    payload: Dict[str, List[Dict]] = {"organization": [], "team": []}
    for r in roles:
        payload.setdefault(r.scope, []).append({"code": r.code, "name": r.name, "rank": r.rank})
    return payload


# ---------------- 部门（Team） ----------------

async def list_teams(db) -> List[Dict]:
    org = await _get_default_organization(db)
    result = await db.execute(select(Team).where(Team.organization_id == org.id).order_by(Team.id))
    teams = result.scalars().all()
    if not teams:
        return []
    team_ids = [t.id for t in teams]

    count_result = await db.execute(
        select(TeamMember.team_id, func.count(TeamMember.id))
        .where(TeamMember.team_id.in_(team_ids), TeamMember.status == "active")
        .group_by(TeamMember.team_id)
    )
    member_counts = {tid: c for tid, c in count_result.all()}

    admin_role_result = await db.execute(
        select(EnterpriseRole.id).where(EnterpriseRole.scope == "team", EnterpriseRole.code == "admin")
    )
    admin_role_id = admin_role_result.scalar_one_or_none()

    leads_by_team: Dict[int, List[Dict]] = {}
    if admin_role_id is not None:
        lead_result = await db.execute(
            select(TeamMember.team_id, User.id, User.name)
            .join(User, User.id == TeamMember.user_id)
            .where(TeamMember.team_id.in_(team_ids), TeamMember.role_id == admin_role_id,
                   TeamMember.status == "active")
        )
        for tid, uid, uname in lead_result.all():
            leads_by_team.setdefault(tid, []).append({"user_id": uid, "name": uname})

    return [
        {
            "id": t.id,
            "name": t.name,
            "status": t.status,
            "department_code": t.department_code,
            "member_count": member_counts.get(t.id, 0),
            "leads": leads_by_team.get(t.id, []),
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in teams
    ]


def _validate_department_code(department_code: Optional[str]) -> None:
    if department_code is None:
        return
    from service.runtime.central_router import VALID_DEPARTMENT_CODES
    if department_code not in VALID_DEPARTMENT_CODES:
        raise InvalidInput(f"department_code 只能是 {sorted(VALID_DEPARTMENT_CODES)} 之一或不填")


async def create_team(db, name: str, owner_user_id: int, department_code: Optional[str] = None) -> Dict:
    name = (name or "").strip()
    if not name:
        raise InvalidInput("部门名称不能为空")
    _validate_department_code(department_code)
    org = await _get_default_organization(db)
    existing = await db.execute(
        select(Team.id).where(Team.organization_id == org.id, Team.name == name)
    )
    if existing.scalar_one_or_none() is not None:
        raise Conflict("同名部门已存在")
    team = Team(organization_id=org.id, name=name, owner_user_id=owner_user_id, status="active",
                department_code=department_code)
    db.add(team)
    await db.flush()
    await db.commit()
    await audit_service.record_async(
        owner_user_id, "org.team_created", resource_type="team", resource_id=team.id,
        detail={"name": team.name, "department_code": department_code},
    )
    return {"id": team.id, "name": team.name, "status": team.status, "department_code": team.department_code,
            "member_count": 0, "leads": []}


# sentinel：区分"没传这个参数（不改）"和"显式传了 None（清空业务类型）"——
# department_code 本身的合法取值就包含 None（未分配业务类型的部门）。
_UNSET = object()


async def update_team(db, team_id: int, operator_id: int, name: Optional[str] = None,
                       status: Optional[str] = None, department_code: Any = _UNSET) -> Dict:
    team = await _get_team_or_404(db, team_id)
    changes: Dict[str, Any] = {}
    if name is not None:
        name = name.strip()
        if not name:
            raise InvalidInput("部门名称不能为空")
        if name != team.name:
            changes["name"] = {"from": team.name, "to": name}
        team.name = name
    if status is not None:
        if status not in ("active", "disabled"):
            raise InvalidInput("status 只能是 active/disabled")
        if status != team.status:
            changes["status"] = {"from": team.status, "to": status}
        team.status = status
    if department_code is not _UNSET:
        _validate_department_code(department_code)
        if department_code != team.department_code:
            changes["department_code"] = {"from": team.department_code, "to": department_code}
        team.department_code = department_code
    await db.commit()
    if changes:
        await audit_service.record_async(
            operator_id, "org.team_updated", resource_type="team", resource_id=team.id, detail=changes,
        )
    return {"id": team.id, "name": team.name, "status": team.status, "department_code": team.department_code}


async def _get_team_or_404(db, team_id: int) -> Team:
    org = await _get_default_organization(db)
    result = await db.execute(
        select(Team).where(Team.id == team_id, Team.organization_id == org.id)
    )
    team = result.scalar_one_or_none()
    if team is None:
        raise NotFound("部门不存在")
    return team


async def team_permissions(db, team_id: int) -> Dict:
    """部门权限关系总览：成员+角色、绑定的知识库空间、绑定的部门 Agent。"""
    team = await _get_team_or_404(db, team_id)
    members = await list_team_members(db, team_id)

    from models.init_db import KnowledgeSpaceDepartment
    space_result = await db.execute(
        select(KnowledgeSpace.id, KnowledgeSpace.name, KnowledgeSpace.status)
        .where((KnowledgeSpace.team_id == team_id) | KnowledgeSpace.id.in_(
            select(KnowledgeSpaceDepartment.space_id).where(KnowledgeSpaceDepartment.team_id == team_id)))
    )
    spaces = [{"id": sid, "name": sname, "status": sstatus} for sid, sname, sstatus in space_result.all()]

    agent_result = await db.execute(
        select(Agent.id, Agent.name, Agent.department_code)
        .where(Agent.team_id == team_id, Agent.scope_type == "department")
    )
    agents = [{"id": aid, "name": aname, "department_code": acode} for aid, aname, acode in agent_result.all()]

    return {
        "team": {"id": team.id, "name": team.name, "status": team.status},
        "members": members,
        "knowledge_spaces": spaces,
        "agents": agents,
    }


# ---------------- 部门成员 ----------------

async def list_team_members(db, team_id: int) -> List[Dict]:
    await _get_team_or_404(db, team_id)
    result = await db.execute(
        select(TeamMember, User.name, EnterpriseRole.code, EnterpriseRole.name)
        .join(User, User.id == TeamMember.user_id)
        .join(EnterpriseRole, EnterpriseRole.id == TeamMember.role_id)
        .where(TeamMember.team_id == team_id)
        .order_by(TeamMember.id)
    )
    return [
        {
            "user_id": tm.user_id,
            "name": uname,
            "role_code": role_code,
            "role_name": role_name,
            "status": tm.status,
        }
        for tm, uname, role_code, role_name in result.all()
    ]


async def add_team_member(db, team_id: int, operator_id: int, user_id: int, role_code: str = "member") -> Dict:
    await _get_team_or_404(db, team_id)
    user_result = await db.execute(select(User.id).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise NotFound("用户不存在")

    existing = await db.execute(
        select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    )
    role_id = await _role_id(db, "team", role_code)
    member = existing.scalar_one_or_none()
    if member is not None:
        member.role_id = role_id
        member.status = "active"
    else:
        db.add(TeamMember(team_id=team_id, user_id=user_id, role_id=role_id, status="active"))

    # 单企业部署下，"在某个部门里"隐含"是企业成员"——分配部门时顺带补上企业成员身份
    # （默认最低的 member 档），没有的话才补，已经有企业角色（哪怈是更高的）不动它。
    await _ensure_org_member(db, user_id)
    await db.commit()
    await audit_service.record_async(
        operator_id, "org.team_member_added", resource_type="team", resource_id=team_id,
        detail={"user_id": user_id, "role_code": role_code},
    )
    return {"user_id": user_id, "team_id": team_id, "role_code": role_code}


async def update_team_member_role(db, team_id: int, operator_id: int, user_id: int, role_code: str) -> Dict:
    await _get_team_or_404(db, team_id)
    result = await db.execute(
        select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFound("该用户不在这个部门里")
    member.role_id = await _role_id(db, "team", role_code)
    await db.commit()
    await audit_service.record_async(
        operator_id, "org.team_member_role_changed", resource_type="team", resource_id=team_id,
        detail={"user_id": user_id, "role_code": role_code},
    )
    return {"user_id": user_id, "team_id": team_id, "role_code": role_code}


async def remove_team_member(db, team_id: int, operator_id: int, user_id: int) -> Dict:
    await _get_team_or_404(db, team_id)
    result = await db.execute(
        select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFound("该用户不在这个部门里")
    await db.delete(member)
    await db.commit()
    await audit_service.record_async(
        operator_id, "org.team_member_removed", resource_type="team", resource_id=team_id,
        detail={"user_id": user_id},
    )
    return {"message": "已移出部门"}


async def _ensure_org_member(db, user_id: int) -> None:
    org = await _get_default_organization(db)
    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org.id, OrganizationMember.user_id == user_id,
        )
    )
    if result.scalar_one_or_none() is not None:
        return
    role_id = await _role_id(db, "organization", "member")
    db.add(OrganizationMember(organization_id=org.id, user_id=user_id, role_id=role_id, status="active"))
    await db.flush()


# ---------------- 企业成员/企业角色 ----------------

async def list_org_members(db) -> List[Dict]:
    org = await _get_default_organization(db)
    department_rows = await db.execute(
        select(TeamMember.user_id, Team.id, Team.name, Team.status,
               EnterpriseRole.code, EnterpriseRole.name, TeamMember.status)
        .join(Team, Team.id == TeamMember.team_id)
        .join(EnterpriseRole, EnterpriseRole.id == TeamMember.role_id)
        .where(Team.organization_id == org.id)
        .order_by(Team.id)
    )
    departments = {}
    for uid, tid, name, status, code, role_name, membership_status in department_rows.all():
        departments.setdefault(uid, []).append({
            "id": tid, "name": name, "status": status, "role_code": code,
            "role_name": role_name, "membership_status": membership_status,
        })
    result = await db.execute(
        select(OrganizationMember, User.name, EnterpriseRole.code, EnterpriseRole.name)
        .join(User, User.id == OrganizationMember.user_id)
        .join(EnterpriseRole, EnterpriseRole.id == OrganizationMember.role_id)
        .where(OrganizationMember.organization_id == org.id)
        .order_by(OrganizationMember.id)
    )
    return [
        {
            "user_id": om.user_id,
            "name": uname,
            "role_code": role_code,
            "role_name": role_name,
            "status": om.status,
            "departments": departments.get(om.user_id, []),
        }
        for om, uname, role_code, role_name in result.all()
    ]


async def _count_active_owners(db, organization_id: int, exclude_user_id: Optional[int] = None) -> int:
    """数"这个企业还剩几个在职 owner"（排除 exclude_user_id 那一个，也就是正要被
    降级/移除的人）。

    P1 并发修复：之前"先数一遍剩几个 owner，数完了再改"这两步不是原子的，两个
    并发请求可能都在对方提交之前读到"还有 1 个"，都判断"安全，可以降级/移除"，
    最终把最后两个 owner 同时降级/移除掉，企业变成零 owner。

    加锁范围不能按 `exclude_user_id` 缩小——如果 SQL 层面就把"正要被排除的那个
    人"排除在锁定范围之外，两个并发请求分别降级 A、B 两个不同的 owner 时，各自
    排除的是对方，加锁的也就是"对方那一行"，双方锁的集合刚好不重叠，等于都没
    锁住，一样会读到"还有人"就都放行。必须锁"这个企业当前全部在职 owner 行"这个
    不随 exclude_user_id 变化的固定集合，让任何两个并发的"改这个企业 owner"操作
    都抢同一把锁，才能真正互斥；排除自己改成锁到之后、在 Python 里过滤。
    """
    owner_role_id = await _role_id(db, "organization", "owner")
    result = await db.execute(
        select(OrganizationMember.user_id).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role_id == owner_role_id,
            OrganizationMember.status == "active",
        ).with_for_update()
    )
    owner_user_ids = [row[0] for row in result.all()]
    if exclude_user_id is not None:
        owner_user_ids = [uid for uid in owner_user_ids if uid != exclude_user_id]
    return len(owner_user_ids)


async def add_org_member(db, operator_id: int, user_id: int, role_code: str = "member") -> Dict:
    org = await _get_default_organization(db)
    user_result = await db.execute(select(User.id).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise NotFound("用户不存在")

    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org.id, OrganizationMember.user_id == user_id,
        )
    )
    role_id = await _role_id(db, "organization", role_code)
    member = result.scalar_one_or_none()
    if member is not None:
        member.role_id = role_id
        member.status = "active"
    else:
        db.add(OrganizationMember(organization_id=org.id, user_id=user_id, role_id=role_id, status="active"))
    await db.commit()
    await audit_service.record_async(
        operator_id, "org.member_added", resource_type="organization", resource_id=org.id,
        detail={"user_id": user_id, "role_code": role_code},
    )
    return {"user_id": user_id, "role_code": role_code}


async def update_org_member(db, operator_id: int, user_id: int, role_code: Optional[str] = None,
                             status: Optional[str] = None) -> Dict:
    org = await _get_default_organization(db)
    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org.id, OrganizationMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFound("该用户不是企业成员")

    owner_role_id = await _role_id(db, "organization", "owner")
    was_active_owner = member.role_id == owner_role_id and member.status == "active"
    new_role_id = member.role_id if role_code is None else await _role_id(db, "organization", role_code)
    new_status = member.status if status is None else status
    if status is not None and status not in ("active", "disabled"):
        raise InvalidInput("status 只能是 active/disabled")
    will_be_active_owner = new_role_id == owner_role_id and new_status == "active"

    if was_active_owner and not will_be_active_owner:
        remaining = await _count_active_owners(db, org.id, exclude_user_id=user_id)
        if remaining == 0:
            raise InvalidInput("不能降级/停用企业最后一个 owner，请先把 owner 角色转给另一个人")

    if role_code is not None:
        member.role_id = new_role_id
    if status is not None:
        member.status = new_status
    await db.commit()
    await audit_service.record_async(
        operator_id, "org.member_updated", resource_type="organization", resource_id=org.id,
        detail={"user_id": user_id, "role_code": role_code, "status": status},
    )
    return {"user_id": user_id}


async def remove_org_member(db, operator_id: int, user_id: int) -> Dict:
    org = await _get_default_organization(db)
    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org.id, OrganizationMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFound("该用户不是企业成员")

    owner_role_id = await _role_id(db, "organization", "owner")
    if member.role_id == owner_role_id and member.status == "active":
        remaining = await _count_active_owners(db, org.id, exclude_user_id=user_id)
        if remaining == 0:
            raise InvalidInput("不能移除企业最后一个 owner，请先把 owner 角色转给另一个人")

    # 企业成员被移除时，顺带把这个人在所有部门里的身份也清掉——不然会留下"不是企业成员
    # 但还挂在某个部门"的悬空状态，跟 require_team_role 的"先查企业成员再查部门角色"
    # 这个既有假设不一致。
    team_ids_result = await db.execute(select(Team.id).where(Team.organization_id == org.id))
    team_ids = [row[0] for row in team_ids_result.all()]
    if team_ids:
        tm_result = await db.execute(
            select(TeamMember).where(TeamMember.user_id == user_id, TeamMember.team_id.in_(team_ids))
        )
        for tm in tm_result.scalars().all():
            await db.delete(tm)
    await db.delete(member)
    await db.commit()
    await audit_service.record_async(
        operator_id, "org.member_removed", resource_type="organization", resource_id=org.id,
        detail={"user_id": user_id},
    )
    return {"message": "已移出企业"}
