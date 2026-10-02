"""测试清理：删除绑定在指定部门上的 Agent，连同专业技能记录和磁盘上的 Prompt/技能配置文件。

建部门会自动生成部门 Agent（service/department_agent_service.py），只删 teams 会撞
fk_agent_team 外键；只删数据库记录又会在 prompt/prompts、skills/enterprise 下留文件。
"""
import os

from sqlalchemy import text


def purge_team_agents(db, team_ids) -> None:
    team_ids = [int(t) for t in team_ids if t is not None]
    if not team_ids:
        return
    from prompt.prompt_manager import delete_prompt_file
    import service.skills.loader as skill_loader

    teams_sql = ",".join(str(t) for t in team_ids)
    db.commit()
    agent_ids = [r[0] for r in db.execute(text(f"SELECT id FROM agent WHERE team_id IN ({teams_sql})")).fetchall()]
    if agent_ids:
        ids_sql = ",".join(str(a) for a in agent_ids)
        skill_ids = [r[0] for r in db.execute(text(
            f"SELECT skill_id FROM agent_skill WHERE agent_id IN ({ids_sql})")).fetchall()]
        db.execute(text(f"DELETE FROM agent_skill WHERE agent_id IN ({ids_sql})"))
        if skill_ids:
            db.execute(text("DELETE FROM skill_version WHERE skill_id IN ({})".format(",".join(map(str, skill_ids)))))
            db.execute(text("DELETE FROM skill WHERE id IN ({}) AND config_file LIKE 'enterprise/agent_%'".format(
                ",".join(map(str, skill_ids)))))
        db.execute(text(f"UPDATE `user` SET selected_agent_id=NULL WHERE selected_agent_id IN ({ids_sql})"))
        db.execute(text(f"DELETE FROM agent WHERE id IN ({ids_sql})"))
    db.execute(text(f"DELETE FROM skill WHERE team_id IN ({teams_sql}) AND config_file LIKE 'enterprise/agent_%'"))
    db.commit()
    for agent_id in agent_ids:
        delete_prompt_file(agent_id)
        path = skill_loader._get_yml_path(f"enterprise/agent_{agent_id}.yml")
        if os.path.exists(path):
            os.remove(path)
