"""Enterprise home: actual memberships and usable published agents, never demo data."""
from sqlalchemy import select

from models.init_db import Agent, EnterpriseRole, LLMConfig, Organization, OrganizationMember, Team, TeamMember
from service.access_control import get_usable_agent_async
from service.enterprise_agent_templates import template_for_agent


async def get_workspace(db, user_id):
    memberships = (await db.execute(
        select(Organization.id, Organization.name, EnterpriseRole.name)
        .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
        .join(EnterpriseRole, EnterpriseRole.id == OrganizationMember.role_id)
        .where(OrganizationMember.user_id == user_id, OrganizationMember.status == "active",
               Organization.status == "active")
    )).all()
    org_ids = [row[0] for row in memberships]
    payload = {"organizations": [{"id": oid, "name": name, "role_name": role} for oid, name, role in memberships],
               "departments": [], "agents": []}
    if not org_ids:
        return payload
    rows = (await db.execute(
        select(Team.id, Team.name, EnterpriseRole.name, Team.department_code)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .join(EnterpriseRole, EnterpriseRole.id == TeamMember.role_id)
        .where(Team.organization_id.in_(org_ids), Team.status == "active",
               TeamMember.user_id == user_id, TeamMember.status == "active")
        .order_by(Team.id)
    )).all()
    payload["departments"] = [
        {"id": tid, "name": name, "role_name": role, "department_code": dept_code}
        for tid, name, role, dept_code in rows
    ]
    models = set((await db.execute(select(LLMConfig.model_name).where(
        LLMConfig.user_id == user_id, LLMConfig.is_active == 1))).scalars().all())
    candidates = (await db.execute(
        select(Agent, Team.name).outerjoin(Team, Team.id == Agent.team_id)
        .where(Agent.organization_id.in_(org_ids), Agent.agent_type.in_(["central", "department"]),
               Agent.lifecycle_status == "published")
        .order_by(Agent.id)
    )).unique().all()
    for agent, team_name in candidates:
        # An owner may use their draft in other entry points; this home only lists published agents
        # within active memberships. Department membership is required even for the creator.
        if agent.agent_type == "department" and agent.team_id not in {row[0] for row in rows}:
            continue
        if await get_usable_agent_async(db, user_id, agent.id) is None:
            continue
        template = template_for_agent(agent) or {}
        payload["agents"].append({
            "id": agent.id, "name": agent.name, "agent_type": agent.agent_type,
            "department_code": agent.department_code, "team_id": agent.team_id, "team_name": team_name,
            "model_name": agent.model_name, "model_configured": agent.model_name in models,
            "description": template.get("description", "部门专业助手"),
            "examples": template.get("examples", []),
            "skill_count": len(agent.skills or []),
        })
    return payload
