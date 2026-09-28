"""Shared resource access checks.

Keep these helpers framework-agnostic so both API routes and services can reuse
the same ownership rules without importing FastAPI.
"""
from typing import Optional

from models.agent_dao import get_agent_by_id
from models.agent_run_dao import get_run_by_id
from models.background_task_dao import get_task_by_id
from models.conversation_dao import get_conversation_by_id
from models.knowledge_dao import get_knowledge_by_id
from models.memory_dao import get_memory_by_id
from models.init_db import Agent, AgentRun, BackgroundTask, Conversation, Knowledge, Memory, Skill


def get_owned_agent(db, user_id: int, agent_id: int) -> Optional[Agent]:
    agent = get_agent_by_id(db, agent_id)
    if not agent or agent.user_id != user_id:
        return None
    return agent


def get_usable_agent(db, user_id: int, agent_id: int) -> Optional[Agent]:
    """能"使用"该 Agent（聊天/检索/流水线步骤/评估）则返回，否则 None。

    比 `get_owned_agent` 多两条来源：`scope_type="department"` 时该 Agent 所属部门的
    在职成员、`scope_type="enterprise"` 时任意在职企业成员——对应 Phase 3D 阶段1加的
    归属字段（docs/enterprise-rbac-plan.md 9.5）。现存 Agent 全部是 `scope_type="personal"`
    默认值，这里放宽的范围目前不会影响任何人。

    只放宽"用"，不放宽"改"：改名/删除/绑定知识库空间/绑定 Skill 这些配置类操作继续用
    `get_owned_agent`，不要在这些地方换成这个函数。
    """
    from models.enterprise_dao import is_team_member_of_team, is_org_member

    agent = get_agent_by_id(db, agent_id)
    if not agent:
        return None
    if agent.user_id == user_id:
        return agent
    if agent.scope_type == "department" and is_team_member_of_team(db, user_id, agent.team_id):
        return agent
    if agent.scope_type == "enterprise" and is_org_member(db, user_id):
        return agent
    return None


def get_owned_conversation(db, user_id: int, conversation_id: int, agent_id: int = None) -> Optional[Conversation]:
    conversation = get_conversation_by_id(db, conversation_id)
    if not conversation or conversation.user_id != user_id:
        return None
    if agent_id is not None and conversation.agent_id != agent_id:
        return None
    return conversation


def get_owned_knowledge(db, user_id: int, knowledge_id: int, agent_id: int = None) -> Optional[Knowledge]:
    knowledge = get_knowledge_by_id(db, knowledge_id)
    if not knowledge or knowledge.user_id != user_id:
        return None
    if agent_id is not None and knowledge.agent_id != agent_id:
        return None
    return knowledge


def get_owned_run(db, user_id: int, run_id: int, agent_id: int = None) -> Optional[AgentRun]:
    run = get_run_by_id(db, run_id)
    if not run or run.user_id != user_id:
        return None
    if agent_id is not None and run.agent_id != agent_id:
        return None
    return run


def get_owned_task(db, user_id: int, task_id: int, agent_id: int = None) -> Optional[BackgroundTask]:
    task = get_task_by_id(db, task_id)
    if not task or task.user_id != user_id:
        return None
    if agent_id is not None and task.agent_id != agent_id:
        return None
    return task


def can_read_skill(skill: Skill, user_id: int, db=None) -> bool:
    """`db` 省略时行为跟以前完全一样（owner 或公开）。传了 `db` 才会额外判断部门/企业
    共享范围——只有 `service/skills_core/binding.py` 里"把 Skill 绑到 Agent"这个场景传，
    其余大量调用点（校验、导入导出、CRUD 详情）保持旧行为不变，不强行都改一遍签名。"""
    if not skill:
        return False
    if skill.user_id == user_id or skill.is_public == 1:
        return True
    if db is None:
        return False
    from models.enterprise_dao import is_team_member_of_team, is_org_member

    if skill.scope_type == "department" and is_team_member_of_team(db, user_id, skill.team_id):
        return True
    if skill.scope_type == "enterprise" and is_org_member(db, user_id):
        return True
    return False


