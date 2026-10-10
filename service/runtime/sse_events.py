"""
SSE 事件格式定义
所有推送事件统一走这里，保证类型和字段一致。
SSE 格式规则（参考 ExperienceRecall 教训）：
  - 每条事件用 \n\n 分隔
  - event: xxx  \n data: {...}
  - 必须禁用代理缓冲 header（X-Accel-Buffering: no）
  - 首次写入前必须 flushHeaders
事件类型及 payload：
  - route          { reason, department_code, target_agent_id, matched_keywords, alternatives, unavailable } → 中央 Agent 的转交决定（仅中央 Agent 新建会话时）
  - ready          { run_id }                            → 会话已就绪，开始
  - thinking       { content, tool_calls? }              → Agent 思考步（LLM 返回的 thought）
  - tool_call      { name, args, step_no }               → 决定调用某个工具
  - tool_result    { name, result, step_no }             → 工具执行完毕
  - retrieval      { hit_count, content_preview, stats? } → RAG 检索结果（stats=上下文压缩/Token 节省）
  - memory         { action, message }                   → 记忆加载/总结
  - answer         { content }                           → 最终回答（完整字符串一次性推，Step1 先不做 token 级）
  - done           { run_id, steps, answer_length }      → 全部完成
  - error          { message, detail? }                  → 异常
"""
import json
from typing import Any,Dict,Optional
# 事件类型常量
EVENT_READY     = "ready"
EVENT_THINKING  = "thinking"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_RESULT = "tool_result"
EVENT_RETRIEVAL = "retrieval"
EVENT_CITATIONS = "citations"
EVENT_MEMORY    = "memory"
EVENT_ANSWER    = "answer"
EVENT_DONE      = "done"
EVENT_ERROR     = "error"
#SSE响应头（路由层直接引用，避免各处重复写）
SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # 禁用 Nginx/反向代理 缓冲
}
def format_event(event_type: str, data: Any) -> str:
    """把事件类型+数据格式化为 SSE 消息字符串
        event: thinking
        data: {"content": "..."}
        结尾 \n\n 为 SSE 协议分隔符）
        """
    data_json = json.dumps(data, ensure_ascii=False)
    # 按 SSE 协议格式封装事件类型和数据，两个换行表示事件结束
    return f"event: {event_type}\ndata: {data_json}\n\n"
def make_ready(run_id: int) -> str:
    return format_event(EVENT_READY, {"run_id": run_id})

def make_thinking(content: str, tool_calls: Optional[list] = None) -> str:
    payload: Dict[str, Any] = {"content": content or ""}
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return format_event(EVENT_THINKING, payload)

def make_tool_call(name: str, args: Any, step_no: int) -> str:
    return format_event(EVENT_TOOL_CALL, {
        "name": name, "args": args, "step_no": step_no
    })

def make_tool_result(name: str, result: str, step_no: int) -> str:
    return format_event(EVENT_TOOL_RESULT, {
        "name": name, "result": result, "step_no": step_no
    })

def make_retrieval(hit_count: int, content_preview: str,
                   stats: Optional[Dict[str, Any]] = None) -> str:
    payload: Dict[str, Any] = {
        "hit_count": hit_count,
        "content_preview": (content_preview or "")[:300],
    }
    if stats:
        payload["stats"] = stats  # RAG 上下文压缩 / Token 节省，见 service/rag/rag_stats.py
    return format_event(EVENT_RETRIEVAL, payload)

def make_citations(citations: list) -> str:
    """回答引用来源（多知识库空间检索时非空）。"""
    return format_event(EVENT_CITATIONS, {"citations": citations or []})


def make_memory(action: str, message: str) -> str:
    return format_event(EVENT_MEMORY, {"action": action, "message": message})

def make_answer(content: str) -> str:
    return format_event(EVENT_ANSWER, {"content": content})

def make_answer_delta(content: str) -> str:
    return format_event("answer_delta", {"content": content})

def make_done(run_id: Optional[int], steps: int, answer_length: int,
              conversation_id: Optional[int] = None, tokens: Optional[int] = None) -> str:
    payload: Dict[str, Any] = {
        "run_id": run_id,
        "steps": steps,
        "answer_length": answer_length,
    }
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    if tokens is not None:
        payload["tokens"] = tokens
    return format_event(EVENT_DONE, payload)
def make_error(message: str, detail: Optional[str] = None) -> str:
    payload: Dict[str, Any] = {"message": message}
    if detail:
        payload["detail"] = detail
    return format_event(EVENT_ERROR, payload)
