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

from models.init_db import Agent, Team, Skill, agent_skill
from service import audit_service
from service.exceptions import Conflict, InvalidInput, NotFound
from service.lifecycle import VALID_LIFECYCLE_STATUSES
from service.runtime.central_router import VALID_DEPARTMENT_CODES
from utils.logger_handler import get_logger

logger = get_logger("agent_admin_service")

VALID_MANAGED_AGENT_TYPES = {"central", "department"}


def assignment_of(agent: Agent) -> str:
    """划分状态：enterprise = 全企业可用；department = 划分给了某个部门；unassigned = 已创建、还没划分。"""
    if agent.agent_type == "central":
        return "enterprise"
    return "department" if agent.team_id is not None else "unassigned"


def _agent_to_dict(agent: Agent, team_name: Optional[str] = None) -> Dict[str, Any]:
    from prompt.prompt_manager import read_prompt_file
    return {
        "id": agent.id,
        "name": agent.name,
        "agent_type": agent.agent_type,
        "department_code": agent.department_code,
        "team_id": agent.team_id,
        "team_name": team_name,
        "assignment": assignment_of(agent),
        "model_name": agent.model_name,
        "runtime_type": agent.runtime_type or "builtin",
        "lifecycle_status": agent.lifecycle_status,
        "row_version": agent.row_version,
        "prompt": read_prompt_file(agent.id) or {},
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


async def _check_publishable(db, team_id: Optional[int], department_code: Optional[str], model_name: str,
                             runtime_type: str = "builtin") -> None:
    """发布前的配置检查：模型必须是可用的聊天模型；部门必须启用；部门配置了业务类型时，
    Agent 的业务方向必须与之一致（不能给销售部发布一个采购 Agent）。"""
    from service.llm.model_catalog import CHAT_MODELS, normalize_model_name
    # 外部 Agent 自己决定用什么模型，平台的模型清单对它不适用
    if runtime_type != "external" and normalize_model_name(model_name or "") not in CHAT_MODELS:
        raise InvalidInput(f"模型「{model_name}」不是可用的聊天模型，请先修改模型再发布")
    team = (await db.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if team is None or team.status != "active":
        raise InvalidInput("部门不存在或已停用，不能发布部门 Agent")
    if team.department_code is not None and department_code != team.department_code:
        raise InvalidInput(f"Agent 的业务方向（{department_code or '通用办公'}）与部门「{team.name}」"
                           f"的业务类型（{team.department_code}）不一致，请使用「一键修复」生成匹配的 Agent")


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
    rows = result.unique().all()
    gaps = await knowledge_gaps(db, [agent for agent, _ in rows])
    return [{**_agent_to_dict(agent, team_name), "knowledge_gaps": gaps.get(agent.id, [])} for agent, team_name in rows]


async def knowledge_gaps(db, agents: List[Agent]) -> Dict[int, List[Dict[str, Any]]]:
    """智能体绑定的知识库里，哪些是它的使用者读不到的。

    聊天时的检索按提问者本人的权限校验：智能体划分给了销售部，却绑着没划分给销售部的资料，
    销售部的人问问题时这份资料检索不到。这里提前给管理员提示，而不是等用户发现“怎么没答上来”。
    规则与 models/enterprise_dao.py 的“划分”一致：全企业资料所有人可读；部门资料需要划分给该部门；
    绝密资料不会被任何划分继承。未划分的智能体还没有使用者，不提示。"""
    from models.enterprise_dao import get_space_departments_async
    from models.init_db import AgentKnowledgeSpace, KnowledgeSpace

    audience = {a.id: a for a in agents if assignment_of(a) != "unassigned"}
    if not audience:
        return {}
    rows = (await db.execute(
        select(AgentKnowledgeSpace.agent_id, KnowledgeSpace)
        .join(KnowledgeSpace, KnowledgeSpace.id == AgentKnowledgeSpace.space_id)
        .where(AgentKnowledgeSpace.agent_id.in_(list(audience)))
    )).all()
    grants = await get_space_departments_async(db, [s.id for _, s in rows if s.scope_type == "department"])
    result: Dict[int, List[Dict[str, Any]]] = {}
    for agent_id, space in rows:
        agent = audience[agent_id]
        reason = None
        if space.sensitivity == "restricted":
            reason = "绝密资料不会自动开放给使用者"
        elif space.scope_type == "enterprise":
            reason = None
        elif agent.agent_type == "central":
            reason = "没有划分给全企业"
        elif space.scope_type == "department" and any(d["id"] == agent.team_id for d in grants.get(space.id, [])):
            reason = None
        else:
            reason = "没有划分给这个智能体所在的部门"
        if reason:
            result.setdefault(agent_id, []).append({"id": space.id, "name": space.name, "reason": reason})
    return result


def publish_key_for_team(team_id: Optional[int]) -> Optional[str]:
    """一个部门（Team）同时只能有一个已发布的部门 Agent。键按部门而不是按业务类型：
    "销售一部""销售二部"各自都能发布自己的 CRM Agent；同一个部门里不会出现两个
    互相冲突的已发布主 Agent（由 uq_agent_department_publish 唯一约束保证）。"""
    return None if team_id is None else f"t{team_id}"


async def bind_template_skill(db, agent: Agent, template: Dict[str, Any], owner_user_id: int) -> None:
    """给 Agent 生成一份独立的专业 Skill 配置并绑定。每个 Agent 单独一份，不共享——
    不然以后改其中一个 Agent 的工具权限会连带影响所有用同一模板建的其他 Agent。"""
    if not template["tools"]:
        return
    import yaml
    from service.skills_core.config_io import atomic_write_validated
    config_file = f"enterprise/agent_{agent.id}.yml"
    validation = atomic_write_validated(config_file, yaml.safe_dump({
        "name": template["name"], "description": template["description"], "version": "1.0",
        "tools": [{"name": tool} for tool in template["tools"]],
        "system_prompt": template["constraints"],
    }, allow_unicode=True))
    if not validation["ok"]:
        await db.rollback()
        raise InvalidInput("专业技能配置校验失败")
    skill = Skill(user_id=owner_user_id, name=f"{agent.name} · 专业业务技能",
                  description=template["description"], config_file=config_file,
                  organization_id=agent.organization_id, team_id=agent.team_id, scope_type=agent.scope_type,
                  lifecycle_status="published", is_public=0)
    db.add(skill)
    await db.flush()
    await db.execute(agent_skill.insert().values(agent_id=agent.id, skill_id=skill.id))


async def create_managed_agent(
        db, creator_user_id: int, name: str, agent_type: str,
        department_code: Optional[str] = None, team_id: Optional[int] = None,
        model_name: str = "glm-4",
        role: Optional[str] = None, task: Optional[str] = None,
        constraints: Optional[str] = None, output: Optional[str] = None,
        template_id: Optional[str] = None, organization_id: Optional[int] = None,
) -> Dict[str, Any]:
    template = None
    if template_id:
        from service.enterprise_agent_templates import get_template
        template = get_template(template_id)
        if agent_type != template["agent_type"] or department_code != template["department_code"]:
            raise InvalidInput("模板与 Agent 类型或业务方向不一致")
        role = role if role is not None else template["role"]
        task = task if task is not None else template["task"]
        constraints = constraints if constraints is not None else template["constraints"]
        output = output if output is not None else template["output"]
    name = (name or "").strip()
    if not name:
        raise InvalidInput("名称不能为空")
    if agent_type not in VALID_MANAGED_AGENT_TYPES:
        raise InvalidInput(f"agent_type 只能是 {sorted(VALID_MANAGED_AGENT_TYPES)} 之一")

    org_id = organization_id or await _get_default_organization_id(db)

    team_name = None
    if agent_type == "department":
        # department_code 为空表示"部门办公助手"（没有配置专属业务类型的部门用）。
        if department_code is not None and department_code not in VALID_DEPARTMENT_CODES:
            raise InvalidInput(f"department_code 只能是 {sorted(VALID_DEPARTMENT_CODES)} 之一或不填")
        if team_id is None:
            # 统一创建、再划分：先建好（草稿，只有创建者能试用），之后由管理员“划分”给部门
            scope_type = "personal"
        else:
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

    if template:
        await bind_template_skill(db, agent, template, creator_user_id)
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
        if new_code is not None and new_code not in VALID_DEPARTMENT_CODES:
            raise InvalidInput(f"department_code 只能是 {sorted(VALID_DEPARTMENT_CODES)} 之一")
        await _get_active_team_or_404(db, new_team_id, agent.organization_id)
        values["department_code"] = new_code
        values["team_id"] = new_team_id

    if (values.get("lifecycle_status") == "published" and agent.agent_type == "department"
            and values.get("team_id", agent.team_id) is None):
        raise InvalidInput("这个智能体还没有划分给部门，请先在列表里点「划分」，再发布")
    if agent.agent_type == "department" and (
            "lifecycle_status" in values or "department_code" in values or "team_id" in values):
        # 同一个部门（team）同时只能有一个 published 的部门 Agent：
        # `department_publish_key` 只在 published 时等于 "t{team_id}"，其余状态清空，
        # 互斥由 uq_agent_department_publish 唯一约束保证。用 values.get(...) 兜底
        # 本次请求没改的字段，取 agent 当前值。
        final_status = values.get("lifecycle_status", agent.lifecycle_status)
        final_code = values.get("department_code", agent.department_code)
        final_team_id = values.get("team_id", agent.team_id)
        if final_status == "published":
            await _check_publishable(db, final_team_id, final_code, values.get("model_name", agent.model_name),
                                     agent.runtime_type or "builtin")
        values["department_publish_key"] = publish_key_for_team(final_team_id) if final_status == "published" else None

    if values.get("lifecycle_status") == "published":
        from service.external_agent_admin_service import require_ready_to_publish
        await require_ready_to_publish(db, agent)

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
        try:
            result = await db.execute(stmt.values(**values))
        except IntegrityError:
            await db.rollback()
            raise InvalidInput("该部门已经有一个已发布的部门 Agent，请先把旧的退役再发布这一个")
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

    # 第五轮审计 P1-7：Prompt 文件和 DB 是两个不同的存储，没法用一个事务同时
    # 保证两边都成功。file 先原子替换、DB 最后才 commit——这样如果 commit
    # 失败，文件已经是"新内容"但 DB 的 row_version 还是旧的，不一致但至少
    # 文件内容本身是完整、有效的（不是本次修复要解决的截断/损坏问题）。这里
    # 再进一步：commit 失败时把文件写回编辑前的内容，让文件跟"没有真正提交
    # 成功"这件事保持一致，不留下一个内容比 DB 记录的版本还新的文件。
    try:
        await db.commit()
    except Exception:
        if touching_prompt:
            try:
                update_prompt_file(
                    agent_id,
                    role=existing.get("role"), task=existing.get("task"),
                    constraints=existing.get("constraints"), output=existing.get("output"),
                )
            except Exception:  # noqa: BLE001 —— 恢复失败也不能盖掉原始异常
                logger.warning(f"DB 提交失败后恢复 Prompt 文件也失败: agent_id={agent_id}", exc_info=True)
        raise
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


ASSIGNMENT_TARGETS = ("department", "enterprise", "unassigned")


async def assign_managed_agent(
        db, agent_id: int, operator_id: int, target: str, *, team_id: Optional[int] = None,
        department_code: Optional[str] = None, expected_row_version: Optional[int] = None,
) -> Dict[str, Any]:
    """划分智能体：划分给某个部门 / 全企业 / 收回成未划分。

    智能体统一创建（草稿），再由管理员决定它服务谁。已发布的智能体正在被使用，不能直接改划分，
    先停用再调整，避免正在聊天的人突然换了一个智能体或失去访问。
    划分给部门时：业务方向默认沿用部门已设置的业务类型；专业技能（由模板生成的）跟着换到新的部门范围。"""
    if target not in ASSIGNMENT_TARGETS:
        raise InvalidInput(f"划分方式只能是 {list(ASSIGNMENT_TARGETS)} 之一")
    agent = (await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.agent_type.in_(VALID_MANAGED_AGENT_TYPES))
    )).unique().scalar_one_or_none()
    if agent is None:
        raise NotFound("智能体不存在")
    if agent.lifecycle_status == "published":
        raise InvalidInput("已发布的智能体正在被使用，请先停用，再调整划分")

    values: Dict[str, Any] = {"department_publish_key": None}
    team_name = None
    if target == "department":
        if team_id is None:
            raise InvalidInput("请选择要划分给的部门")
        team = (await db.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
        if team is None or team.status != "active":
            raise InvalidInput("部门不存在或已停用")
        code = department_code if department_code is not None else (agent.department_code or team.department_code)
        if code is not None and code not in VALID_DEPARTMENT_CODES:
            raise InvalidInput(f"业务方向只能是 {sorted(VALID_DEPARTMENT_CODES)} 之一或不填")
        if team.department_code is not None and code != team.department_code:
            raise InvalidInput(f"智能体的业务方向（{code or '通用办公'}）与部门「{team.name}」的业务类型（{team.department_code}）不一致")
        team_name = team.name
        values.update(agent_type="department", team_id=team.id, department_code=code,
                      organization_id=team.organization_id, scope_type="department")
    elif target == "enterprise":
        values.update(agent_type="central", team_id=None, department_code=None,
                      organization_id=await _get_default_organization_id(db), scope_type="enterprise")
    else:
        values.update(agent_type="department", team_id=None, department_code=None, scope_type="personal")

    values["row_version"] = Agent.row_version + 1
    stmt = sa_update(Agent).where(Agent.id == agent_id)
    if expected_row_version is not None:
        stmt = stmt.where(Agent.row_version == expected_row_version)
    result = await db.execute(stmt.values(**values))
    if result.rowcount == 0:
        await db.refresh(agent)
        raise Conflict(f"智能体已被其他人修改（当前版本 {agent.row_version}），请刷新后重试")

    # 模板生成的专业技能跟着智能体换范围，否则技能还留在旧部门里
    skill_values = {k: values[k] for k in ("organization_id", "scope_type") if k in values}
    skill_values["team_id"] = values.get("team_id")
    await db.execute(sa_update(Skill).where(Skill.config_file == f"enterprise/agent_{agent_id}.yml").values(**skill_values))
    await db.commit()
    await db.refresh(agent)
    await audit_service.record_async(
        operator_id, "org.managed_agent_assigned", resource_type="agent", resource_id=agent_id,
        detail={"target": target, "team_id": values.get("team_id"), "department_code": values.get("department_code")},
    )
    return {**_agent_to_dict(agent, team_name), "knowledge_gaps": (await knowledge_gaps(db, [agent])).get(agent_id, [])}
