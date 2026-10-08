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


CONFIG_FLAGS = {"rag_enabled": "知识库检索", "memory_enabled": "长期记忆", "kb_rerank_enabled": "检索重排",
                "kb_force_citation": "强制引用来源", "kb_refuse_when_empty": "无命中时拒答"}
CONFIG_RANGES = {"temperature": ("温度", 0, 100), "kb_top_k": ("检索条数", 1, 20)}


def clean_config(config: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """智能体的运行参数：只认这几项，值必须在范围内；None 表示不改。"""
    cleaned: Dict[str, int] = {}
    for key, value in (config or {}).items():
        if value is None:
            continue
        if key in CONFIG_FLAGS:
            if value not in (0, 1, True, False):
                raise InvalidInput(f"「{CONFIG_FLAGS[key]}」只能是开或关")
            cleaned[key] = int(bool(value))
        elif key in CONFIG_RANGES:
            label, low, high = CONFIG_RANGES[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise InvalidInput(f"「{label}」需要在 {low} 到 {high} 之间")
            cleaned[key] = value
        else:
            raise InvalidInput(f"不支持的配置项：{key}")
    return cleaned


def check_model(model_name: Optional[str]) -> None:
    """平台自带运行方式用的模型必须是目录里的聊天模型（外部服务自己决定模型，不受此限）。"""
    from service.llm.model_catalog import CHAT_MODELS, normalize_model_name
    if normalize_model_name(model_name or "") not in CHAT_MODELS:
        raise InvalidInput(f"模型「{model_name}」不是可用的聊天模型，请从列表里选择")


async def validated_space_ids(db, space_ids) -> List[int]:
    """管理员可以给智能体绑定任何一个存在的知识库空间（不要求自己是空间成员）。"""
    from models.init_db import KnowledgeSpace
    ids = sorted({int(s) for s in (space_ids or [])})
    if ids:
        found = {r[0] for r in (await db.execute(select(KnowledgeSpace.id).where(KnowledgeSpace.id.in_(ids)))).all()}
        if found != set(ids):
            raise InvalidInput("包含不存在的知识库空间")
    return ids


async def validated_skill_ids(db, skill_ids, operator_id: int) -> List[int]:
    from service.access_control import can_bind_skill
    ids = sorted({int(s) for s in (skill_ids or [])})
    if ids:
        skills = (await db.execute(select(Skill).where(Skill.id.in_(ids)))).unique().scalars().all()
        if len(skills) != len(ids):
            raise InvalidInput("包含不存在的技能")
        unpublished = [s.name for s in skills if not can_bind_skill(s, operator_id)]
        if unpublished:
            raise InvalidInput(f"这些技能还没有发布，不能绑定：{'、'.join(unpublished)}")
    return ids


async def apply_space_ids(db, agent_id: int, space_ids: List[int]) -> None:
    from sqlalchemy import delete as sa_delete

    from models.init_db import AgentKnowledgeSpace
    await db.execute(sa_delete(AgentKnowledgeSpace).where(AgentKnowledgeSpace.agent_id == agent_id))
    for space_id in space_ids:
        db.add(AgentKnowledgeSpace(agent_id=agent_id, space_id=space_id))


async def apply_skill_ids(db, agent_id: int, skill_ids: List[int]) -> None:
    from sqlalchemy import delete as sa_delete
    await db.execute(sa_delete(agent_skill).where(agent_skill.c.agent_id == agent_id))
    for skill_id in skill_ids:
        await db.execute(agent_skill.insert().values(agent_id=agent_id, skill_id=skill_id))


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
    from service.organization_admin_service import _get_default_organization

    org = await _get_default_organization(db)       # 平台只服务一个企业
    result = await db.execute(
        select(Agent, Team.name)
        .outerjoin(Team, Team.id == Agent.team_id)
        .where(Agent.agent_type.in_(VALID_MANAGED_AGENT_TYPES), Agent.organization_id == org.id)
        .order_by(Agent.id)
    )
    # Agent.skills 是 lazy=False（联表预加载），查完整 Agent 实体的结果集必须先
    # .unique() 去重（一个 Agent 绑了多个 Skill 会因为联表 JOIN 出现重复行），
    # 不然 SQLAlchemy 直接报错，不是可选的优化。
    rows = result.unique().all()
    gaps = await knowledge_gaps(db, [row[0] for row in rows])
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
        config: Optional[Dict[str, Any]] = None, space_ids: Optional[List[int]] = None,
        skill_ids: Optional[List[int]] = None,
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

    check_model(model_name)
    cleaned_config = clean_config(config)
    bound_spaces = await validated_space_ids(db, space_ids) if space_ids is not None else None
    bound_skills = await validated_skill_ids(db, skill_ids, creator_user_id) if skill_ids is not None else None

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
        lifecycle_status="draft", **cleaned_config,
    )
    db.add(agent)
    await db.flush()

    from prompt.prompt_manager import create_prompt_file
    agent.prompt_file = create_prompt_file(agent.id, role, task, constraints, output)

    if template:
        await bind_template_skill(db, agent, template, creator_user_id)
    if bound_spaces is not None:
        await apply_space_ids(db, agent.id, bound_spaces)
    if bound_skills:
        from sqlalchemy import select as _select
        already = {r[0] for r in (await db.execute(_select(agent_skill.c.skill_id).where(agent_skill.c.agent_id == agent.id))).all()}
        for skill_id in bound_skills:
            if skill_id not in already:
                await db.execute(agent_skill.insert().values(agent_id=agent.id, skill_id=skill_id))
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
        config: Optional[Dict[str, Any]] = None, space_ids: Optional[List[int]] = None,
        skill_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    agent = (await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.agent_type.in_(VALID_MANAGED_AGENT_TYPES))
    )).unique().scalar_one_or_none()
    if agent is None:
        raise NotFound("Agent 不存在")

    values: Dict[str, Any] = {}
    # 绑定先校验（只读），通过了再写，避免写了一半才发现某个技能不存在
    bound_spaces = await validated_space_ids(db, space_ids) if space_ids is not None else None
    bound_skills = await validated_skill_ids(db, skill_ids, operator_id) if skill_ids is not None else None
    values.update(clean_config(config))
    if name is not None:
        name = name.strip()
        if not name:
            raise InvalidInput("名称不能为空")
        values["name"] = name
    if model_name is not None:
        if (agent.runtime_type or "builtin") != "external":
            check_model(model_name)
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
    touching_binding = bound_spaces is not None or bound_skills is not None
    if values or touching_prompt or touching_binding:
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

    if bound_spaces is not None:
        await apply_space_ids(db, agent_id, bound_spaces)
    if bound_skills is not None:
        await apply_skill_ids(db, agent_id, bound_skills)

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

    if values or touching_prompt or touching_binding:
        action = f"org.managed_agent_{lifecycle_status}" if lifecycle_status else "org.managed_agent_updated"
        detail = {k: v for k, v in values.items() if k != "row_version"}
        if bound_spaces is not None:
            detail["space_ids"] = bound_spaces
        if bound_skills is not None:
            detail["skill_ids"] = bound_skills
        await audit_service.record_async(operator_id, action, resource_type="agent", resource_id=agent.id, detail=detail)
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
        enterprise_id = await _get_default_organization_id(db)
        team = (await db.execute(select(Team).where(Team.id == team_id, Team.organization_id == enterprise_id))).scalar_one_or_none()
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


