"""高风险 Agent 工具调用的确认令牌（第五轮审计 P0-2）。

`service/tools/langchain_adapter.py` 在 ReAct 循环里拦到 `risk_level="high_risk"`
的工具调用时，只会调这里的 `create_pending` 建一条待确认记录，不会真正执行。
真正执行只有 `confirm_and_execute_async` 这一个入口，由 FastAPI 路由
（`FasdtApi/chat.py`）在用户点击确认按钮时调用——ReAct 循环本身完全没有路径能
走到这里，所以哪怕模型被提示词注入诱导着反复请求同一个高风险操作，最多也只是
反复生成新的待确认单，不会有任何一次真的把请求发给企业业务中心。

`confirm_and_execute_async` 里"校验通过就标 confirmed"用一条条件 UPDATE
（WHERE status='pending'）而不是"先查再改"两步分开——避免同一个 token 被
点两次确认时并发执行两遍，跟这轮之前几个 P1 修的并发缺口（审批 decide、
Java 幂等占位）是同一个思路。
"""
import asyncio
import json
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional

from utils.logger_handler import get_logger
from utils.timeutil import utcnow

logger = get_logger("tool_confirmation_service")

_TTL_SECONDS = 600  # 10 分钟内没确认就过期，跟 Java 侧 nonce/幂等 key 的量级一致


class ConfirmationError(Exception):
    """确认/拒绝失败时抛出，带一个建议的 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def create_pending(user_id: int, agent_id: Optional[int], tool_name: str,
                    tool_args: Dict[str, Any]) -> Dict[str, Any]:
    """建一条 pending 记录。跑在 ReAct 引擎的同步 worker 线程里，用同步 Session。"""
    from models.init_db import SessionLocal, ToolConfirmation

    token = uuid.uuid4().hex
    expires_at = utcnow() + timedelta(seconds=_TTL_SECONDS)
    db = SessionLocal()
    try:
        record = ToolConfirmation(
            token=token, user_id=user_id, agent_id=agent_id,
            tool_name=tool_name, tool_args=json.dumps(tool_args, ensure_ascii=False),
            status="pending", expires_at=expires_at,
        )
        db.add(record)
        db.commit()
    finally:
        db.close()
    return {"token": token, "expires_at": expires_at.isoformat()}


async def confirm_and_execute_async(token: str, user_id: int) -> Dict[str, Any]:
    """校验 token 归属当前用户、还没过期、还没被处理过，标成 confirmed 后才真正执行工具。

    权限判断不在这里重复实现：真正执行时新建的 ToolContext 只带 user_id/agent_id，
    工具自己的 execute() 会用当前 user_id 重新算一遍 resolve_caller_context，
    所以哪怕用户角色在"LLM 请求"和"用户点确认"这两个时间点之间发生了变化，
    权限检查用的也是确认那一刻的最新状态，不是创建待确认单时的旧状态。
    """
    from sqlalchemy import select, update
    from models.async_db import AsyncSessionLocal
    from models.init_db import ToolConfirmation

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(ToolConfirmation).where(ToolConfirmation.token == token)
        )).scalars().first()
        if row is None or row.user_id != user_id:
            raise ConfirmationError("确认单不存在或不属于当前用户", 404)
        if row.status != "pending":
            raise ConfirmationError(f"确认单已经是「{row.status}」状态，不能重复处理", 409)
        if row.expires_at < utcnow():
            await db.execute(
                update(ToolConfirmation)
                .where(ToolConfirmation.id == row.id, ToolConfirmation.status == "pending")
                .values(status="expired")
            )
            await db.commit()
            raise ConfirmationError("确认单已过期，请让 Agent 重新发起这次操作", 410)

        result = await db.execute(
            update(ToolConfirmation)
            .where(ToolConfirmation.id == row.id, ToolConfirmation.status == "pending")
            .values(status="confirmed", decided_at=utcnow())
        )
        await db.commit()
        if result.rowcount == 0:
            raise ConfirmationError("确认单已经被处理过，不能重复处理", 409)

        tool_name = row.tool_name
        tool_args = json.loads(row.tool_args or "{}")
        agent_id = row.agent_id

    # 真正执行放到上面那个事务外——工具执行本身会走一次 HTTP（企业业务中心），
    # 不该占着这条 DB 连接；而且工具是同步代码（requests 阻塞调用），必须扔进
    # 线程池，不能直接在 async 函数里跑，否则会堵住事件循环。
    result_text = await asyncio.to_thread(_execute_tool_sync, tool_name, user_id, agent_id, tool_args)

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(ToolConfirmation).where(ToolConfirmation.token == token)
            .values(result=result_text[:4000])
        )
        await db.commit()

    return {"tool_name": tool_name, "result": result_text}


def _execute_tool_sync(tool_name: str, user_id: int, agent_id: Optional[int],
                        tool_args: Dict[str, Any]) -> str:
    from service.tools.base import ToolContext, ToolRegistry

    tool_class = ToolRegistry.get(tool_name)
    if tool_class is None:
        return json.dumps({"error": f"工具「{tool_name}」已不存在，无法执行"}, ensure_ascii=False)
    tool = tool_class()
    tool.set_context(ToolContext(user_id=user_id, agent_id=agent_id))
    try:
        return tool.execute(**tool_args)
    except Exception as e:  # noqa: BLE001 —— 执行失败要把原因带回给用户，不能让请求直接崩
        logger.error(f"确认执行高风险工具失败: tool={tool_name}, user={user_id}, error={e}")
        return json.dumps({"error": f"执行失败: {e}"}, ensure_ascii=False)


async def reject_async(token: str, user_id: int) -> None:
    from sqlalchemy import update
    from models.async_db import AsyncSessionLocal
    from models.init_db import ToolConfirmation

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(ToolConfirmation)
            .where(ToolConfirmation.token == token, ToolConfirmation.user_id == user_id,
                   ToolConfirmation.status == "pending")
            .values(status="rejected", decided_at=utcnow())
        )
        await db.commit()
        if result.rowcount == 0:
            raise ConfirmationError("确认单不存在、不属于当前用户，或已经被处理过", 404)
