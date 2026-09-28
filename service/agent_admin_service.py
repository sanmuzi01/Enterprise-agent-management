"""中央/部门 Agent 的管理后台服务（创建/绑部门/发布/停用）。

补的是"中央 Agent 管理部门 Agent"这个目标下最后一块缺口：`agent_type`/
`department_code`/`organization_id`/`team_id`/`scope_type` 这些字段 Phase 3D
阶段1/3 就有了，中央路由（`service/runtime/central_router.py`）也认这些字段，
但一直没有创建/维护它们的管理入口——只能直接改数据库。普通用户的创建接口
（`POST /agent`）不接受 `agent_type`/`department_code`（见
docs/enterprise-rbac-plan.md 第15节），中央/部门 Agent 只能从这里建。

网关跟 `service/organization_admin_service.py` 一样：走平台超级管理员
（`get_current_admin_user_async`），不是企业内部角色，理由见那个文件顶部注释。

发布状态（`lifecycle_status`）是这里真正的把关点：新建的央/部门 Agent 默认
`draft`，`central_router` 只会路由到 `published` 的（见 docs/
enterprise-rbac-plan.md 第20节）——管理员可以先建、先配 prompt、先测，确认没
问题再发布，不会一建好就立刻影响真实用户的路由。`row_version` 乐观锁只在这里
（管理员多人协作编辑同一个央/部门 Agent 才有实际冲突风险）接入，不碰
`service/agent_service.py`/`models/agent_dao.py` 那条给普通用户个人 Agent 用的
既有更新路径——那条单一所有者编辑，并发冲突风险低，且是全项目测试最多的热路径
之一，不做没必要的改动。
"""
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from models.init_db import Agent, Team
from service import audit_service
from service.exceptions import Conflict, InvalidInput, NotFound
from service.lifecycle import VALID_LIFECYCLE_STATUSES
from service.runtime.central_router import VALID_DEPARTMENT_CODES

VALID_MANAGED_AGENT_TYPES = {"central", "department"}


def _agent_to_dict(agent: Agent, team_name: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "agent_type": agent.agent_type,
        "department_code": agent.department_code,
        "team_id": agent.team_id,
        "team_name": team_name,
        "model_name": agent.model_name,
        "lifecycle_status": agent.lifecycle_status,
        "row_version": agent.row_version,
    }


async def _get_default_organization_id(db) -> int:
    from service.organization_admin_service import _get_default_organization

    org = await _get_default_organization(db)
    return org.id


async def _get_active_team_or_404(db, team_id: int, organization_id: int) -> Team:
    team = (await db.execute(
        select(Team).where(Team.id == team_id, Team.organization_id == organization_id, Team.status == "active")
    )).scalar_one_or_none()
    if team is None:
        raise NotFound("部门不存在或已停用")
    return team


async def list_managed_agents(db) -> List[Dict[str, Any]]:
    """列出所有中央/部门 Agent（不含普通用户自己的 personal Agent）。"""
    result = await db.execute(
        select(Agent, Team.name)
        .outerjoin(Team, Team.id == Agent.team_id)
        .where(Agent.agent_type.in_(VALID_MANAGED_AGENT_TYPES))
        .order_by(Agent.id)
    )
    # Agent.skills 是 lazy=False（联表预加载），查完整 Agent 实体的结果集必须先
    # .unique() 去重（一个 Agent 绑了多个 Skill 会因为联表 JOIN 出现重复行），
    # 不然 SQLAlchemy 直接报错，不是可选的优化。
    return [_agent_to_dict(agent, team_name) for agent, team_name in result.unique().all()]


async def create_managed_agent(
        db, creator_user_id: int, name: str, agent_type: str,
        department_code: Optional[str] = None, team_id: Optional[int] = None,
        model_name: str = "glm-4",
        role: Optional[str] = None, task: Optional[str] = None,
        constraints: Optional[str] = None, output: Optional[str] = None,
) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise InvalidInput("名称不能为空")
    if agent_type not in VALID_MANAGED_AGENT_TYPES:
        raise InvalidInput(f"agent_type 只能是 {sorted(VALID_MANAGED_AGENT_TYPES)} 之一")

    org_id = await _get_default_organization_id(db)

    team_name = None
    if agent_type == "department":
        if department_code not in VALID_DEPARTMENT_CODES:
            raise InvalidInput(f"department_code 只能是 {sorted(VALID_DEPARTMENT_CODES)} 之一")
        if team_id is None:
            raise InvalidInput("部门 Agent 必须绑定一个部门（team_id）")
        team = await _get_active_team_or_404(db, team_id, org_id)
        team_name = team.name
        scope_type = "department"
    else:
        department_code = None
        team_id = None
        scope_type = "enterprise"

    agent = Agent(
        user_id=creator_user_id, name=name, model_name=model_name,
        organization_id=org_id, team_id=team_id, scope_type=scope_type,
        agent_type=agent_type, department_code=department_code,
        lifecycle_status="draft",
    )
    db.add(agent)
    await db.flush()

    from prompt.prompt_manager import create_prompt_file
    agent.prompt_file = create_prompt_file(agent.id, role, task, constraints, output)

    await db.commit()
    await audit_service.record_async(
        creator_user_id, "org.managed_agent_created", resource_type="agent", resource_id=agent.id,
        detail={"name": agent.name, "agent_type": agent_type, "department_code": department_code, "team_id": team_id},
    )
    return _agent_to_dict(agent, team_name)


