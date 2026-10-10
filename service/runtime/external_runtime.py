"""外部 Agent 的运行入口：Agent.runtime_type == "external" 时，聊天运行时转到这里，而不是平台自带的运行循环。

保持和自带运行时同样的外部契约（返回结构、SSE 事件、AgentRun 记录、失败也留记录），
所以会话保存、运行轨迹、用量统计、审计这些下游逻辑都不用改。
平台继续负责：身份与权限（调用方已经过 get_usable_agent）、限流、密级策略、审计；
对方负责：怎么思考、调什么工具、用什么模型。
"""
import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy import select, text

from models.agent_run_async_dao import create_run_async, create_step_async, update_run_status_async
from models.init_db import AgentExternalEndpoint, User
from service.http_resilience import CircuitOpenError, circuit_breaker
from service.runtime import external_agent
from service.runtime.sse_events import (
    make_answer, make_answer_delta, make_citations, make_done, make_error, make_ready, make_retrieval, make_thinking,
)
from utils.crypto import decrypt
from utils.logger_handler import get_logger

logger = get_logger("external_runtime")

# 内容密级策略里对“外部 Agent”用的模型名：不在 confidential 白名单里，所以 confidential / restricted 的资料不会发出去
EGRESS_MODEL_NAME = "external-agent"


def breaker_name(agent_id: int) -> str:
    return f"external_agent:{agent_id}"


async def load_endpoint_async(db, agent_id: int) -> Optional[AgentExternalEndpoint]:
    return (await db.execute(
        select(AgentExternalEndpoint).where(AgentExternalEndpoint.agent_id == agent_id)
    )).scalars().first()


def to_config(endpoint: AgentExternalEndpoint) -> external_agent.EndpointConfig:
    headers: Dict[str, str] = {}
    if endpoint.headers_encrypted:
        try:
            headers = {str(k): str(v) for k, v in json.loads(decrypt(endpoint.headers_encrypted)).items()}
        except Exception:  # noqa: BLE001
            logger.error(f"外部 Agent 附加请求头解密失败: agent_id={endpoint.agent_id}")
    return external_agent.EndpointConfig(
        url=endpoint.url, secret=decrypt(endpoint.secret_encrypted), headers=headers, timeout_seconds=endpoint.timeout_seconds,
    )


async def user_context_async(db, user_id: int) -> Dict[str, Any]:
    """告诉对方“是谁在问”：用户编号、名字、所在部门和部门内角色。不含手机号、密码等其他资料。"""
    name = (await db.execute(select(User.name).where(User.id == user_id))).scalar()
    rows = (await db.execute(text(
        "SELECT t.id, t.name, t.department_code, er.code FROM team_members tm "
        "JOIN teams t ON tm.team_id = t.id JOIN enterprise_role er ON tm.role_id = er.id "
        "WHERE tm.user_id = :uid AND tm.status = 'active' AND t.status = 'active' ORDER BY t.id LIMIT 20"
    ), {"uid": user_id})).all()
    return {
        "id": user_id, "name": name or "",
        "departments": [{"id": r[0], "name": r[1], "department_code": r[2], "role": r[3]} for r in rows],
    }


async def _knowledge_for(db, agent, user_id: int, endpoint, user_message: str) -> Dict[str, Any]:
    """send_knowledge 开启时才检索并转发；按密级策略过滤（对外部 Agent 等同外部模型）。"""
    if not (endpoint.send_knowledge and agent.rag_enabled):
        return {}
    from service.runtime.agent_runtime import _kb_retrieve_async
    proxy = SimpleNamespace(
        model_name=EGRESS_MODEL_NAME, kb_top_k=agent.kb_top_k, kb_rerank_enabled=agent.kb_rerank_enabled,
        kb_refuse_when_empty=agent.kb_refuse_when_empty, sensitivity=agent.sensitivity,
    )
    return await _kb_retrieve_async(db, proxy, user_id, agent.id, user_message)


def _payload(agent, user: Dict[str, Any], message: str, history, run_id: int, conversation_id: Optional[int],
             rag: Dict[str, Any]) -> Dict[str, Any]:
    knowledge = None
    if rag and rag.get("hit_count"):
        knowledge = {"context": rag.get("context", ""), "citations": rag.get("citations", [])}
    return external_agent.build_payload(
        kind="chat", agent={"id": agent.id, "name": agent.name}, user=user, message=message, history=history,
        run_id=run_id, conversation_id=conversation_id, knowledge=knowledge,
    )


def _friendly(exc: Exception) -> str:
    if isinstance(exc, CircuitOpenError):
        return "外部 Agent 服务连续出错，已暂停调用，稍后会自动重试"
    return str(exc) if isinstance(exc, external_agent.ExternalAgentError) else "外部 Agent 服务暂时不可用，请稍后重试"


async def _record_outcome(db, run, run_id: int, started: float, endpoint_agent_id: int, result: Dict[str, Any],
                          first_step_no: int) -> int:
    step_no = first_step_no
    await create_step_async(
        db=db, run_id=run_id, step_no=step_no, step_type="external",
        thought=f"外部 Agent 服务已回复（{int((time.monotonic() - started) * 1000)} 毫秒）",
        tool_name="external_agent", tool_args=f"agent_id={endpoint_agent_id}", tool_result=result["answer"][:500],
    )
    for item in result.get("steps", []):
        step_no += 1
        await create_step_async(db=db, run_id=run_id, step_no=step_no, step_type="external",
                                thought=item["title"], tool_name="external_agent", tool_result=item["detail"])
    usage = result.get("usage") or {}
    await update_run_status_async(
        db=db, run=run, status="finished", final_answer=result["answer"], total_steps=step_no,
        total_tokens=usage.get("total_tokens"),
    )
    await db.commit()
    return step_no


