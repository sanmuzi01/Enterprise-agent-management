"""企业/部门的统一授权层（Phase 3B 第 2 步，docs/enterprise-rbac-plan.md 第 2 节）。

路由层直接 `Depends(require_org_role(...))` 这类工厂函数的返回值，不再各自手写
"查一下 is_admin_user() 顶一下"这种权宜判断——那是本次改造要收敛掉的模式（见设计稿
2.2 节）。`is_admin_user()` 之后只保留给平台超级管理员，企业内部的权限判断全部走这里。

固定的校验顺序（设计稿 2.1 节，不因路由而变）：
    1. 是否属于该企业/部门（有效的 organization_members/team_members 行）—— 不通过 404
    2. 是否具有所需的角色等级 —— 不通过 403

**不含知识库空间的权限判断**：设计稿最初的草稿在这里重新实现了一套
`require_space_permission`/`get_accessible_space_ids`，后来发现
`service/access_control.py` 早就有 `get_owned_space[_async]`/`get_space_role[_async]`/
`user_space_ids[_async]` 覆盖了完全一样的事情（owner + SpaceMember，"阶段6预留"接口），
而且已经被 `service/knowledge_space/*` 全线在用——两套并存迟早会算出不一样的结果。
已改成扩展 `service/access_control.py`（加"部门 team admin 也能看"这个新来源），
不在这里重复一遍，见 [docs/enterprise-rbac-plan.md](../docs/enterprise-rbac-plan.md) 的
执行记录。这里只留 `require_org_role`/`require_team_role`——这两个是真正新增的能力，
之前没有任何模块做过组织/部门维度的判断。

**这一步只落地这两个函数本身 + 单元测试，还没有接到任何路由上**——按设计稿第 3 节的
模块清单逐个接入是下一步，接一个模块跑一遍那个模块的路由级测试，不是一次性全量替换。
"""
from typing import Iterable, List, Optional

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from models.async_db import get_async_db
from models.init_db import User, get_db
from service.dependencies import get_current_user, get_current_user_async
from service.exceptions import NotFound, PermissionDenied


def _role_ranks(db: Session, scope: str, codes: Iterable[str]) -> List[int]:
    """查这几个角色代码（在给定 scope 下）各自的 rank——不在 Python 里硬编码一份跟迁移
    种子数据重复的映射表，避免两边改了一个忘了改另一个。"""
    codes = list(codes)
    if not codes:
        return []
    placeholders = ",".join(f":c{i}" for i in range(len(codes)))
    params = {f"c{i}": c for i, c in enumerate(codes)}
    params["scope"] = scope
    rows = db.execute(
        text(f"SELECT `rank` FROM enterprise_role WHERE scope=:scope AND code IN ({placeholders})"),
        params,
    ).all()
    return [r[0] for r in rows]


def _min_required_rank(db: Session, scope: str, roles: Iterable[str]) -> int:
    ranks = _role_ranks(db, scope, roles)
    if not ranks:
        # 传进来的角色代码在 enterprise_role 里一个都找不到——多半是拼错了，
        # 宁可拒绝所有人也不要静默放行。
        return 10**9
    return min(ranks)


def _org_role_rank(db: Session, user_id: int) -> Optional[int]:
    """当前用户在企业里的最高角色等级；不是成员、或所在企业已停用都返回 None。

    单企业部署下一个用户实际只会在一个 organization_members 行里，取 MAX 是为了
    未来真的支持多企业时也不用改这个函数。

    必须 JOIN `organizations` 检查 `status='active'`——企业本身被停用后，成员的
    企业角色不能继续生效，不然管理员在组织后台停用企业只是摆设（修复记录见
    docs/enterprise-rbac-plan.md 第18节）。
    """
    return db.execute(
        text(
            "SELECT MAX(er.rank) FROM organization_members om "
            "JOIN organizations o ON om.organization_id = o.id "
            "JOIN enterprise_role er ON om.role_id = er.id "
            "WHERE om.user_id = :uid AND om.status = 'active' AND er.scope = 'organization' "
            "AND o.status = 'active'"
        ),
        {"uid": user_id},
    ).scalar()


def _team_role_rank(db: Session, user_id: int, team_id: int) -> Optional[int]:
    """同上，JOIN `teams` 检查 `status='active'`——部门被停用后，部门内角色
    （包括部门负责人）立即失效，不用等成员关系本身也被清掉。"""
    return db.execute(
        text(
            "SELECT MAX(er.rank) FROM team_members tm "
            "JOIN teams t ON tm.team_id = t.id "
            "JOIN enterprise_role er ON tm.role_id = er.id "
            "WHERE tm.user_id = :uid AND tm.team_id = :tid "
            "AND tm.status = 'active' AND er.scope = 'team' AND t.status = 'active'"
        ),
        {"uid": user_id, "tid": team_id},
    ).scalar()


def is_org_admin(db: Session, user_id: int) -> bool:
    """当前用户是否有企业管理员及以上权限（admin/owner）。这是"能跨部门审批/查看"的唯一
    权限来源——`service/tools/oa_leave.py`、`service/tools/procurement.py` 用它算好之后
    签进企业业务中心的 RequestContext，Java 侧只信这个签好的布尔值，不会（也没有数据源）
    自己再查一遍 enterprise_role，见 docs/enterprise-business-hub-plan.md 第16节。"""
    rank = _org_role_rank(db, user_id)
    if rank is None:
        return False
    return rank >= _min_required_rank(db, "organization", ["admin"])


