"""中央 Agent 受控路由（Phase 3D 阶段3，docs/enterprise-rbac-plan.md 9.5）。

第一版按设计稿"规则路由，不需要独立 Runtime"：关键词命中部门 → 在当前用户能用的
Agent 里找那个部门的 Agent → 有就路由过去，没有就中央 Agent 自己回答。

只在**新建会话**（`conversation_id is None`）时路由一次；同一个会话后续消息不重新
路由——会话一旦创建就固定绑在某个 `agent.id` 上（`conversation.agent_id`），这是
`chat_service`/`access_control` 到处依赖的既有约束，阶段3 不改这个约束，只在
"选哪个 agent_id 来创建/延用这个会话"这一步之前插一层。

中央 Agent 本身（`agent_type == "central"`）现存 Agent 一个都没有——这个字段默认值
是 `personal`，所以下面 `resolve_target_agent[_async]` 对所有现存 Agent 都是直接
原样返回 `agent_id`，零行为变化；只有显式创建了 `agent_type="central"` 的 Agent
才会触发路由，需要真的建一个中央 Agent + 至少一个部门 Agent 才能观察到路由生效。
"""
from typing import Optional

from utils.logger_handler import get_logger

logger = get_logger("central_router")

VALID_AGENT_TYPES = {"central", "department", "personal"}
VALID_DEPARTMENT_CODES = {"hr", "procurement", "sales", "finance", "it"}

# 关键词 → 部门代码。设计稿原文的对应关系（docs/enterprise-business-hub-plan.md 第6节）：
# 请假/入职/制度 → HR；库存/供应商/采购 → 采购；客户/联系人/商机 → 销售；
# 预算/报销 → 财务；账号/故障/工单 → IT。按顺序匹配，第一个命中的部门生效。
_ROUTING_RULES = (
    ("hr", ("请假", "入职", "制度", "考勤", "离职", "转正")),
    ("procurement", ("库存", "供应商", "采购", "订单", "补货")),
    ("sales", ("客户", "联系人", "商机", "跟进", "报价")),
    ("finance", ("预算", "报销", "发票", "付款", "费用")),
    ("it", ("账号", "故障", "工单", "密码重置", "网络")),
)


def match_department(message: str) -> Optional[str]:
    """规则路由本体：message 里出现哪个部门的关键词就返回哪个部门代码，
    都没命中返回 None（中央 Agent 自己回答）。"""
    if not message:
        return None
    for department_code, keywords in _ROUTING_RULES:
        if any(kw in message for kw in keywords):
            return department_code
    return None


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


async def resolve_target_agent_async(db, user_id: int, agent_id: int, message: str,
                                      conversation_id: Optional[int]) -> int:
    from models.agent_async_dao import get_agent_by_id_async
    from models.conversation_async_dao import get_conversation_by_id_async

    if conversation_id is not None:
        conv = await get_conversation_by_id_async(db, conversation_id)
        return conv.agent_id if conv else agent_id

    agent = await get_agent_by_id_async(db, agent_id)
    if not agent or agent.agent_type != "central" or agent.lifecycle_status != "published":
        return agent_id

    department_code = match_department(message)
    if department_code is None:
        return agent_id

    target = await _find_department_agent_async(db, user_id, department_code)
    if target is None:
        logger.info(f"中央Agent[{agent_id}] 命中部门 {department_code} 但没有可用的部门Agent，中央Agent自己回答")
        return agent_id

    logger.info(f"中央Agent[{agent_id}] 按关键词路由到部门Agent[{target}]（部门={department_code}）")
    return target


def _find_department_agent(db, user_id: int, department_code: str) -> Optional[int]:
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT id, user_id, scope_type, team_id, organization_id FROM agent "
            "WHERE agent_type='department' AND department_code=:code AND lifecycle_status='published'"
        ),
        {"code": department_code},
    ).all()
    return _pick_usable(db, user_id, rows)


async def _find_department_agent_async(db, user_id: int, department_code: str) -> Optional[int]:
    from sqlalchemy import text

    result = await db.execute(
        text(
            "SELECT id, user_id, scope_type, team_id, organization_id FROM agent "
            "WHERE agent_type='department' AND department_code=:code AND lifecycle_status='published'"
        ),
        {"code": department_code},
    )
    rows = result.all()
    return await _pick_usable_async(db, user_id, rows)


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
