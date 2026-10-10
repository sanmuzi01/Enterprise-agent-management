"""部门专业 Agent 的自动配置：每个部门按业务类型（Team.department_code）拥有一个主 Agent。

- 建部门、修改业务类型、重新启用部门时，按模板自动生成草稿 Agent 并绑定专业技能；
- 停用部门时，该部门已发布的 Agent 同步退役；
- 配置状态可诊断（缺失、未绑定技能、模型无效、与业务类型冲突、待发布），并可"一键修复"；
- 发布仍需管理员确认（一键发布），草稿不会自动影响员工。
"""
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from models.init_db import Agent, Skill, Team, agent_skill
from service import agent_admin_service
from service.enterprise_agent_templates import get_template, template_id_for_department
from service.exceptions import InvalidInput, NotFound
from service.lifecycle import RETIRED_STATUS

STATE_LABELS = {
    "ready": "已发布，可使用",
    "pending_publish": "已配置，待发布",
    "needs_repair": "配置不完整，需要修复",
    "team_disabled": "部门已停用",
}


async def _team(db, team_id: int) -> Team:
    team = (await db.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if team is None:
        raise NotFound("部门不存在")
    return team


async def _team_agents(db, team_id: int) -> List[Agent]:
    return list((await db.execute(
        select(Agent).where(Agent.agent_type == "department", Agent.team_id == team_id).order_by(Agent.id)
    )).unique().scalars().all())


async def _has_template_skill(db, agent: Agent) -> bool:
    found = (await db.execute(
        select(Skill.id).join(agent_skill, agent_skill.c.skill_id == Skill.id)
        .where(agent_skill.c.agent_id == agent.id, Skill.config_file == f"enterprise/agent_{agent.id}.yml")
    )).first()
    return found is not None


def _primary(agents: List[Agent], expected_code: Optional[str]) -> Optional[Agent]:
    matching = [a for a in agents if a.department_code == expected_code and a.lifecycle_status != RETIRED_STATUS]
    return next((a for a in matching if a.lifecycle_status == "published"), matching[0] if matching else None)


def _conflicts(agents: List[Agent], expected_code: Optional[str]) -> List[Agent]:
    # 部门没设业务类型时不判冲突：管理员可能手工给它配了专业 Agent。
    if expected_code is None:
        return []
    return [a for a in agents if a.lifecycle_status == "published" and a.department_code != expected_code]


async def agent_status(db, team_id: int) -> Dict[str, Any]:
    team = await _team(db, team_id)
    agents = await _team_agents(db, team.id)
    template = get_template(template_id_for_department(team.department_code))
    primary = _primary(agents, team.department_code)
    issues: List[str] = []
    if team.status != "active":
        state = "team_disabled"
        issues.append("部门已停用，部门 Agent 不对员工开放")
    else:
        if primary is None:
            issues.append(f"还没有与业务类型匹配的「{template['name']}」")
        else:
            if template["tools"] and not await _has_template_skill(db, primary):
                issues.append("专业技能未绑定，Agent 无法调用业务工具")
            elif template["tools"] and agent_admin_service.template_skill_file_missing(primary):
                issues.append("专业技能配置文件丢失（例如容器重建后没有保留），Agent 无法调用业务工具，一键修复会重新生成")
            elif template["tools"]:
                missing = agent_admin_service.template_skill_missing_tools(primary, template)
                if missing:
                    issues.append(f"专业技能缺少 {len(missing)} 项新能力（模板已更新），一键修复会补上")
            from service.llm.model_catalog import CHAT_MODELS, normalize_model_name
            if normalize_model_name(primary.model_name or "") not in CHAT_MODELS:
                issues.append(f"模型「{primary.model_name}」不是可用的聊天模型")
        for agent in _conflicts(agents, team.department_code):
            issues.append(f"已发布的「{agent.name}」与部门业务类型不一致")
        if issues:
            state = "needs_repair"
        elif primary.lifecycle_status == "published":
            state = "ready"
        else:
            state = "pending_publish"
            issues.append("Agent 已配置，发布后员工才能在部门工作台使用")
    return {
        "team_id": team.id, "team_name": team.name, "department_code": team.department_code,
        "template_id": template["id"], "template_name": template["name"],
        "state": state, "state_label": STATE_LABELS[state], "issues": issues,
        "agent": None if primary is None else {
            "id": primary.id, "name": primary.name, "lifecycle_status": primary.lifecycle_status,
            "model_name": primary.model_name, "row_version": primary.row_version,
        },
    }


async def repair(db, team_id: int, operator_id: int) -> Dict[str, Any]:
    """让部门回到"恰好有一个与业务类型匹配、绑定了专业技能的主 Agent"的状态。可重复执行。"""
    team = await _team(db, team_id)
    if team.status != "active":
        raise InvalidInput("部门已停用，请先启用部门再修复部门 Agent")
    agents = await _team_agents(db, team.id)
    template_id = template_id_for_department(team.department_code)
    template = get_template(template_id)
    for agent in _conflicts(agents, team.department_code):
        await agent_admin_service.update_managed_agent(db, agent.id, operator_id, lifecycle_status=RETIRED_STATUS)
    primary = _primary(agents, team.department_code)
    if primary is None:
        retired = [a for a in agents if a.department_code == team.department_code and a.lifecycle_status == RETIRED_STATUS]
        if retired:
            # 部门重新启用：恢复最近一个匹配的 Agent 为草稿，而不是再造一个新的。
            primary = retired[-1]
            await agent_admin_service.update_managed_agent(db, primary.id, operator_id, lifecycle_status="draft")
        else:
            await agent_admin_service.create_managed_agent(
                db, operator_id, f"{team.name} · {template['name']}", "department",
                department_code=team.department_code, team_id=team.id, template_id=template_id,
                organization_id=team.organization_id,
            )
            return await agent_status(db, team.id)
    if template["tools"] and not await _has_template_skill(db, primary):
        await agent_admin_service.bind_template_skill(db, primary, template, primary.user_id)
        await db.commit()
    elif template["tools"] and agent_admin_service.template_skill_file_missing(primary):
        agent_admin_service.restore_template_skill_file(primary, template)
        from service import audit_service
        await audit_service.record_async(
            operator_id, "agent.template_skill_restored", resource_type="agent", resource_id=primary.id,
            detail={"team_id": team.id, "template_id": template_id},
        )
    elif template["tools"]:
        added = await agent_admin_service.sync_template_skill_tools(db, primary, template)
        if added:
            from service import audit_service
            await audit_service.record_async(
                operator_id, "agent.template_tools_synced", resource_type="agent", resource_id=primary.id,
                detail={"team_id": team.id, "template_id": template_id, "added_tools": added},
            )
    if not primary.model_name:
        await agent_admin_service.update_managed_agent(db, primary.id, operator_id, model_name="glm-4")
    return await agent_status(db, team.id)


async def publish(db, team_id: int, operator_id: int) -> Dict[str, Any]:
    status = await agent_status(db, team_id)
    if status["state"] in ("needs_repair", "team_disabled"):
        raise InvalidInput("部门 Agent 暂不能发布：" + "；".join(status["issues"]))
    if status["state"] == "pending_publish":
        await agent_admin_service.update_managed_agent(
            db, status["agent"]["id"], operator_id, lifecycle_status="published",
            expected_row_version=status["agent"]["row_version"],
        )
    return await agent_status(db, team_id)


async def retire_team_agents(db, team_id: int, operator_id: int) -> None:
    for agent in await _team_agents(db, team_id):
        if agent.lifecycle_status == "published":
            await agent_admin_service.update_managed_agent(db, agent.id, operator_id, lifecycle_status=RETIRED_STATUS)


async def on_team_saved(db, team_id: int, operator_id: int, *, created: bool = False,
                        department_code_changed: bool = False, status: Optional[str] = None) -> Dict[str, Any]:
    """建部门/改业务类型/启停部门之后调用，保持部门 Agent 与部门状态同步。"""
    if status == "disabled":
        await retire_team_agents(db, team_id, operator_id)
    elif created or department_code_changed or status == "active":
        await repair(db, team_id, operator_id)
    return await agent_status(db, team_id)
