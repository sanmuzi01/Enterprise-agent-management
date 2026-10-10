"""一轮对话的完整流程：限流 → 中央 Agent 路由 → 额度 → 并发上限 → 对话 → 额度提醒。

网页的 POST /chat/{agent_id} 和飞书 / 钉钉里发给机器人的消息走的都是这一条，不另写一套：
外部渠道只是换了个入口，用的还是同一个 Agent 运行时（service/runtime/agent_runtime.py::run_with_history_async）、
同样的权限、额度和高风险操作确认。
"""
from typing import Any, Dict, Optional, Tuple

from service import chat_service, quota_service
from service.runtime import central_router
from utils.rate_limit import concurrency_guard, require_limit


async def run_turn(db, user, agent_id: int, user_message: str,
                   conversation_id: Optional[int] = None) -> Tuple[Dict[str, Any], central_router.RoutePlan]:
    """返回 (对话结果, 路由决定)。超限抛 LimitExceeded，无权使用抛 ValueError（调用方各自转成自己的错误形式）。"""
    user_id = user.id
    require_limit(key=f"chat:user:{user_id}", limit_env="CHAT_RATE_LIMIT", default_limit=20,
                  window_env="CHAT_RATE_WINDOW_SECONDS", default_window=60, label="聊天请求")
    # 只有 agent_id 指向 agent_type="central" 的 Agent 才会真的路由；其他 Agent 原样返回
    plan = await central_router.plan_route_async(db, user_id, agent_id, user_message, conversation_id)
    await central_router.record_handoff_async(db, user_id, plan, user_message)
    quota_before = await quota_service.enforce_quota_async(db, user_id)
    with concurrency_guard(key=f"agent_run:user:{user_id}", limit_env="USER_MAX_CONCURRENT_AGENT_RUNS", default_limit=2,
                           ttl_env="AGENT_RUN_CONCURRENCY_TTL_SECONDS", default_ttl=300, label="Agent"):
        result = await chat_service.chat_with_agent(db=db, user=user, agent_id=plan.target_agent_id,
                                                    user_message=user_message, conversation_id=conversation_id)
    if "answer" in result:
        await quota_service.check_and_notify_threshold_async(db, user_id, quota_before["used_tokens"])
    return result, plan
