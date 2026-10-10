"""中央 Agent 转交记录查询（管理后台审计用）。"""
import json
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import aliased

from models.init_db import Agent, AgentHandoff, User

REASON_LABELS = {"routed": "已转交部门助手", "own_department": "通用办公事务，交给员工本部门助手办理",
                 "no_match": "未命中任何部门，中央助手自己回答",
                 "no_usable_agent": "命中部门但你没有可用的部门助手，中央助手自己回答"}


async def list_handoffs(db, limit: int = 100, department_code: Optional[str] = None,
                        reason: Optional[str] = None) -> Dict[str, Any]:
    target = aliased(Agent)
    query = (select(AgentHandoff, User.name, Agent.name, target.name)
             .join(User, User.id == AgentHandoff.user_id)
             .outerjoin(Agent, Agent.id == AgentHandoff.central_agent_id)
             .outerjoin(target, target.id == AgentHandoff.target_agent_id))
    if department_code:
        query = query.where(AgentHandoff.department_code == department_code)
    if reason:
        query = query.where(AgentHandoff.reason == reason)
    rows = (await db.execute(query.order_by(AgentHandoff.id.desc()).limit(min(limit, 500)))).unique().all()
    items = []
    for handoff, user_name, central_name, target_name in rows:
        detail = json.loads(handoff.detail_json or "{}")
        items.append({
            "id": handoff.id, "user_id": handoff.user_id, "user_name": user_name,
            "central_agent": central_name, "target_agent": target_name, "target_agent_id": handoff.target_agent_id,
            "reason": handoff.reason, "reason_label": REASON_LABELS.get(handoff.reason, handoff.reason),
            "department_code": handoff.department_code, "matched_keywords": detail.get("matched_keywords", []),
            "alternatives": [a["name"] for a in detail.get("alternatives", [])],
            "unavailable": [u["department_code"] for u in detail.get("unavailable", [])],
            "message_excerpt": handoff.message_excerpt, "created_at": handoff.created_at.isoformat() + "Z",
        })
    return {"items": items}