def can_write_skill(skill: Skill, user_id: int) -> bool:
    return bool(skill and skill.user_id == user_id)


def can_bind_skill(skill: Skill, user_id: int) -> bool:
    """能不能把这个 Skill 绑到某个 Agent 上——`can_read_skill` 只判断"看不看得到"，
    这里判断"绑不绑得了"，是两件事：作者自己永远能绑自己的 Skill（哪怕还是草稿，
    这就是草稿存在的意义：绑到自己的测试 Agent 上试跑），其他人只能绑
    `lifecycle_status == 'published'` 的 Skill——草稿/待审核/已退役的 Skill 对
    别人来说，就算是公开或部门/企业共享范围内，也还不到能用的阶段。"""
    from service.lifecycle import BINDABLE_BY_OTHERS_STATUSES

    if not skill:
        return False
    if skill.user_id == user_id:
        return True
    return skill.lifecycle_status in BINDABLE_BY_OTHERS_STATUSES


def can_manage_skill(skill: Skill, user_id: int, allow_admin: bool = False) -> bool:
    """Owners manage their records; admin-only callers may manage every Skill."""
    return bool(skill and (allow_admin or skill.user_id == user_id))


def get_owned_memory(db, user_id: int, memory_id: int, agent_id: int = None) -> Optional[Memory]:
    memory = get_memory_by_id(db, memory_id)
    if not memory or memory.user_id != user_id:
        return None
    if agent_id is not None and memory.agent_id != agent_id:
        return None
    return memory


# ---------------------------------------------------------------------------
# 异步版本：AsyncSession 路由使用。归属规则与同步版保持一致，只是查询走
# 各自的 *_async_dao；skill 的读写判定是纯内存逻辑，两版共用上面的函数。
# ---------------------------------------------------------------------------

async def get_owned_agent_async(db, user_id: int, agent_id: int) -> Optional[Agent]:
    from models.agent_async_dao import get_agent_by_id_async

    agent = await get_agent_by_id_async(db, agent_id)
    if not agent or agent.user_id != user_id:
        return None
    return agent


async def get_usable_agent_async(db, user_id: int, agent_id: int) -> Optional[Agent]:
    """`get_usable_agent` 的异步版，语义完全一致（见其 docstring）。"""
    from models.agent_async_dao import get_agent_by_id_async
    from models.enterprise_dao import is_team_member_of_team_async, is_org_member_async

    agent = await get_agent_by_id_async(db, agent_id)
    if not agent:
        return None
    if agent.user_id == user_id:
        return agent
    if agent.scope_type == "department" and await is_team_member_of_team_async(db, user_id, agent.team_id):
        return agent
    if agent.scope_type == "enterprise" and await is_org_member_async(db, user_id):
        return agent
    return None


async def get_owned_conversation_async(
    db, user_id: int, conversation_id: int, agent_id: int = None
) -> Optional[Conversation]:
    from models.conversation_async_dao import get_owned_conversation_async as _dao

    return await _dao(db, user_id, conversation_id, agent_id)


async def get_owned_knowledge_async(
    db, user_id: int, knowledge_id: int, agent_id: int = None
) -> Optional[Knowledge]:
    from models.knowledge_async_dao import get_owned_knowledge_async as _dao

    return await _dao(db, user_id, knowledge_id, agent_id)


async def get_owned_run_async(
    db, user_id: int, run_id: int, agent_id: int = None
) -> Optional[AgentRun]:
    from models.agent_run_async_dao import get_owned_run_async as _dao

    return await _dao(db, user_id, run_id, agent_id)


async def get_owned_task_async(
    db, user_id: int, task_id: int, agent_id: int = None
) -> Optional[BackgroundTask]:
    from models.background_task_async_dao import get_owned_task_async as _dao

    return await _dao(db, user_id, task_id, agent_id)


async def get_owned_memory_async(
    db, user_id: int, memory_id: int, agent_id: int = None
) -> Optional[Memory]:
    from models.memory_async_dao import get_owned_memory_async as _dao

    return await _dao(db, user_id, memory_id, agent_id)


