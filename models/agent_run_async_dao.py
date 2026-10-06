"""Agent 运行轨迹异步 DAO。"""

from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.init_db import AgentRun, AgentStep


async def get_owned_run_async(
        db: AsyncSession, user_id: int, run_id: int, agent_id: int = None,
) -> Optional[AgentRun]:
    conditions = [AgentRun.id == run_id, AgentRun.user_id == user_id]
    if agent_id is not None:
        conditions.append(AgentRun.agent_id == agent_id)
    result = await db.execute(select(AgentRun).where(*conditions))
    return result.scalars().first()


async def list_runs_by_agent_async(
        db: AsyncSession,
        agent_id: int,
        limit: int = 50,
        conversation_id: Optional[int] = None,
) -> List[AgentRun]:
    conditions = [AgentRun.agent_id == agent_id]
    if conversation_id is not None:
        conditions.append(AgentRun.conversation_id == conversation_id)
    result = await db.execute(
        select(AgentRun)
        .where(*conditions)
        .order_by(AgentRun.started_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_steps_by_run_async(db: AsyncSession, run_id: int) -> List[AgentStep]:
    result = await db.execute(
        select(AgentStep)
        .where(AgentStep.run_id == run_id)
        .order_by(AgentStep.step_no.asc())
    )
    return list(result.scalars().all())


# ========== 写入（agent_runtime async 迁移用；对齐 models/agent_run_dao.py 同步版）==========

def _trace_id():
    from service.observability.context import current_trace_id
    return current_trace_id()


async def create_run_async(
        db: AsyncSession, user_id: int, agent_id: int,
        user_message: str, chat_id: int = None, conversation_id: int = None,
) -> AgentRun:
    """创建一次 Agent 运行记录（初始 running）。"""
    run = AgentRun(
        user_id=user_id,
        agent_id=agent_id,
        user_message=user_message,
        chat_id=chat_id,
        conversation_id=conversation_id,
        status="running",
        trace_id=_trace_id(),
    )
    db.add(run)
    await db.flush()
    return run


async def update_run_status_async(
        db: AsyncSession, run: AgentRun, status: str,
        final_answer: str = None, total_steps: int = None,
        total_tokens: int = None, error_msg: str = None,
) -> AgentRun:
    """更新运行状态（结束时调用）。"""
    from utils.timeutil import utcnow

    run.status = status
    if final_answer is not None:
        run.final_answer = final_answer
    if total_steps is not None:
        run.total_steps = total_steps
    if total_tokens is not None:
        run.total_tokens = total_tokens
    if error_msg is not None:
        run.error_msg = error_msg
    run.finished_at = utcnow()
    await db.flush()
    return run


async def add_run_tokens_async(db: AsyncSession, run_id: int, extra_tokens: int) -> None:
    """给已经写完的 run 追加 token 用量（不改状态/时间戳）。

    用于记忆总结这类跑在 run「finished」之后的独立事务里的花费——不能等它跑完再
    finalize 这次 run（会拖慢用户拿到回答的时间，且失败了不该牵连已完成的 run），
    所以先按主循环用量收尾，总结算完再补一笔。
    """
    if not extra_tokens:
        return
    await db.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(total_tokens=func.coalesce(AgentRun.total_tokens, 0) + extra_tokens)
    )


async def create_step_async(
        db: AsyncSession, run_id: int, step_no: int, step_type: str,
        thought: str = None, tool_name: str = None, tool_args: str = None,
        tool_result: str = None, tokens: int = 0,
) -> AgentStep:
    """记录运行中的一个步骤。"""
    step = AgentStep(
        run_id=run_id, step_no=step_no, step_type=step_type,
        thought=thought, tool_name=tool_name, tool_args=tool_args,
        tool_result=tool_result, tokens=tokens,
    )
    db.add(step)
    await db.flush()
    return step


async def count_finished_runs_by_agent_async(
        db: AsyncSession, user_id: int, agent_id: int,
) -> int:
    """统计某用户某 Agent 已成功完成的运行次数（记忆总结节流用）。"""
    result = await db.execute(
        select(func.count())
        .select_from(AgentRun)
        .where(
            AgentRun.user_id == user_id,
            AgentRun.agent_id == agent_id,
            AgentRun.status == "finished",
            AgentRun.final_answer.isnot(None),
        )
    )
    return int(result.scalar() or 0)


async def list_finished_runs_by_agent_async(
        db: AsyncSession, user_id: int, agent_id: int, limit: int = 1000,
) -> List[AgentRun]:
    """某用户某 Agent 的成功运行记录，按开始时间倒序。"""
    result = await db.execute(
        select(AgentRun)
        .where(
            AgentRun.user_id == user_id,
            AgentRun.agent_id == agent_id,
            AgentRun.status == "finished",
            AgentRun.final_answer.isnot(None),
        )
        .order_by(AgentRun.started_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
