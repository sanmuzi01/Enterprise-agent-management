"""中央 Agent 受控路由（Phase 3D 阶段3，docs/enterprise-rbac-plan.md 9.5）。

规则路由，不需要独立 Runtime：关键词命中部门 → 在当前用户能用的已发布部门 Agent 里找那个部门的
Agent → 有就路由过去，没有就中央 Agent 自己回答。一次请求命中多个部门时，转给命中最强的那个，
其余有可用助手的部门作为"备选"返回给用户，由用户明确切换；每次转交决定都会记录。

只在**新建会话**（`conversation_id is None`）时路由一次；同一个会话后续消息不重新路由——会话一旦
创建就固定绑在某个 `agent.id` 上（`conversation.agent_id`），这是 `chat_service`/`access_control`
到处依赖的既有约束。

现存的个人 Agent 默认 `agent_type` 是 `personal`，对它们路由函数原样返回 `agent_id`，零行为变化。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from utils.logger_handler import get_logger

logger = get_logger("central_router")

VALID_AGENT_TYPES = {"central", "department", "personal"}
VALID_DEPARTMENT_CODES = {"hr", "procurement", "sales", "finance", "it"}


def match_departments(message: str) -> List[Tuple[str, List[str]]]:
    """返回 message 命中的所有部门及命中的关键词：命中词越多越靠前，相同时按模板声明顺序。
    关键词跟着部门 Agent 模板走（service/enterprise_agent_templates.py 的 routing_keywords 字段），
    新增真实部门业务闭环时模板里加这个字段就自动生效，不需要改这个文件。"""
    if not message:
        return []
    from service.enterprise_agent_templates import TEMPLATES
    hits = []
    for order, template in enumerate(TEMPLATES.values()):
        if template["agent_type"] != "department" or template.get("department_code") is None:
            continue
        matched = [kw for kw in template.get("routing_keywords", ()) if kw in message]
        if matched:
            hits.append((order, template["department_code"], matched))
    hits.sort(key=lambda h: (-len(h[2]), h[0]))
    return [(code, matched) for _, code, matched in hits]


def match_department(message: str) -> Optional[str]:
    """命中最多的那个部门代码；都没命中返回 None（中央 Agent 自己回答）。"""
    matches = match_departments(message)
    return matches[0][0] if matches else None


@dataclass
class RoutePlan:
    """一次中央 Agent 路由的决定：转给谁、为什么、还有哪些部门也相关（供用户明确切换）。"""
    target_agent_id: int
    central_agent_id: Optional[int] = None
    reason: str = "not_central"   # not_central / existing_conversation / no_match / routed / own_department / no_usable_agent
    department_code: Optional[str] = None
    target_name: Optional[str] = None
    matched_keywords: List[str] = field(default_factory=list)
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    unavailable: List[Dict[str, Any]] = field(default_factory=list)  # 命中但当前用户没有可用助手的部门

    @property
    def decided_by_central(self) -> bool:
        return self.reason in ("routed", "own_department", "no_match", "no_usable_agent")

    def public(self) -> Optional[Dict[str, Any]]:
        """给前端看的转交说明；非中央 Agent 或已有会话时没有。"""
        if not self.decided_by_central:
            return None
        return {"reason": self.reason, "department_code": self.department_code,
                "target_agent_id": self.target_agent_id, "target_name": self.target_name,
                "matched_keywords": self.matched_keywords,
                "alternatives": self.alternatives, "unavailable": self.unavailable}


def resolve_target_agent(db, user_id: int, agent_id: int, message: str, conversation_id: Optional[int]) -> int:
    from models.agent_dao import get_agent_by_id
    from models.conversation_dao import get_conversation_by_id

    if conversation_id is not None:
        # 已有会话：延用它创建时定下的 agent_id，不重新路由。
        conv = get_conversation_by_id(db, conversation_id)
        return conv.agent_id if conv else agent_id

    agent = get_agent_by_id(db, agent_id)
    if not agent or agent.agent_type != "central" or agent.lifecycle_status != "published":
        return agent_id

    department_code = match_department(message)
    if department_code is None:
        return agent_id

    target = _find_department_agent(db, user_id, department_code)
    if target is None:
        logger.info(f"中央Agent[{agent_id}] 命中部门 {department_code} 但没有可用的部门Agent，中央Agent自己回答")
        return agent_id

    logger.info(f"中央Agent[{agent_id}] 按关键词路由到部门Agent[{target}]（部门={department_code}）")
    return target


async def plan_route_async(db, user_id: int, agent_id: int, message: str,
                           conversation_id: Optional[int]) -> RoutePlan:
    from models.agent_async_dao import get_agent_by_id_async
    from models.conversation_async_dao import get_conversation_by_id_async

    if conversation_id is not None:
        conv = await get_conversation_by_id_async(db, conversation_id)
        return RoutePlan(conv.agent_id if conv else agent_id, reason="existing_conversation")

    agent = await get_agent_by_id_async(db, agent_id)
    if not agent or agent.agent_type != "central" or agent.lifecycle_status != "published":
        return RoutePlan(agent_id)

    plan = RoutePlan(agent_id, central_agent_id=agent_id, reason="no_match")
    usable = []
    for code, keywords in match_departments(message):
        found = await _find_department_agent_async(db, user_id, code, with_name=True)
        if found is None:
            plan.unavailable.append({"department_code": code, "matched_keywords": keywords})
        else:
            usable.append({"agent_id": found[0], "name": found[1], "department_code": code,
                           "matched_keywords": keywords})
    if usable:
        primary, plan.alternatives = usable[0], usable[1:]
        plan.target_agent_id, plan.department_code = primary["agent_id"], primary["department_code"]
        plan.matched_keywords, plan.reason = primary["matched_keywords"], "routed"
        plan.target_name = primary["name"]
        logger.info(f"中央Agent[{agent_id}] 路由到部门Agent[{plan.target_agent_id}]"
                    f"（部门={plan.department_code}，备选 {len(plan.alternatives)} 个）")
    elif plan.unavailable and (own := await _own_department_fallback_async(db, user_id, plan.unavailable)):
        # 问题属于别的部门（比如销售员工问报销），但这是员工自己的通用办公事务：交给他本部门的助手办
        entry, (plan.target_agent_id, plan.target_name) = own
        plan.reason, plan.department_code, plan.matched_keywords = "own_department", entry["department_code"], entry["matched_keywords"]
        logger.info(f"中央Agent[{agent_id}] 命中部门 {plan.department_code} 的通用办公事务，交给本部门Agent[{plan.target_agent_id}]")
    elif plan.unavailable:
        plan.reason = "no_usable_agent"
        plan.department_code = plan.unavailable[0]["department_code"]
        plan.matched_keywords = plan.unavailable[0]["matched_keywords"]
        logger.info(f"中央Agent[{agent_id}] 命中部门 {plan.department_code} 但没有可用的部门Agent，中央Agent自己回答")
    return plan


async def resolve_target_agent_async(db, user_id: int, agent_id: int, message: str,
                                      conversation_id: Optional[int]) -> int:
    return (await plan_route_async(db, user_id, agent_id, message, conversation_id)).target_agent_id


async def record_handoff_async(db, user_id: int, plan: RoutePlan, message: str) -> None:
    """记录一次中央 Agent 的转交决定（含"没转出去"的原因）；失败不能影响对话本身。"""
    if not plan.decided_by_central:
        return
    try:
        import json
        from models.init_db import AgentHandoff
        db.add(AgentHandoff(
            user_id=user_id, central_agent_id=plan.central_agent_id, target_agent_id=plan.target_agent_id,
            reason=plan.reason, department_code=plan.department_code,
            detail_json=json.dumps({"matched_keywords": plan.matched_keywords, "alternatives": plan.alternatives,
                                    "unavailable": plan.unavailable}, ensure_ascii=False),
            message_excerpt=(message or "")[:500]))
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()
        logger.warning("记录转交失败", exc_info=True)


def _general_keywords(department_code: str) -> tuple:
    from service.enterprise_agent_templates import TEMPLATES
    return next((t.get("general_keywords", ()) for t in TEMPLATES.values()
                 if t["agent_type"] == "department" and t.get("department_code") == department_code), ())


async def _own_department_fallback_async(db, user_id: int, unavailable: List[Dict[str, Any]]):
    """命中的部门员工用不了，但命中的是请假、报销、IT 工单这类通用办公事务（模板的 general_keywords）：
    找员工本部门已发布、能用的部门助手（它带着通用办公工具）。返回 (命中项, (agent_id, 名称))，没有返回 None。
    采购、客户这类部门专属业务不在此列，照旧由中央助手说明“你没有可用的助手”。"""
    general = [u for u in unavailable if set(u["matched_keywords"]) & set(_general_keywords(u["department_code"]))]
    if not general:
        return None
    from sqlalchemy import text
    rows = (await db.execute(text(
        "SELECT a.id, a.name, a.scope_type, a.team_id, a.organization_id FROM agent a "
        "JOIN team_members tm ON tm.team_id = a.team_id AND tm.user_id = :u AND tm.status = 'active' "
        "WHERE a.agent_type = 'department' AND a.lifecycle_status = 'published' ORDER BY a.id"), {"u": user_id})).all()
    agent_id = await _pick_usable_async(db, user_id, rows)
    if agent_id is None:
        return None
    return general[0], (agent_id, next(row[1] for row in rows if row[0] == agent_id))


def _find_department_agent(db, user_id: int, department_code: str) -> Optional[int]:
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT id, name, scope_type, team_id, organization_id FROM agent "
            "WHERE agent_type='department' AND department_code=:code AND lifecycle_status='published' ORDER BY id"
        ),
        {"code": department_code},
    ).all()
    return _pick_usable(db, user_id, rows)


async def _find_department_agent_async(db, user_id: int, department_code: str, with_name: bool = False):
    from sqlalchemy import text

    result = await db.execute(
        text(
            "SELECT id, name, scope_type, team_id, organization_id FROM agent "
            "WHERE agent_type='department' AND department_code=:code AND lifecycle_status='published' ORDER BY id"
        ),
        {"code": department_code},
    )
    rows = result.all()
    agent_id = await _pick_usable_async(db, user_id, rows)
    if agent_id is None or not with_name:
        return agent_id
    return agent_id, next(row[1] for row in rows if row[0] == agent_id)


def _pick_usable(db, user_id: int, rows) -> Optional[int]:
    from service.access_control import get_usable_agent

    for row in rows:
        agent_id = row[0]
        if get_usable_agent(db, user_id, agent_id) is not None:
            return agent_id
    return None


async def _pick_usable_async(db, user_id: int, rows) -> Optional[int]:
    from service.access_control import get_usable_agent_async

    for row in rows:
        agent_id = row[0]
        if await get_usable_agent_async(db, user_id, agent_id) is not None:
            return agent_id
    return None