async def _begin(db, user_id: int, agent, user_message: str, conversation_id: Optional[int]):
    endpoint = await load_endpoint_async(db, agent.id)
    if endpoint is None:
        raise external_agent.ExternalAgentError("这个智能体还没有配置外部服务地址")
    run = await create_run_async(db=db, user_id=user_id, agent_id=agent.id, user_message=user_message,
                                 conversation_id=conversation_id)
    await db.commit()
    return endpoint, run, run.id


async def run_external_async(db, agent, user_id: int, user_message: str, history: Optional[List[Dict[str, str]]],
                             conversation_id: Optional[int]) -> Dict[str, Any]:
    from service.runtime.agent_runtime import _finalize_run_async

    agent_id = agent.id
    endpoint, run, run_id = await _begin(db, user_id, agent, user_message, conversation_id)
    started = time.monotonic()
    try:
        cfg = to_config(endpoint)
        user = await user_context_async(db, user_id)
        rag = await _knowledge_for(db, agent, user_id, endpoint, user_message)
        payload = _payload(agent, user, user_message, history, run_id, conversation_id, rag)
        circuit_breaker.before_call(breaker_name(agent_id))
        try:
            result = await asyncio.to_thread(external_agent.call, cfg, payload)
        except Exception:
            circuit_breaker.record_failure(breaker_name(agent_id))
            raise
        circuit_breaker.record_success(breaker_name(agent_id))
        steps = await _record_outcome(db, run, run_id, started, agent_id, result, 1)
        return {
            "answer": result["answer"], "run_id": run_id, "steps": steps, "question": user_message, "agent_id": agent_id,
            "citations": result.get("citations") or (rag.get("citations") if rag else []) or [],
            "rag_mode": rag.get("mode", "off") if rag else "off", "rag_refused": False, "rag_stats": None,
            "usage": result.get("usage"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"外部 Agent 运行失败: run_id={run_id}, agent_id={agent_id}, error={type(exc).__name__}: {exc}")
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        message = _friendly(exc)
        await _finalize_run_async(db, run_id, "failed", error_msg=message, exc=exc)
        raise ValueError(message) from exc


async def stream_external_async(db, agent, user_id: int, user_message: str, history: Optional[List[Dict[str, str]]],
                                conversation_id: Optional[int]) -> AsyncGenerator[str, None]:
    from service.runtime.agent_runtime import _finalize_run_async

    agent_id = agent.id
    try:
        endpoint, run, run_id = await _begin(db, user_id, agent, user_message, conversation_id)
    except Exception as exc:  # noqa: BLE001
        yield make_error(_friendly(exc))
        return

    started = time.monotonic()
    finished = False
    try:
        yield make_ready(run_id)
        cfg = to_config(endpoint)
        user = await user_context_async(db, user_id)
        rag = await _knowledge_for(db, agent, user_id, endpoint, user_message)
        if rag and rag.get("hit_count"):
            yield make_retrieval(hit_count=rag["hit_count"], content_preview=rag.get("context", ""), stats=rag.get("stats"))
        payload = _payload(agent, user, user_message, history, run_id, conversation_id, rag)
        circuit_breaker.before_call(breaker_name(agent_id))

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        sentinel = object()
        box: Dict[str, Any] = {}

        def worker():
            try:
                for event in external_agent.stream(cfg, payload):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as exc:  # noqa: BLE001
                box["error"] = exc
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, sentinel)

        task = asyncio.create_task(asyncio.to_thread(worker))
        result: Optional[Dict[str, Any]] = None
        try:
            while True:
                event = await queue.get()
                if event is sentinel:
                    break
                if event["type"] == "delta":
                    yield make_answer_delta(event["text"])
                elif event["type"] == "step":
                    yield make_thinking(f"{event['title']}：{event['detail']}" if event["detail"] else event["title"])
                elif event["type"] == "citations" and event["items"]:
                    yield make_citations(event["items"])
                elif event["type"] == "result":
                    result = event["data"]
            await task
        finally:
            if not task.done():
                task.cancel()

        if "error" in box:
            circuit_breaker.record_failure(breaker_name(agent_id))
            raise box["error"]
        if result is None:
            circuit_breaker.record_failure(breaker_name(agent_id))
            raise external_agent.ExternalAgentError("外部 Agent 服务没有返回结果")
        circuit_breaker.record_success(breaker_name(agent_id))

        yield make_answer(result["answer"])
        if result.get("citations"):
            yield make_citations(result["citations"])
        steps = await _record_outcome(db, run, run_id, started, agent_id, result, 1)
        finished = True
        yield make_done(run_id, steps, len(result["answer"]), tokens=(result.get("usage") or {}).get("total_tokens"))
    except (asyncio.CancelledError, GeneratorExit):
        if not finished:
            await db.rollback()
            await _finalize_run_async(db, run_id, "cancelled", error_msg="用户停止生成")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"外部 Agent 流式运行失败: run_id={run_id}, agent_id={agent_id}, error={type(exc).__name__}: {exc}")
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        message = _friendly(exc)
        await _finalize_run_async(db, run_id, "failed", error_msg=message, exc=exc)
        yield make_error(message)
