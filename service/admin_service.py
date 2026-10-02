"""管理员相关的基础判定 + 用户级联删除。

这个文件历史上还有一份完整的管理后台统计/列表实现（overview / list_users /
usage_stats 等），但 FasdtApi/admin.py 早就全部改成调 service/admin_async_service.py
的异步版本了——那份同步实现已经没有任何路由在用，2025 年审计发现后删除，
只留下真正还被引用的部分：角色/在线判定、当前用户 payload 组装（同步 + 异步两条
鉴权链路共用）、以及 delete_user（异步版通过线程池委托给这里，级联删除逻辑
只维护一份）。
"""
from utils.timeutil import utcnow
import os
from datetime import timedelta
from typing import Dict, List

from fastapi import HTTPException, status

from models.init_db import (
    Agent,
    BackgroundTask,
    Chat,
    LLMConfig,
    Memory,
    Skill,
    SkillVersion,
    User,
)


ADMIN_ROLE_NAMES = {"admin", "administrator", "管理员"}
ONLINE_WINDOW_SECONDS = int(os.getenv("ONLINE_WINDOW_SECONDS", "300"))


def role_names(user: User) -> List[str]:
    return [role.role_name for role in (user.roles or [])]


def is_admin_user(user: User) -> bool:
    names = {name.strip() for name in role_names(user)}
    if names.intersection(ADMIN_ROLE_NAMES):
        return True
    env_admins = {
        name.strip()
        for name in os.getenv("ADMIN_USER_NAMES", "admin").split(",")
        if name.strip()
    }
    return user.name in env_admins


