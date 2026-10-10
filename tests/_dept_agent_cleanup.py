"""测试清理：删除绑定在指定部门上的 Agent，连同专业技能记录和磁盘上的 Prompt/技能配置文件。

建部门会自动生成部门 Agent（service/department_agent_service.py），只删 teams 会撞
fk_agent_team 外键；只删数据库记录又会在 prompt/prompts、skills/enterprise 下留文件。
"""
import os

from sqlalchemy import text


def purge_agents(db, agent_ids) -> None:
    """删指定的 Agent，连同它们的专属技能记录、技能绑定和磁盘上的 Prompt / 技能配置文件。
    测试里直接 DELETE FROM agent 会留下 skills/enterprise/agent_<id>.yml（数据体检会把它们列成孤儿文件）。"""
    agent_ids = [int(a) for a in agent_ids if a is not None]
    if not agent_ids:
        return
    from prompt.prompt_manager import delete_prompt_file
    import service.skills.loader as skill_loader

    ids_sql = ",".join(str(a) for a in agent_ids)
    skill_ids = [r[0] for r in db.execute(text(
        f"SELECT skill_id FROM agent_skill WHERE agent_id IN ({ids_sql})")).fetchall()]
    private = [f"enterprise/agent_{a}.yml" for a in agent_ids]
    skill_ids += [r[0] for r in db.execute(text("SELECT id FROM skill WHERE config_file IN ({})".format(
        ",".join(f"'{c}'" for c in private)))).fetchall()]
    for table in ("agent_skill", "agent_knowledge_space", "agent_external_endpoint"):
        db.execute(text(f"DELETE FROM {table} WHERE agent_id IN ({ids_sql})"))
    if skill_ids:
        sk = ",".join(map(str, set(skill_ids)))
        db.execute(text(f"DELETE FROM agent_skill WHERE skill_id IN ({sk}) AND skill_id IN "
                        f"(SELECT id FROM (SELECT id FROM skill WHERE id IN ({sk}) AND config_file LIKE 'enterprise/agent_%') t)"))
        db.execute(text(f"DELETE FROM skill_version WHERE skill_id IN ({sk}) AND skill_id IN "
                        f"(SELECT id FROM (SELECT id FROM skill WHERE id IN ({sk}) AND config_file LIKE 'enterprise/agent_%') t)"))
        db.execute(text(f"DELETE FROM skill WHERE id IN ({sk}) AND config_file LIKE 'enterprise/agent_%'"))
    db.execute(text(f"UPDATE `user` SET selected_agent_id=NULL WHERE selected_agent_id IN ({ids_sql})"))
    db.execute(text(f"DELETE FROM agent WHERE id IN ({ids_sql})"))
    db.commit()
    for agent_id in agent_ids:
        delete_prompt_file(agent_id)
        path = skill_loader._get_yml_path(f"enterprise/agent_{agent_id}.yml")
        if os.path.exists(path):
            os.remove(path)


def purge_agents_named(db, like_pattern: str) -> None:
    """按名字前缀删测试建的 Agent（如 'acf-%'），文件一起清。"""
    db.commit()
    purge_agents(db, [r[0] for r in db.execute(text("SELECT id FROM agent WHERE name LIKE :p"), {"p": like_pattern}).fetchall()])


def purge_team_agents(db, team_ids) -> None:
    team_ids = [int(t) for t in team_ids if t is not None]
    if not team_ids:
        return
    teams_sql = ",".join(str(t) for t in team_ids)
    db.commit()
    purge_agents(db, [r[0] for r in db.execute(text(f"SELECT id FROM agent WHERE team_id IN ({teams_sql})")).fetchall()])
    db.execute(text(f"DELETE FROM skill WHERE team_id IN ({teams_sql}) AND config_file LIKE 'enterprise/agent_%'"))
    db.commit()