# ---------------------------------------------------------------------------
# 知识库空间（Knowledge Space）—— 隔离唯一入口。
# get_owned_space / user_space_ids 只回答「能不能读到这个空间」：
#   owner（knowledge_spaces.user_id）、space_members 里有任意角色、或者是该空间所属
#   部门（team_id）的 team admin（Phase 3B 接入，docs/enterprise-rbac-plan.md）即可。
# 写权限（改空间 / 传文档 / 删）由 service 层再取 get_space_role + membership.can_*。
# 阶段6/Phase 3B 只改这里的实现，调用点不变。
# ---------------------------------------------------------------------------

def get_owned_space(db, user_id: int, space_id: int):
    """能读到该空间则返回 space，否则 None（owner / 任意角色成员 / 所属部门的 team admin）。"""
    from models.knowledge_space_dao import get_space_by_id
    from models.space_member_dao import get_role
    from models.enterprise_dao import is_team_admin_of_team

    space = get_space_by_id(db, space_id)
    if not space:
        return None
    if space.user_id == user_id:
        return space
    if get_role(db, space_id, user_id) is not None:
        return space
    return space if is_team_admin_of_team(db, user_id, space.team_id) else None


def user_space_ids(db, user_id: int) -> set:
    from models.knowledge_space_dao import list_spaces_by_user
    from models.space_member_dao import list_space_ids_for_member
    from models.enterprise_dao import list_space_ids_where_team_admin

    ids = {s.id for s in list_spaces_by_user(db, user_id)}
    ids.update(list_space_ids_for_member(db, user_id))
    ids.update(list_space_ids_where_team_admin(db, user_id))
    return ids


def get_space_role(db, user_id: int, space_id: int):
    """当前用户对该空间的角色：owner / admin / editor / viewer / None。"""
    from models.knowledge_space_dao import get_space_by_id
    from models.space_member_dao import get_role
    from models.enterprise_dao import is_team_admin_of_team
    from service.knowledge_space.membership import resolve_role

    space = get_space_by_id(db, space_id)
    if not space:
        return None
    member_role = get_role(db, space_id, user_id)
    is_team_admin = is_team_admin_of_team(db, user_id, space.team_id)
    return resolve_role(user_id, space, member_role, is_team_admin=is_team_admin)


async def get_owned_space_async(db, user_id: int, space_id: int):
    from models.knowledge_space_async_dao import get_owned_space_async as _owner_dao
    from models.space_member_dao import get_role_async
    from models.enterprise_dao import is_team_admin_of_team_async
    from models.knowledge_space_async_dao import get_space_by_id_async

    space = await _owner_dao(db, user_id, space_id)   # owner-only 快路径
    if space is not None:
        return space
    if await get_role_async(db, space_id, user_id) is not None:
        return await get_space_by_id_async(db, space_id)
    space = await get_space_by_id_async(db, space_id)
    if space is not None and await is_team_admin_of_team_async(db, user_id, space.team_id):
        return space
    return None


async def user_space_ids_async(db, user_id: int) -> set:
    from models.knowledge_space_async_dao import user_space_ids_async as _dao
    from models.space_member_dao import list_space_ids_for_member_async
    from models.enterprise_dao import list_space_ids_where_team_admin_async

    ids = set(await _dao(db, user_id))
    ids.update(await list_space_ids_for_member_async(db, user_id))
    ids.update(await list_space_ids_where_team_admin_async(db, user_id))
    return ids


async def get_space_role_async(db, user_id: int, space_id: int):
    from models.knowledge_space_async_dao import get_space_by_id_async
    from models.space_member_dao import get_role_async
    from models.enterprise_dao import is_team_admin_of_team_async
    from service.knowledge_space.membership import resolve_role

    space = await get_space_by_id_async(db, space_id)
    if space is None:
        return None
    member_role = await get_role_async(db, space_id, user_id)
    is_team_admin = await is_team_admin_of_team_async(db, user_id, space.team_id)
    return resolve_role(user_id, space, member_role, is_team_admin=is_team_admin)