def _format_dt(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def is_online_user(user: User) -> bool:
    last_seen_at = getattr(user, "last_seen_at", None)
    if not last_seen_at:
        return False
    return utcnow() - last_seen_at <= timedelta(seconds=ONLINE_WINDOW_SECONDS)


def current_user_payload(user: User) -> Dict:
    roles = role_names(user)
    return {
        "user_id": user.id,
        "username": user.name,
        "phone": getattr(user, "phone", None),
        "age": user.age,
        "is_disabled": getattr(user, "is_disabled", 0),
        "last_login_at": _format_dt(getattr(user, "last_login_at", None)),
        "last_seen_at": _format_dt(getattr(user, "last_seen_at", None)),
        "is_online": is_online_user(user),
        "selected_agent_id": user.selected_agent_id,
        "roles": roles,
        "is_admin": is_admin_user(user),
    }


def _user_admin_payload(
    user: User,
    agent_count: int = 0,
    skill_count: int = 0,
    knowledge_count: int = 0,
    task_count: int = 0,
) -> Dict:
    """组装管理员后台用户摘要，保证列表和详情字段一致。"""

    return {
        "id": user.id,
        "name": user.name,
        "phone": getattr(user, "phone", None),
        "age": user.age,
        "is_disabled": getattr(user, "is_disabled", 0),
        "last_login_at": _format_dt(getattr(user, "last_login_at", None)),
        "last_seen_at": _format_dt(getattr(user, "last_seen_at", None)),
        "is_online": is_online_user(user),
        "selected_agent_id": user.selected_agent_id,
        "roles": role_names(user),
        "is_admin": is_admin_user(user),
        "agent_count": int(agent_count or 0),
        "skill_count": int(skill_count or 0),
        "knowledge_count": int(knowledge_count or 0),
        "task_count": int(task_count or 0),
    }


def delete_user(db, user_id: int, operator_id: int) -> Dict:
    """删除用户及其全部级联数据。

    审计管理后台时发现：这个函数原来只清了 LLMConfig/Memory/BackgroundTask/Chat/角色
    这几张表——agent_service.delete() 循环能处理每个 Agent 名下的会话/运行记录/
    旧版私有知识库，但完全不知道"知识库空间"（现在所有新上传文档走的默认路径）、
    工作台组件、网页监控、操作日志这些表的存在。对任何真实用过产品的用户（几乎
    必然有至少一个 KnowledgeSpace），点"删除用户"会直接撞 FK 约束报 500，这个
    按钮实际上是坏的。现在按 tests/_route_client.py 里已经验证过的完整覆盖顺序补齐。
    """
    from sqlalchemy import text
    from models.init_db import agent_skill, association_table
    from service import agent_service

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {}
    if user.id == operator_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="不能删除当前登录的管理员账号")
    if user.name == "admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="不能删除内置管理员账号")

    # 1. 企业接口连接器 / 固定评估集：agent_api_connector.agent_id 和 eval_set.agent_id
    #    都外键引用 agent.id，必须在删 Agent 之前先清掉，顺序反了会在下面 agent_service.delete()
    #    里报 FK 约束失败（这个坑真实踩过一次：写成放在 agent 循环后面，测试直接炸了）。
    db.execute(text("DELETE FROM automation_work WHERE user_id = :uid"), {"uid": user.id})
    db.execute(text("DELETE FROM agent_api_connector WHERE user_id = :uid"), {"uid": user.id})
    db.execute(text(
        "DELETE er FROM eval_run er JOIN eval_set es ON er.eval_set_id = es.id "
        "WHERE es.user_id = :uid"
    ), {"uid": user.id})
    db.execute(text("DELETE FROM eval_set WHERE user_id = :uid"), {"uid": user.id})

    # 2. 逐个 Agent 显式级联（会话/消息、运行轨迹、旧版私有知识库+向量、记忆/工具/旧聊天）
    agents = db.query(Agent).filter(Agent.user_id == user.id).all()
    for agent in agents:
        agent_service.delete(db, user, agent.id)

    # 3. 知识库空间体系（当前所有新上传文档的默认路径，agent_service.delete 管不到）
    #    子表在前、KnowledgeSpace 本身在后；同时清"这个用户是别人空间的成员"和
    #    "别人是这个用户空间的成员"两个方向。
    db.execute(text("DELETE FROM kb_audit_log WHERE user_id = :uid"), {"uid": user.id})
    db.execute(text("DELETE FROM space_members WHERE user_id = :uid"), {"uid": user.id})
    db.execute(text(
        "DELETE sm FROM space_members sm JOIN knowledge_spaces s ON sm.space_id = s.id "
        "WHERE s.user_id = :uid"
    ), {"uid": user.id})
    db.execute(text(
        "DELETE aks FROM agent_knowledge_space aks JOIN knowledge_spaces s ON aks.space_id = s.id "
        "WHERE s.user_id = :uid"
    ), {"uid": user.id})
    db.execute(text("DELETE FROM knowledge_spaces WHERE user_id = :uid"), {"uid": user.id})

    # 4. 工作台组件（先删数据点，widget_data_points.widget_id -> user_widgets.id）
    db.execute(text(
        "DELETE dp FROM widget_data_points dp JOIN user_widgets w ON dp.widget_id = w.id "
        "WHERE w.user_id = :uid"
    ), {"uid": user.id})
    db.execute(text("DELETE FROM user_widgets WHERE user_id = :uid"), {"uid": user.id})

    # 5. 其它按 user_id 直接挂的表
    db.execute(text("DELETE FROM rag_debug_samples WHERE user_id = :uid"), {"uid": user.id})
    db.execute(text("DELETE FROM web_monitor WHERE user_id = :uid"), {"uid": user.id})
    db.execute(text("DELETE FROM user_workspace WHERE user_id = :uid"), {"uid": user.id})
    db.execute(text("DELETE FROM operation_log WHERE user_id = :uid"), {"uid": user.id})
    db.execute(text("DELETE FROM user_profile WHERE user_id = :uid"), {"uid": user.id})
    db.execute(text("DELETE FROM user_subscription WHERE user_id = :uid"), {"uid": user.id})

    # 6. Skill（agent_skill 绑定关系先清）
    skill_ids = [row[0] for row in db.query(Skill.id).filter(Skill.user_id == user.id).all()]
    if skill_ids:
        db.execute(agent_skill.delete().where(agent_skill.c.skill_id.in_(skill_ids)))
        db.query(SkillVersion).filter(SkillVersion.skill_id.in_(skill_ids)).delete(synchronize_session=False)
        db.query(Skill).filter(Skill.id.in_(skill_ids)).delete(synchronize_session=False)

    db.query(LLMConfig).filter(LLMConfig.user_id == user.id).delete(synchronize_session=False)
    db.query(Memory).filter(Memory.user_id == user.id).delete(synchronize_session=False)
    db.query(BackgroundTask).filter(BackgroundTask.user_id == user.id).delete(synchronize_session=False)
    db.query(Chat).filter(Chat.user_id == user.id).delete(synchronize_session=False)
    db.execute(association_table.delete().where(association_table.c.user_id == user.id))
    db.delete(user)
    db.flush()
    db.commit()
    return {"message": "用户已删除", "user_id": user_id}
