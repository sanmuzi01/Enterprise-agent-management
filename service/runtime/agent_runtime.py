"""
Agent Runtime - 智能体推理引擎
职责：接收用户消息 → 编排RAG/LLM → 记录运行轨迹 → 返回回答
这一层是"大脑"，chat_service只负责"传话"，不关心内部怎么编排。
架构位置：
  chat_service.py（传话层）
        ↓ 调用
  agent_runtime.py（编排层 ← 你在这里）
        ↓ 调用
  rag_service + llm_service + agent_run_dao
        ↓
  ChromaDB + 智谱API + MySQL
"""
from utils.timeutil import utcnow
from typing import Any, AsyncGenerator, Dict, List
from sqlalchemy import select
from models.agent_run_async_dao import (
    create_run_async, create_step_async, update_run_status_async, add_run_tokens_async,
)
from service.access_control import get_usable_agent_async
from prompt.prompt_manager import build_prompt
from utils.logger_handler import get_logger
from service.runtime.sse_events import (
    make_ready, make_done, make_error, make_retrieval, make_memory, make_citations,
)
from service.memory_async_service import (
    load_memory_async, should_summarize_async, summarize_and_save_async,
)
from service.user_profile_async_service import format_user_profile_for_prompt_async
# ReAct 引擎装配（同步；由 _execute_react_sync / 流式 worker 在 to_thread 里用）
from service.tools.executor import ToolExecutor
logger = get_logger("agent_runtime")

def _short_text(value: Any, limit: int = 1000) -> str:
    import json

    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    return text[:limit]


def _format_rag_audit(results: List[Dict[str, Any]], rag_context: str,
                      stats: Dict[str, Any] = None, blocked_count: int = 0) -> str:
    import json

    payload = {
        "hit_count": len(results or []),
        "hits": [
            {
                "knowledge_id": item.get("knowledge_id"),
                "file_name": item.get("file_name") or (item.get("source") or {}).get("file_name"),
                "space_name": (item.get("source") or {}).get("space_name"),
                "chunk_index": item.get("chunk_index"),
                "score": item.get("score"),
                "rerank_score": item.get("rerank_score"),
                "distance": item.get("distance"),
                "content_preview": (item.get("content") or "")[:500],
            }
            for item in (results or [])
        ],
        "context_preview": rag_context[:1000],
    }
    if stats:
        payload["stats"] = stats  # 上下文压缩 / Token 节省，见 service/rag/rag_stats.py
    if blocked_count:
        # 第五轮审计 P0-1：有多少条检索结果因为密级限制没有进 context——
        # 管理员回看轨迹时能看出"结果变少了"是策略拦的，不是检索本身没命中。
        payload["blocked_by_sensitivity_count"] = blocked_count
    return json.dumps(payload, ensure_ascii=False)


async def _kb_retrieve_async(db, agent, user_id: int, agent_id: int, user_message: str) -> Dict[str, Any]:
    """统一 RAG 检索（彻底 async）：绑定了知识库空间走多空间联合检索（带来源），否则走旧 Agent 私有库。

    走 `search_entry.search_for_agent_async` —— 向量化 + 归属校验 + chunk 反查全 async，
    ChromaDB / rerank 封 `to_thread`；不再整段 `asyncio.to_thread` 一个同步 Session。
    返回 {context, citations, hit_count, mode, refused, error}。异常降级为空。

    第五轮审计 P0-1：拿到检索结果之后、拼进 system prompt 之前，按内容密级过滤一遍
    （`service/data_egress_policy.py`）——之前不管命中内容密级是什么都会原样发给
    Agent 配置的模型，`confidential`/`restricted` 的文档一样会被送到 DeepSeek/智谱
    这类外部云 API。被拦下来的 hits 不进 context/citations，只在 `blocked_count`
    里体现一个数字，方便前端提示"部分结果因密级限制未显示"。
    """
    from service.rag import search_entry
    from service import data_egress_policy

    try:
        res = await search_entry.search_for_agent_async(
            user_id, agent_id, user_message,
            top_k=int(getattr(agent, "kb_top_k", 5) or 5),
            rerank=bool(getattr(agent, "kb_rerank_enabled", 0)),
            refuse_when_empty=bool(getattr(agent, "kb_refuse_when_empty", 1)),
        )
        hits = res.get("hits", [])
        mode = res.get("mode", "agent")
        allowed, blocked = await data_egress_policy.filter_hits_by_sensitivity_async(
            db, hits, mode, getattr(agent, "sensitivity", "internal"), agent.model_name,
        )
        if blocked:
            context, citations = _reassemble_after_policy_filter(allowed, mode)
        else:
            context, citations = res.get("context", ""), res.get("citations", [])
        return {
            "context": context,
            "citations": citations,
            "hit_count": len(allowed),
            "hits": allowed,
            "mode": mode,
            "refused": bool(res.get("refused")),
            "stats": res.get("stats"),
            "error": "",
            "blocked_count": len(blocked),
        }
    except Exception as e:  # noqa: BLE001 —— RAG 失败一律降级，不阻断对话
        logger.warning(f"RAG 检索失败（降级跳过）: {e}")
        return {"context": "", "citations": [], "hit_count": 0, "hits": [],
                "mode": "error", "refused": False, "stats": None, "error": str(e), "blocked_count": 0}