async def get_managed_agent_detail(db, agent_id: int) -> Dict[str, Any]:
    """编辑页要用的完整配置：设定、模型与参数、绑定的知识库和技能、划分状态、发布前检查。"""
    from models.init_db import AgentKnowledgeSpace

    row = (await db.execute(
        select(Agent, Team.name)
        .outerjoin(Team, Team.id == Agent.team_id)
        .where(Agent.id == agent_id, Agent.agent_type.in_(VALID_MANAGED_AGENT_TYPES))
    )).unique().first()
    if row is None:
        raise NotFound("智能体不存在")
    agent, team_name = row
    space_ids = [r[0] for r in (await db.execute(
        select(AgentKnowledgeSpace.space_id).where(AgentKnowledgeSpace.agent_id == agent_id).order_by(AgentKnowledgeSpace.space_id))).all()]
    skill_ids = [r[0] for r in (await db.execute(
        select(agent_skill.c.skill_id).where(agent_skill.c.agent_id == agent_id).order_by(agent_skill.c.skill_id))).all()]
    gaps = (await knowledge_gaps(db, [agent])).get(agent_id, [])
    detail = {
        **_agent_to_dict(agent, team_name),
        "config": {
            "temperature": agent.temperature, "memory_enabled": agent.memory_enabled, "rag_enabled": agent.rag_enabled,
            "kb_top_k": agent.kb_top_k, "kb_rerank_enabled": agent.kb_rerank_enabled,
            "kb_force_citation": agent.kb_force_citation, "kb_refuse_when_empty": agent.kb_refuse_when_empty,
        },
        "space_ids": space_ids,
        "skill_ids": skill_ids,
        "knowledge_gaps": gaps,
    }
    detail["readiness"] = await readiness_of(db, agent, detail)
    return detail