async def update_managed_agent(
        db, agent_id: int, operator_id: int, name: Optional[str] = None, department_code: Optional[str] = None,
        team_id: Optional[int] = None, model_name: Optional[str] = None,
        lifecycle_status: Optional[str] = None, expected_row_version: Optional[int] = None,
        role: Optional[str] = None, task: Optional[str] = None,
        constraints: Optional[str] = None, output: Optional[str] = None,
) -> Dict[str, Any]:
    agent = (await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.agent_type.in_(VALID_MANAGED_AGENT_TYPES))
    )).unique().scalar_one_or_none()
    if agent is None:
        raise NotFound("Agent 不存在")

    values: Dict[str, Any] = {}
    if name is not None:
        name = name.strip()
        if not name:
            raise InvalidInput("名称不能为空")
        values["name"] = name
    if model_name is not None:
        values["model_name"] = model_name
    if lifecycle_status is not None:
        if lifecycle_status not in VALID_LIFECYCLE_STATUSES:
            raise InvalidInput(f"lifecycle_status 只能是 {sorted(VALID_LIFECYCLE_STATUSES)} 之一")
        values["lifecycle_status"] = lifecycle_status
    if agent.agent_type == "department" and (department_code is not None or team_id is not None):
        new_code = department_code if department_code is not None else agent.department_code
        new_team_id = team_id if team_id is not None else agent.team_id
        if new_code not in VALID_DEPARTMENT_CODES:
            raise InvalidInput(f"department_code 只能是 {sorted(VALID_DEPARTMENT_CODES)} 之一")
        await _get_active_team_or_404(db, new_team_id, agent.organization_id)
        values["department_code"] = new_code
        values["team_id"] = new_team_id

    if agent.agent_type == "department" and ("lifecycle_status" in values or "department_code" in values):
        # P1 修复：同一个 department_code 同时只能有一个 published 的部门
        # Agent——不然 central_router._find_department_agent 查出多条候选，
        # 选哪个是未定义行为。`department_publish_key` 只在 published 时等于
        # department_code 本身，其余状态一律清空；真正的互斥由
        # models/init_db.py::Agent 上的唯一约束保证，这里只负责维护这一列跟
        # "最终会变成什么状态"保持一致（用 values.get(...) 兜底还没被这次请求
        # 改动的字段，取 agent 当前值）。
        final_status = values.get("lifecycle_status", agent.lifecycle_status)
        final_code = values.get("department_code", agent.department_code)
        values["department_publish_key"] = final_code if final_status == "published" else None

    touching_prompt = any(v is not None for v in (role, task, constraints, output))
    if values or touching_prompt:
        # 乐观锁必须覆盖"只改 Prompt（role/task/constraints/output）"这种情况——
        # 之前只有 values（name/model_name/lifecycle_status/department_code/
        # team_id 这些 DB 字段）非空才会走这段递增+比对，纯改 Prompt 完全不会碰
        # row_version，两个管理员并发改同一个 Agent 的 Prompt 会互相覆盖都不知道。
        # 哪怕 values 是空字典也要走一次 UPDATE，只为了递增 row_version 和比对。
        values["row_version"] = Agent.row_version + 1
        stmt = sa_update(Agent).where(Agent.id == agent_id)
        if expected_row_version is not None:
            stmt = stmt.where(Agent.row_version == expected_row_version)
        # 执行前先把报错要用到的值取出来存成本地变量——执行失败并 rollback()
        # 之后，`agent` 这个 ORM 对象的属性会被标记过期，这时再访问
        # `agent.department_code` 会触发一次隐式懒加载查询，但在 AsyncSession
        # 里这种"裸属性访问"没办法正确 await，会直接报
        # `MissingGreenlet`——不是"检查失败"，是访问本身就出错，绝对不能在
        # except 块里再碰 `agent` 的任何列属性。
        department_code_for_error = values.get("department_code", agent.department_code)
        try:
            result = await db.execute(stmt.values(**values))
        except IntegrityError:
            await db.rollback()
            raise InvalidInput(
                f"部门「{department_code_for_error}」"
                "已经有一个已发布的 Agent 了，请先把旧的退役再发布这一个"
            )
        if result.rowcount == 0:
            await db.refresh(agent)
            raise Conflict(f"Agent 已被其他人修改（当前版本 {agent.row_version}），请刷新后重试")

    if touching_prompt:
        from prompt.prompt_manager import read_prompt_file, update_prompt_file
        existing = read_prompt_file(agent_id) or {}
        update_prompt_file(
            agent_id,
            role=role if role is not None else existing.get("role"),
            task=task if task is not None else existing.get("task"),
            constraints=constraints if constraints is not None else existing.get("constraints"),
            output=output if output is not None else existing.get("output"),
        )

    await db.commit()
    await db.refresh(agent)
    team_name = None
    if agent.team_id:
        team_name = (await db.execute(select(Team.name).where(Team.id == agent.team_id))).scalar_one_or_none()

    if values or touching_prompt:
        action = f"org.managed_agent_{lifecycle_status}" if lifecycle_status else "org.managed_agent_updated"
        await audit_service.record_async(
            operator_id, action, resource_type="agent", resource_id=agent.id,
            detail={k: v for k, v in values.items() if k != "row_version"},
        )
    return _agent_to_dict(agent, team_name)