def is_team_admin(db: Session, user_id: int, team_id: Optional[int]) -> bool:
    """当前用户是否是给定部门的负责人（team scope 的 admin 及以上）。部门内审批权限的
    唯一来源，同上，不接受调用方自行断言。"""
    if team_id is None:
        return False
    rank = _team_role_rank(db, user_id, team_id)
    if rank is None:
        return False
    return rank >= _min_required_rank(db, "team", ["admin"])


def require_org_role(*roles: str):
    """FastAPI 依赖工厂：当前用户必须是企业成员，且角色等级 >= 给定 roles 里最低的那个。

    "等级 >= 最低要求" 而不是"角色代码必须精确等于给定值之一"：要求 "admin" 的接口，
    "owner" 也应该能过，不然每个路由都要把上级角色抄一遍到 roles 里，容易漏。
    不通过：不是成员 -> 404（不暴露资源存在性）；是成员但等级不够 -> 403。
    """
    def _dep(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        user_rank = _org_role_rank(db, current_user.id)
        if user_rank is None:
            raise NotFound("企业不存在或无权限")
        min_rank = _min_required_rank(db, "organization", roles)
        if user_rank < min_rank:
            raise PermissionDenied("没有足够的企业权限")
        return current_user

    return _dep


async def _org_role_rank_async(db, user_id: int) -> Optional[int]:
    result = await db.execute(
        text(
            "SELECT MAX(er.rank) FROM organization_members om "
            "JOIN organizations o ON om.organization_id = o.id "
            "JOIN enterprise_role er ON om.role_id = er.id "
            "WHERE om.user_id = :uid AND om.status = 'active' AND er.scope = 'organization' "
            "AND o.status = 'active'"
        ),
        {"uid": user_id},
    )
    return result.scalar()


async def _min_required_rank_async(db, scope: str, roles: Iterable[str]) -> int:
    roles = list(roles)
    if not roles:
        return 10**9
    placeholders = ",".join(f":c{i}" for i in range(len(roles)))
    params = {f"c{i}": c for i, c in enumerate(roles)}
    params["scope"] = scope
    result = await db.execute(
        text(f"SELECT `rank` FROM enterprise_role WHERE scope=:scope AND code IN ({placeholders})"),
        params,
    )
    ranks = [r[0] for r in result.all()]
    return min(ranks) if ranks else 10**9


async def is_org_admin_async(db, user_id: int) -> bool:
    """`is_org_admin` 的异步版，部门工作台（`service/department_workspace_service.py`）
    在异步路由里判断"当前用户是不是企业管理员"要用这个，不新开同步 Session。"""
    rank = await _org_role_rank_async(db, user_id)
    if rank is None:
        return False
    return rank >= await _min_required_rank_async(db, "organization", ["admin"])


def require_org_role_async(*roles: str):
    """`require_org_role` 的异步版，语义完全一致——给已经全异步化的模块（比如
    Phase 3D 阶段4 的审批路由）用，避免为了一个权限校验硬塞一个同步 Session。"""
    async def _dep(
        current_user: User = Depends(get_current_user_async),
        db=Depends(get_async_db),
    ) -> User:
        user_rank = await _org_role_rank_async(db, current_user.id)
        if user_rank is None:
            raise NotFound("企业不存在或无权限")
        min_rank = await _min_required_rank_async(db, "organization", roles)
        if user_rank < min_rank:
            raise PermissionDenied("没有足够的企业权限")
        return current_user

    return _dep


def require_team_role(*roles: str):
    """同 require_org_role，范围换成部门。依赖路由路径里有 `team_id` 参数——FastAPI 按参数名
    从路径里取值注入进来，和路由自己声明 `team_id: int` 是同一份。

    部门不属于当前用户所在企业（或部门本身不存在）时也视为不存在，统一 404，
    不额外区分"部门不存在"和"部门存在但不是你的企业"——那条区分本身就是信息泄露。
    """
    def _dep(
        team_id: int,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        user_org_rank = _org_role_rank(db, current_user.id)
        if user_org_rank is None:
            raise NotFound("部门不存在或无权限")
        team_org_id = db.execute(
            text("SELECT organization_id FROM teams WHERE id = :tid AND status = 'active'"), {"tid": team_id}
        ).scalar()
        user_org_id = db.execute(
            text(
                "SELECT organization_id FROM organization_members "
                "WHERE user_id = :uid AND status = 'active' LIMIT 1"
            ),
            {"uid": current_user.id},
        ).scalar()
        if team_org_id is None or team_org_id != user_org_id:
            raise NotFound("部门不存在或无权限")

        user_rank = _team_role_rank(db, current_user.id, team_id)
        if user_rank is None:
            raise NotFound("部门不存在或无权限")
        min_rank = _min_required_rank(db, "team", roles)
        if user_rank < min_rank:
            raise PermissionDenied("没有足够的部门权限")
        return current_user

    return _dep