async def readiness_of(db, agent: Agent, detail: Dict[str, Any]) -> Dict[str, Any]:
    """发布前检查：error = 现在就不能发布；warn = 能发布但建议处理；ok = 没问题。"""
    from service.llm.model_catalog import CHAT_MODELS, normalize_model_name

    items: List[Dict[str, str]] = []

    def add(key: str, level: str, label: str, message: str) -> None:
        items.append({"key": key, "level": level, "label": label, "message": message})

    assignment = assignment_of(agent)
    if assignment == "unassigned":
        add("assignment", "error", "划分", "还没有划分给部门或全企业，先点「划分」")
    elif assignment == "enterprise":
        add("assignment", "ok", "划分", "全企业的成员都能使用")
    else:
        add("assignment", "ok", "划分", f"划分给了「{detail.get('team_name') or '部门'}」")

    if (agent.runtime_type or "builtin") == "external":
        from service.runtime.external_runtime import load_endpoint_async
        endpoint = await load_endpoint_async(db, agent.id)
        if endpoint is None:
            add("runtime", "error", "外部服务", "还没有配置服务地址")
        elif endpoint.last_test_ok:
            add("runtime", "ok", "外部服务", "连接测试已通过")
        else:
            add("runtime", "warn", "外部服务", "还没有测试通过连接，建议先点「测试连接」")
    else:
        prompt = detail.get("prompt") or {}
        if not (prompt.get("role") or prompt.get("task")):
            add("prompt", "warn", "助手设定", "还没有写角色设定和任务说明，助手只会按默认方式回答")
        else:
            add("prompt", "ok", "助手设定", "已填写")
        if normalize_model_name(agent.model_name or "") in CHAT_MODELS:
            add("model", "ok", "模型", "可用")
        else:
            add("model", "error", "模型", f"「{agent.model_name}」不是可用的聊天模型")
        if agent.rag_enabled and not detail["space_ids"]:
            add("knowledge", "warn", "知识库", "开启了知识库检索，但还没有绑定任何知识库")
        elif detail["knowledge_gaps"]:
            names = "、".join(g["name"] for g in detail["knowledge_gaps"][:3])
            add("knowledge", "warn", "知识库", f"有 {len(detail['knowledge_gaps'])} 份绑定的资料使用者读不到：{names}")
        elif agent.rag_enabled and assignment == "unassigned":
            add("knowledge", "ok", "知识库", f"已绑定 {len(detail['space_ids'])} 个知识库（划分之后会检查使用者能不能读到）")
        elif agent.rag_enabled:
            add("knowledge", "ok", "知识库", f"已绑定 {len(detail['space_ids'])} 个知识库，使用者都读得到")
        if detail["skill_ids"]:
            add("skills", "ok", "技能", f"已添加 {len(detail['skill_ids'])} 个技能")

    return {"ready": not any(i["level"] == "error" for i in items), "items": items}


async def agent_options(db, operator_id: int, agent_id: Optional[int] = None) -> Dict[str, Any]:
    """编辑页的下拉与多选需要的可选项：模型、技能、知识库、部门。"""
    from models.enterprise_dao import get_space_departments_async
    from models.init_db import KnowledgeSpace
    from service.llm.model_catalog import CHAT_MODELS

    skills = (await db.execute(select(Skill).order_by(Skill.id))).unique().scalars().all()
    own_template_file = f"enterprise/agent_{agent_id}.yml" if agent_id else None
    skill_items = []
    for s in skills:
        # 模板为某个智能体单独生成的“专业业务技能”只属于那个智能体，不出现在别的智能体的可选项里
        if (s.config_file or "").startswith("enterprise/agent_") and s.config_file != own_template_file:
            continue
        if s.user_id != operator_id and s.lifecycle_status != "published":
            continue
        skill_items.append({"id": s.id, "name": s.name, "description": s.description or "", "lifecycle_status": s.lifecycle_status})

    spaces = (await db.execute(
        select(KnowledgeSpace).where(KnowledgeSpace.status == "active").order_by(KnowledgeSpace.id.desc()))).scalars().all()
    departments = await get_space_departments_async(db, [s.id for s in spaces if s.scope_type == "department"])
    enterprise_id = await _get_default_organization_id(db)
    teams = (await db.execute(
        select(Team.id, Team.name, Team.department_code)
        .where(Team.status == "active", Team.organization_id == enterprise_id).order_by(Team.id))).all()
    return {
        "models": sorted(CHAT_MODELS.keys()),
        "skills": skill_items,
        "spaces": [{"id": s.id, "name": s.name, "scope_type": s.scope_type, "sensitivity": s.sensitivity,
                    "doc_count": s.doc_count, "departments": departments.get(s.id, [])} for s in spaces],
        "teams": [{"id": t[0], "name": t[1], "department_code": t[2]} for t in teams],
    }