def _reassemble_after_policy_filter(allowed_hits, mode: str):
    """密级策略拦掉了一部分 hits 之后，用剩下的重新拼 context/citations——不能直接
    用过滤前的 context 字符串抠掉一段，那样容易漏斩不干净；复用检索模块自己的组装
    逻辑（跟没被拦时走的是同一份代码，格式/编号规则不会跟正常路径长出两套）。"""
    if not allowed_hits:
        return "", []
    if mode == "spaces":
        from service.rag.space_search import _assemble
        return _assemble(allowed_hits)
    from service.rag.search_entry import _assemble_agent_context
    return _assemble_agent_context(allowed_hits)


def _compose_kb_prompt(system_prompt: str, agent, rag: Dict[str, Any]) -> str:
    """把检索上下文 + 引用/拒答规则拼进 system prompt。"""
    context = rag.get("context") or ""
    if context:
        rules = ["若参考资料不足以回答问题，请如实说明，不要编造。"]
        if getattr(agent, "kb_force_citation", 1):
            rules.append("引用规则：回答中每处引用了下面资料的内容，都要在句末用【来源N】标注（N 为资料编号）。")
        return (
            f"{system_prompt}\n\n"
            f"=== 知识库参考资料（按编号）===\n{context}\n=== 参考资料结束 ===\n"
            + "\n".join(rules)
        )
    if getattr(agent, "rag_enabled", 0) and getattr(agent, "kb_refuse_when_empty", 1):
        return (
            f"{system_prompt}\n\n"
            f"知识库里没有检索到和这个问题相关的内容。请如实告诉用户：知识库中暂无相关资料，"
            f"并建议对方 ① 换个说法、用文档里的术语再问一次；② 确认相关文档已上传且处于「启用」状态。"
            f"不要凭常识或推测作答。"
        )
    return system_prompt


async def _compose_system_prompt_async(db, user_id: int, agent_id: int, agent) -> Dict[str, str]:
    """统一组装 Agent 基础提示词、用户画像和长期记忆（画像 / 记忆走 *_async）。"""
    base_prompt = build_prompt(agent_id) or "你是一个通用智能助理。"

    profile_text = ""
    try:
        profile_text = await format_user_profile_for_prompt_async(db, user_id) or ""
        if profile_text:
            logger.info(f"已加载用户画像，长度={len(profile_text)}")
    except Exception as profile_err:  # noqa: BLE001
        logger.warning(f"加载用户画像失败（降级跳过）: {profile_err}")

    memory_text = ""
    memory_error = ""
    if agent.memory_enabled:
        try:
            memory_text = await load_memory_async(db, user_id, agent_id) or ""
            if memory_text:
                logger.info(f"已加载长期记忆，长度={len(memory_text)}")
        except Exception as mem_err:  # noqa: BLE001
            memory_error = str(mem_err)
            logger.warning(f"加载长期记忆失败（降级跳过）: {mem_err}")

    system_prompt = "\n\n".join([part for part in [base_prompt, profile_text, memory_text] if part])
    return {
        "system_prompt": system_prompt,
        "base_prompt": base_prompt,
        "profile_text": profile_text,
        "memory_text": memory_text,
        "memory_error": memory_error,
    }


