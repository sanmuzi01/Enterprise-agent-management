"""知识库空间的成员/角色解析。

阶段1：只有 owner（space.user_id == 当前用户）。
阶段6：owner 隐含（space.user_id），其余角色查 space_members(space_id, user_id, role)。
Phase 3B（docs/enterprise-rbac-plan.md）接入部门管理员：该空间所属部门（team_id）的
team admin 视同这个空间的 admin，即使不是这个空间的 space_members——即便不是这个空间
的 SpaceMember，只要是所属部门的 team admin 也一样能管。
调用方拿 role 后用 can_* 判定能不能写。
"""

ROLES = ("owner", "admin", "editor", "viewer")
ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3, "owner": 4}

_WRITE_DOC = {"owner", "admin", "editor"}
_MANAGE_SPACE = {"owner", "admin"}
_DELETE_SPACE = {"owner"}
_MANAGE_MEMBERS = {"owner", "admin"}


def resolve_role(
    user_id: int, space, member_role: str | None = None, *, is_team_admin: bool = False,
    is_team_member: bool = False,
) -> str | None:
    """当前用户对该空间的角色；无权返回 None。

    member_role 由调用方从 space_members 查出后传入；is_team_admin 由调用方查
    models/enterprise_dao.py 后传入（DAO 隔离，本函数保持纯逻辑）。
    """
    if space is None:
        return None
    if getattr(space, "user_id", None) == user_id:
        return "owner"
    if member_role in ROLES:
        return member_role
    if is_team_admin:
        return "admin"
    # 部门空间：本部门成员自动拥有只读权限（restricted 密级由调用方在 is_team_member 里排除）
    return "viewer" if is_team_member else None


def at_least(role: str | None, minimum: str) -> bool:
    return ROLE_RANK.get(role or "", 0) >= ROLE_RANK.get(minimum, 99)


def can_write_doc(role: str | None) -> bool:
    return role in _WRITE_DOC


def can_manage_space(role: str | None) -> bool:
    return role in _MANAGE_SPACE


def can_delete_space(role: str | None) -> bool:
    return role in _DELETE_SPACE


def can_manage_members(role: str | None) -> bool:
    return role in _MANAGE_MEMBERS
