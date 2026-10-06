"""Agent 运行轨迹异步读服务。"""

from typing import Dict, List, Optional

from models import agent_run_async_dao as dao
from service.access_control import get_usable_agent_async


def _format_dt(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def _run_to_dict(run) -> Dict:
    final_answer = run.final_answer
    return {
        "id": run.id,
        "agent_id": run.agent_id,
        "conversation_id": run.conversation_id,
        "user_message": run.user_message,
        "status": run.status,
        "total_steps": run.total_steps,
        "total_tokens": run.total_tokens,
        "final_answer": final_answer[:200] + "..." if final_answer and len(final_answer) > 200 else final_answer,
        "error_msg": run.error_msg,
        "trace_id": run.trace_id,
        "error_code": run.error_code,
        "issue_no": run.issue_no,
        "started_at": _format_dt(run.started_at),
        "finished_at": _format_dt(run.finished_at),
    }


async def list_runs(
        db,
        user_id: int,
        agent_id: int,
        limit: int = 20,
        conversation_id: Optional[int] = None,
) -> Optional[List[Dict]]:
    if await get_usable_agent_async(db, user_id, agent_id) is None:
        return None
    runs = await dao.list_runs_by_agent_async(
        db, agent_id, limit=limit, conversation_id=conversation_id
    )
    return [_run_to_dict(run) for run in runs]


async def get_steps(db, user_id: int, run_id: int, full: int = 0) -> Optional[Dict]:
    run = await dao.get_owned_run_async(db, user_id, run_id)
    if not run:
        return None

    def maybe_truncate(value: str, limit: int = 300):
        if value is None or full == 1:
            return value
        return value[:limit] + "..." if len(value) > limit else value

    steps = await dao.list_steps_by_run_async(db, run_id)
    return {
        "run_id": run_id,
        "status": run.status,
        "user_message": run.user_message,
        "final_answer": run.final_answer,
        "total_steps": run.total_steps,
        "started_at": _format_dt(run.started_at),
        "finished_at": _format_dt(run.finished_at),
        "steps": [
            {
                "step_no": step.step_no,
                "step_type": step.step_type,
                "thought": step.thought,
                "tool_name": step.tool_name,
                "tool_args": step.tool_args,
                "tool_result": maybe_truncate(step.tool_result),
                "tokens": step.tokens,
                "created_at": _format_dt(step.created_at),
            }
            for step in steps
        ],
    }