def _plan_react_steps(step_no_ref: Dict[str, int], step_info: Dict[str, Any],
                      agent_model_name: str, user_message: str, rag_context: str) -> List[Dict[str, Any]]:
    """把一条 ReAct step_info 翻译成若干条 AgentStep 落库参数（不含 db / run_id）。

    纯函数（只推进 step_no_ref 计数）。同步路径立刻 create_step；异步路径先收集、
    engine 跑完再逐条 create_step_async，保证步号与行内容与同步版一致。
    """
    step_type = step_info.get("step_type", "thought")
    planned: List[Dict[str, Any]] = []
    if step_type == "thought":
        tool_calls = step_info.get("tool_calls") or []
        if tool_calls:
            for tc in tool_calls:
                planned.append(dict(
                    step_no=step_no_ref["value"],
                    step_type="tool_call",
                    thought=_short_text(step_info.get("content"), 500),
                    tool_name=tc.get("name", ""),
                    tool_args=_short_text(tc.get("args", ""), 1000),
                    tool_result="",
                ))
                step_no_ref["value"] += 1
        else:
            planned.append(dict(
                step_no=step_no_ref["value"],
                step_type="responder",
                thought=f"基于{'RAG上下文+' if rag_context else ''}ReAct引擎生成回答",
                tool_name=agent_model_name,
                tool_args=_short_text(user_message, 300),
                tool_result=_short_text(step_info.get("content"), 1200),
            ))
            step_no_ref["value"] += 1
    elif step_type == "tool_result":
        permission_denied = bool(step_info.get("permission_denied"))
        planned.append(dict(
            step_no=step_no_ref["value"],
            step_type="permission_denied" if permission_denied else "tool_result",
            thought="工具权限拒绝" if permission_denied else "工具执行结果",
            tool_name=step_info.get("tool_name", ""),
            tool_args="",
            tool_result=_short_text(step_info.get("tool_result"), 2000),
        ))
        step_no_ref["value"] += 1
    return planned



# ============================================================================
# AsyncSession 版（阶段 2）：chat_service.chat_with_agent → 这条
# ============================================================================

async def _finalize_run_async(db, run_id: int, status: str, *, error_msg: str = None, exc: BaseException = None) -> None:
    """异步失败收尾。run 行在函数开头已 commit，主事务 rollback 后它仍在，
    这里单独把它标记为终态（best-effort，吞二次异常）。"""
    from models.init_db import AgentRun
    try:
        res = await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        run_record = res.scalars().first()
        if run_record:
            run_record.status = status
            if error_msg is not None:
                run_record.error_msg = str(error_msg)[:500]
            run_record.finished_at = utcnow()
            if status == "failed":
                # 失败要可追溯：错误码、trace_id、问题编号、事件（同一个事务）；追踪失败不影响收尾本身
                try:
                    from service.observability.agent_runs import report_failure
                    await report_failure(db, run_id, exc, str(error_msg or ""))
                except Exception:  # noqa: BLE001
                    logger.warning("Agent 运行失败追踪出错", exc_info=True)
            await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()


async def _summarize_memory_async(user_id: int, agent_id: int, model_name: str,
                                   usage_sink: list = None) -> bool:
    """记忆总结跑在独立 AsyncSession + 独立事务里：失败只吞自己，
    不牵连已 finished 的 run（修同步版怪癖 2）。

    返回 True 表示确实跑了一次总结并落库（流式路径据此发 memory:summarized 事件）。
    usage_sink：传一个列表进来收总结这次调用的 token 用量，调用方再补到 run 的 total_tokens 上。
    """
    from models.async_db import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as mem_db:
            if await should_summarize_async(mem_db, user_id, agent_id):
                logger.info("开始总结长期记忆(async)...")
                await summarize_and_save_async(mem_db, user_id, agent_id, model_name, usage_sink=usage_sink)
                await mem_db.commit()
                logger.info("长期记忆总结完成(async)")
                return True
    except Exception as mem_err:  # noqa: BLE001
        logger.warning(f"记忆总结失败（不影响对话结果）: {mem_err}")
    return False


def _execute_react_sync(user_id: int, agent_id: int, agent_model_name: str,
                        agent_temperature: int, full_system_prompt: str,
                        user_message: str, history: List[Dict[str, str]]):
    """在独立同步 Session 里装配 ToolExecutor 并跑完 ReAct（供 async 路径 to_thread 调用）。

    engine 内部的 LLM / 工具调用都是阻塞的，整段进线程池。这里开的同步 Session 只服务
    ToolExecutor（读 llm_config / skill 配置 / 工具查库），与调用方的 AsyncSession 无关。
    返回 (react_result, step_infos)。轨迹在引擎跑完后由调用方统一落库。
    """
    from models.init_db import SessionLocal

    step_infos: List[Dict[str, Any]] = []
    sdb = SessionLocal()
    try:
        executor = ToolExecutor(
            db=sdb, user_id=user_id, agent_id=agent_id,
            model_name=agent_model_name,
            temperature=agent_temperature / 100,
        )
        engine = executor.create_engine(max_iterations=5, step_callback=step_infos.append)
        react_result = engine.invoke(
            system_prompt=full_system_prompt,
            user_message=user_message,
            history=history,
        )
        return react_result, step_infos
    finally:
        sdb.close()


