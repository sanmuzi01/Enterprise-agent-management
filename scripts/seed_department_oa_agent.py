"""部门工作台本地/演示环境用的便利脚本，幂等地建好一个部门 + 对应业务模板的 Agent
并发布，省得每次手动在管理后台点几遍。生产环境走管理员在 `AdminOrganization.vue`
里手动建，这个脚本不是必须的生产路径。默认建人事部（里程碑1），传
`--department-code procurement --template-id procurement` 就能建采购部（里程碑2）。

用法：
    .venv/Scripts/python.exe scripts/seed_department_oa_agent.py --user <user_id> [--team-name 人事部]
    .venv/Scripts/python.exe scripts/seed_department_oa_agent.py --user <user_id> \
        --team-name 采购部 --department-code procurement --template-id procurement --agent-name "采购与库存助手"

`--user` 是一个已存在的用户 id，会被加进新建/已有的这个部门当负责人（如果还
不是的话）——不会创建新用户，也不会动其它任何用户的数据。
"""
import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


async def _run(user_id: int, team_name: str, org_name: str, department_code: str, template_id: str,
                agent_name: str) -> None:
    from sqlalchemy import select

    from models.async_db import AsyncSessionLocal
    from models.init_db import Agent, EnterpriseRole, Organization, OrganizationMember, Team, TeamMember
    from service import agent_admin_service
    from utils.timeutil import utcnow

    async with AsyncSessionLocal() as db:
        org = (await db.execute(select(Organization).where(Organization.status == "active"))).scalars().first()
        if org is None:
            org = Organization(name=org_name, owner_user_id=user_id, status="active")
            db.add(org)
            await db.flush()
            print(f"[建企业] {org.name} (id={org.id})")
        else:
            print(f"[企业已存在] {org.name} (id={org.id})")

        org_admin_role = (await db.execute(
            select(EnterpriseRole).where(EnterpriseRole.scope == "organization", EnterpriseRole.code == "admin")
        )).scalars().first()
        existing_org_member = (await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org.id, OrganizationMember.user_id == user_id,
            )
        )).scalars().first()
        if existing_org_member is None:
            db.add(OrganizationMember(
                organization_id=org.id, user_id=user_id, role_id=org_admin_role.id,
                status="active", created_at=utcnow(), updated_at=utcnow(),
            ))
            print(f"[加企业成员] user_id={user_id} role=admin")
        else:
            print(f"[企业成员已存在] user_id={user_id}")

        team = (await db.execute(
            select(Team).where(Team.organization_id == org.id, Team.name == team_name)
        )).scalars().first()
        if team is None:
            team = Team(organization_id=org.id, name=team_name, owner_user_id=user_id, status="active")
            db.add(team)
            await db.flush()
            print(f"[建部门] {team.name} (id={team.id})")
        else:
            print(f"[部门已存在] {team.name} (id={team.id})")

        team_admin_role = (await db.execute(
            select(EnterpriseRole).where(EnterpriseRole.scope == "team", EnterpriseRole.code == "admin")
        )).scalars().first()
        existing_team_member = (await db.execute(
            select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
        )).scalars().first()
        if existing_team_member is None:
            db.add(TeamMember(
                team_id=team.id, user_id=user_id, role_id=team_admin_role.id,
                status="active", created_at=utcnow(), updated_at=utcnow(),
            ))
            print(f"[加部门成员] user_id={user_id} role=admin（部门负责人）")
        else:
            print(f"[部门成员已存在] user_id={user_id}")

        await db.commit()

        agent = (await db.execute(
            select(Agent).where(Agent.team_id == team.id, Agent.agent_type == "department",
                                 Agent.department_code == department_code)
        )).scalars().first()
        if agent is None:
            created = await agent_admin_service.create_managed_agent(
                db, user_id, agent_name, "department",
                department_code=department_code, team_id=team.id, template_id=template_id,
            )
            print(f"[建 Agent] {created['name']} (id={created['id']})")
            agent_id = created["id"]
        else:
            print(f"[Agent 已存在] {agent.name} (id={agent.id}, lifecycle_status={agent.lifecycle_status})")
            agent_id = agent.id

        agent_row = await db.get(Agent, agent_id)
        if agent_row.lifecycle_status != "published":
            await agent_admin_service.update_managed_agent(db, agent_id, user_id, lifecycle_status="published")
            print(f"[发布 Agent] id={agent_id}")
        else:
            print(f"[Agent 已发布] id={agent_id}")

        print(f"\n完成：team_id={team.id}, agent_id={agent_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", type=int, required=True, help="作为部门负责人/企业管理员的用户 id")
    parser.add_argument("--team-name", default="人事部")
    parser.add_argument("--org-name", default="演示企业")
    parser.add_argument("--department-code", default="hr")
    parser.add_argument("--template-id", default="oa")
    parser.add_argument("--agent-name", default="人事 OA 助手")
    args = parser.parse_args()
    asyncio.run(_run(args.user, args.team_name, args.org_name, args.department_code, args.template_id,
                      args.agent_name))


if __name__ == "__main__":
    main()