async def run_with_history_async(
        db, user_id: int, agent_id: int, user_message: str,
        history: List[Dict[str, str]] = None,
        conversation_id: int = None,
) -> Dict[str, Any]:
    """非流式聊天执行（AsyncSession）。chat_service.chat_with_agent → 这条。

    事务设计要点：
      - run 记录建好后立即 commit —— LLM / 引擎失败也能留下 failed 记录
      - 记忆总结在独立 AsyncSession 里做 —— 失败不回滚已 finished 的 run
      - ReAct 引擎整段 asyncio.to_thread（自带同步 Session），轨迹在引擎跑完后统一落库
    """
    import asyncio

    agent = await get_usable_agent_async(db, user_id, agent_id)
    if not agent:
        raise ValueError("智能体不存在或无权使用")

    run = await create_run_async(
        db=db, user_id=user_id, agent_id=agent_id,
        user_message=user_message, conversation_id=conversation_id,
    )
    await db.commit()
    run_id = run.id  # 固化：异常路径 db.rollback() 会 expire run，之后不能再读 run.id

    rag_enabled = bool(agent.rag_enabled)
    memory_enabled = bool(agent.memory_enabled)
    agent_model_name = agent.model_name
    agent_temperature = agent.temperature
    logger.info(
        f"Agent运行开始(async): run_id={run_id}, agent_id={agent_id}, conv={conversation_id}, "
        f"rag={'on' if rag_enabled else 'off'}, memory={'on' if memory_enabled else 'off'}"
    )

    try:
        prompt_parts = await _compose_system_prompt_async(db, user_id, agent_id, agent)
        system_prompt = prompt_parts["system_prompt"]

        rag = {"context": "", "citations": [], "hit_count": 0, "hits": [], "mode": "off",
               "refused": False, "error": ""}
        citations: List[Dict[str, Any]] = []
        if rag_enabled:
            logger.info(f"RAG已启用，开始检索: query='{user_message[:30]}...'")
            rag = await _kb_retrieve_async(db, agent, user_id, agent_id, user_message)
            citations = rag.get("citations", [])
            if rag["error"]:
                thought, audit = f"RAG检索异常，已降级跳过: {rag['error'][:100]}", "检索失败，已跳过"
            elif rag["hit_count"]:
                thought = f"检索到{rag['hit_count']}条相关片段（{rag['mode']}）"
                audit = _format_rag_audit(rag["hits"], rag["context"], rag.get("stats"), rag.get("blocked_count", 0))
            else:
                thought, audit = "知识库中未检索到相关内容", "无匹配结果"
            await create_step_async(
                db=db, run_id=run_id, step_no=1, step_type="retrieval",
                thought=thought, tool_name="rag_search",
                tool_args=user_message[:200], tool_result=audit,
            )
        else:
            logger.info("RAG未启用，跳过知识库检索")

        rag_context = rag["context"]
        full_system_prompt = _compose_kb_prompt(system_prompt, agent, rag)

        if history is None:
            history = []

        react_result, step_infos = await asyncio.to_thread(
            _execute_react_sync,
            user_id, agent_id, agent_model_name, agent_temperature,
            full_system_prompt, user_message, history,
        )
        answer = react_result["answer"]

        # 轨迹统一落库（引擎跑完后一次性写，步号规则见 _plan_react_steps）
        step_no_ref = {"value": 2 if rag_enabled else 1}
        for step_info in step_infos:
            for kw in _plan_react_steps(step_no_ref, step_info, agent_model_name,
                                        user_message, rag_context):
                await create_step_async(db=db, run_id=run_id, **kw)
        total_steps = step_no_ref["value"] - 1 if step_no_ref["value"] > 1 else 1

        usage = react_result.get("usage")
        await update_run_status_async(
            db=db, run=run, status="finished",
            final_answer=answer, total_steps=total_steps,
            total_tokens=(usage or {}).get("total_tokens"),
        )
        await db.commit()

        if memory_enabled:
            memory_usage: list = []
            await _summarize_memory_async(user_id, agent_id, agent_model_name, usage_sink=memory_usage)
            if memory_usage:
                extra = sum(u.get("total_tokens", 0) for u in memory_usage)
                if extra:
                    await add_run_tokens_async(db, run_id, extra)
                    await db.commit()
                    usage = dict(usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
                    usage["total_tokens"] = usage.get("total_tokens", 0) + extra
                    usage["memory_tokens"] = extra

        logger.info(
            f"Agent运行完成(async): run_id={run_id}, steps={total_steps}, answer长度={len(answer)}"
        )
        return {
            "answer": answer,
            "run_id": run_id,
            "steps": total_steps,
            "question": user_message,
            "agent_id": agent_id,
            "citations": citations,
            "rag_mode": rag.get("mode", "off"),
            "rag_refused": rag.get("refused", False),
            "rag_stats": rag.get("stats"),
            "usage": usage,
        }

    except Exception as e:
        logger.error(f"Agent运行失败(async): run_id={run_id}, error={e}")
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        await _finalize_run_async(db, run_id, "failed", error_msg=str(e), exc=e)
        raise



# ============================================================================
# AsyncSession 版流式（阶段 3）：chat_service.chat_with_agent_stream_async → 这条
# ============================================================================

async def run_stream_with_history_async(
        db, user_id: int, agent_id: int, user_message: str,
        history: List[Dict[str, str]] = None,
        conversation_id: int = None,
) -> AsyncGenerator[str, None]:
    """流式聊天执行（AsyncSession）。chat_service.chat_with_agent_stream_async → 这条。

    同步 LangGraph `engine.invoke_stream()` 生成器整段跑在 worker 线程里，产出的 SSE 事件串
    经 `asyncio.Queue` 桥回主协程逐条 `yield`；线程内不碰 DB，轨迹收集在 `step_infos`，
    流结束后主协程用 `create_step_async` 统一落库。事务边界同非流式路径：
      - run 建好即 commit（失败也留 failed 记录）
      - 记忆总结独立事务
      - 逐 yield 之间不持有未提交事务
    """
    import asyncio

    # 1. Agent
    try:
        agent = await get_usable_agent_async(db, user_id, agent_id)
        if not agent:
            yield make_error("智能体不存在或无权使用")
            return
    except Exception as e:  # noqa: BLE001
        yield make_error(f"查询智能体失败: {e}")
        return

    # 2. 建 AgentRun 并立即 commit
    try:
        run = await create_run_async(
            db=db, user_id=user_id, agent_id=agent_id,
            user_message=user_message, conversation_id=conversation_id,
        )
        await db.commit()
        run_id = run.id
        rag_enabled = bool(agent.rag_enabled)
        memory_enabled = bool(agent.memory_enabled)
        agent_model_name = agent.model_name
        agent_temperature = agent.temperature
        logger.info(
            f"Agent[stream] 运行开始(async): run_id={run_id}, agent_id={agent_id}, "
            f"conv={conversation_id}, rag={'on' if rag_enabled else 'off'}, "
            f"memory={'on' if memory_enabled else 'off'}"
        )
    except Exception as e:  # noqa: BLE001
        yield make_error(f"创建运行记录失败: {e}")
        return

    run_finished = False
    try:
        yield make_ready(run_id)
        # 3. system_prompt + Memory 事件
        prompt_parts = await _compose_system_prompt_async(db, user_id, agent_id, agent)
        system_prompt = prompt_parts["system_prompt"]
        if prompt_parts["memory_text"]:
            yield make_memory("loaded", f"加载长期记忆 {len(prompt_parts['memory_text'])} 字")
        if prompt_parts["memory_error"]:
            yield make_memory("error", f"加载记忆失败，已跳过: {prompt_parts['memory_error'][:100]}")

        # RAG 检索
        rag = {"context": "", "citations": [], "hit_count": 0, "hits": [], "mode": "off",
               "refused": False, "error": ""}
        if rag_enabled:
            logger.info(f"RAG已启用，开始检索: query='{user_message[:30]}...'")
            rag = await _kb_retrieve_async(db, agent, user_id, agent_id, user_message)
            if rag["error"]:
                thought, audit = f"RAG检索异常，已降级跳过: {rag['error'][:100]}", "检索失败，已跳过"
                yield make_retrieval(hit_count=0, content_preview=f"检索异常已跳过: {rag['error'][:100]}")
            elif rag["hit_count"]:
                thought = f"检索到{rag['hit_count']}条相关片段（{rag['mode']}）"
                audit = _format_rag_audit(rag["hits"], rag["context"], rag.get("stats"), rag.get("blocked_count", 0))
                yield make_retrieval(hit_count=rag["hit_count"], content_preview=rag["context"],
                                     stats=rag.get("stats"))
            else:
                thought, audit = "知识库中未检索到相关内容", "无匹配结果"
                yield make_retrieval(hit_count=0, content_preview="知识库无匹配内容")
            await create_step_async(
                db=db, run_id=run_id, step_no=1, step_type="retrieval",
                thought=thought, tool_name="rag_search",
                tool_args=user_message[:200], tool_result=audit,
            )
            if rag["citations"]:
                yield make_citations(rag["citations"])
        else:
            logger.info("RAG未启用，跳过知识库检索")

        rag_context = rag["context"]
        full_system_prompt = _compose_kb_prompt(system_prompt, agent, rag)
        if history is None:
            history = []

        # 4. 桥接同步引擎流：worker 线程 next(gen) → queue → 主协程 yield
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        import threading
        stop_event = threading.Event()
        step_infos: List[Dict[str, Any]] = []
        result_box: Dict[str, Any] = {}
        _SENTINEL = object()

        def _worker():
            from models.init_db import SessionLocal
            sdb = SessionLocal()
            try:
                executor = ToolExecutor(
                    db=sdb, user_id=user_id, agent_id=agent_id,
                    model_name=agent_model_name, temperature=agent_temperature / 100,
                )
                engine = executor.create_engine(max_iterations=5, step_callback=step_infos.append)
                engine.cancel_event = stop_event
                gen = engine.invoke_stream(
                    system_prompt=full_system_prompt,
                    user_message=user_message, history=history,
                )
                try:
                    while not stop_event.is_set():
                        ev = next(gen)
                        if not stop_event.is_set():
                            loop.call_soon_threadsafe(queue.put_nowait, ev)
                except StopIteration as si:
                    result_box["result"] = si.value or {}
                finally:
                    gen.close()
            except Exception as e:  # noqa: BLE001
                result_box["error"] = e
            finally:
                sdb.close()
                if not stop_event.is_set():
                    loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

        worker = asyncio.create_task(asyncio.to_thread(_worker))
        try:
            while True:
                ev = await queue.get()
                if ev is _SENTINEL:
                    break
                yield ev
            await worker
        finally:
            stop_event.set()

        if "error" in result_box:
            raise result_box["error"]
        react_result = result_box.get("result", {}) or {}
        answer = react_result.get("answer", "") or ""

        # 5. 轨迹落库 + run 状态
        step_no_ref = {"value": 2 if rag_enabled else 1}
        for step_info in step_infos:
            for kw in _plan_react_steps(step_no_ref, step_info, agent_model_name,
                                        user_message, rag_context):
                await create_step_async(db=db, run_id=run_id, **kw)
        total_steps = step_no_ref["value"] - 1 if step_no_ref["value"] > 1 else 1

        usage = react_result.get("usage")
        await update_run_status_async(
            db=db, run=run, status="finished",
            final_answer=answer, total_steps=total_steps,
            total_tokens=(usage or {}).get("total_tokens"),
        )
        await db.commit()

        run_finished = True
        # 6. 记忆总结（独立事务）
        if memory_enabled:
            memory_usage: list = []
            if await _summarize_memory_async(user_id, agent_id, agent_model_name, usage_sink=memory_usage):
                yield make_memory("summarized", "长期记忆总结完成")
            if memory_usage:
                extra = sum(u.get("total_tokens", 0) for u in memory_usage)
                if extra:
                    await add_run_tokens_async(db, run_id, extra)
                    await db.commit()
                    usage = dict(usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
                    usage["total_tokens"] = usage.get("total_tokens", 0) + extra
                    usage["memory_tokens"] = extra

        logger.info(
            f"Agent[stream] 运行完成(async): run_id={run_id}, steps={total_steps}, "
            f"answer长度={len(answer)}"
        )
        yield make_done(run_id, total_steps, len(answer),
                        tokens=(usage or {}).get("total_tokens"))

    except (asyncio.CancelledError, GeneratorExit):
        if not run_finished:
            await db.rollback()
            await _finalize_run_async(db, run_id, "cancelled", error_msg="用户停止生成")
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"Agent[stream] 运行失败(async): run_id={run_id}, error={type(e).__name__}: {e}", exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        await _finalize_run_async(db, run_id, "failed", error_msg=str(e), exc=e)
        yield make_error(message="服务暂时异常，请稍后重试", detail=str(e)[:300])
        return
