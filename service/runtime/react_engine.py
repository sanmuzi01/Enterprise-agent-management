"""
ReAct 推理引擎 - 基于 LangGraph StateGraph
职责：Agent思考 → 调用工具 → 观察结果 → 继续思考 → 生成最终回答
1. 可控：每个节点都能插入轨迹记录(写入 AgentStep 表)
2. 可调：能控制最大迭代次数、工具调用前后的钩子
3. 可观测：每一步的状态都能检查
4. 可扩展：未来加 RAG节点/记忆节点/审核节点 都很方便
【ReAct 循环流程】
  START
    ↓
  agent_node (LLM思考：要不要调工具？)
    ↓
  ┌── 有tool_calls ──→ tool_node (执行工具) ──→ agent_node (继续思考)
  │
  └── 无tool_calls ──→ END (生成最终回答)
【架构位置】
  agent_runtime.py (编排层：RAG/记忆/轨迹/Chat保存)
      ↓ 调用
  react_engine.py (ReAct引擎 ← 本文件)
      ↓ 使用
  ChatOpenAI (LLM) + LangChain Tools (适配后的自定义工具)
"""
import operator
import threading
from typing import Dict, Any, List, Optional, Annotated
from typing_extensions import TypedDict#创建有固定字段的字典。
from langchain_core.messages import (
    BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage,
    message_chunk_to_message,
)#所有消息的父类，用户消息，模型回复，系统提示词，工具执行后的返回。
from langgraph.graph import StateGraph, END, START #创建 Agent 工作流图。入口和出口
from langgraph.graph.message import add_messages#状态合并函数
from langgraph.prebuilt import ToolNode#提供的工具执行节点
from langgraph.config import get_stream_writer
from utils.logger_handler import get_logger
from service.runtime.sse_events import make_thinking, make_tool_call, make_tool_result, make_answer, make_answer_delta
from service.runtime import action_guard
from service.llm.usage import extract_usage

logger = get_logger("react_engine")
#state定义
class AgentState(TypedDict):
    #react引擎的状态：消息列表和轨迹记录
    # add_messages 是 LangGraph 的 reducer：新消息会追加到列表，不会覆盖
    messages:Annotated[List[BaseMessage],add_messages]
    #轨迹记录
    steps: Annotated[List[Dict[str, Any]], operator.add]   # ← 加 reducer
class ReActEngine:
    #推理引擎，agent_runtime 调用 engine.invoke() 拿到最终回答 + 轨迹
    def __init__(self,
                 llm,tools,
                 max_iterations: int = 5,  # 最大迭代次数（防止死循环）
                 step_callback=None,  # 每步回调（供 agent_runtime 实时写轨迹）
                 ctx=None,  # ToolContext：工具内部调 LLM 的用量从这里回收进 self._usage
                 ):
        """:param llm: ChatOpenAI 实例
            :param tools: LangChain 工具列表
            :param max_iterations: 最大工具调用次数
            :param step_callback: 回调函数 fn(step_info: dict) → None
            每个节点执行后调用，用于实时记录轨迹
            :param ctx: 工具执行上下文（ToolContext），工具用它调 LLM 时会把用量记在
            ctx.usage_log 里；每次工具节点跑完就取走汇总，避免这次运行的总用量漏计"""
        self.llm = llm
        self.tools = tools
        self.max_iterations = max_iterations
        self.step_callback = step_callback
        self._ctx = ctx
        self._iteration_count = 0
        self._force_finalize = False
        self._streaming = False
        self.cancel_event = threading.Event()
        self._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self._usage_calls = 0
        # 防编造（service/runtime/action_guard.py）：只对绑了办理类工具（write / high_risk）的助手生效
        self._guard_enabled = any(action_guard.is_action_tool(getattr(t, "name", "")) for t in (tools or []))
        self._action_done = False      # 这一次运行里有没有办理类工具真的办成（成功或已生成待确认单）
        self._guard_retries = 0
        self._guard_retry_pending = False
        #工具绑定llm
        if tools:
            self.llm_with_tools = llm.bind_tools(tools)
        else:
            self.llm_with_tools = llm
        #构建图
        self.graph = self._build_graph()
    def _build_graph(self)->StateGraph:
        #用“状态（State）”驱动的工作流图
        workflow = StateGraph(AgentState)
        #添加节点
        workflow.add_node("agent",self._agent_node)
        if self.tools:
            self.tool_node = ToolNode(self.tools)
            workflow.add_node("tools", self._run_tools)
        #设置入口,把工作流的入口 START 连接到名为 "agent" 的节点，Agent 运行时从这里开始执行。
        workflow.add_edge(START,"agent")
        #添加条件边：agent → tools or END
        if self.tools:
            workflow.add_conditional_edges(
                "agent",self._should_continue,{
                    "continue": "tools",
                    "retry": "agent",      # 回答声称已办理却没办：让模型重来一次
                    "end": END,
                },)
            # tools 执行完回到 agent 继续思考
            workflow.add_edge("tools", "agent")
        else:
            # 没有工具，直接结束
            workflow.add_edge("agent", END)

        # 5. 编译
        return workflow.compile()

    def _check_cancelled(self):
        if self.cancel_event.is_set():
            raise RuntimeError("Agent run cancelled")

    def _run_tools(self, state):
        self._check_cancelled()
        result = self.tool_node.invoke(state)
        tool_messages = result.get("messages", []) if isinstance(result, dict) else []
        for msg in tool_messages:
            if (isinstance(msg, ToolMessage) and action_guard.is_action_tool(getattr(msg, "name", "") or "")
                    and action_guard.tool_result_outcome(msg.content) in ("ok", "pending")):
                self._action_done = True
        # requires_context=True 的工具（比如 outline_generator）会自己调一次 LLM；
        # 这次运行的总用量必须把它算进去，不然「Token 用量」display 永远只统计主循环。
        if self._ctx is not None and hasattr(self._ctx, "drain_usage"):
            for usage in self._ctx.drain_usage():
                self._merge_usage(usage)
        return result

    def _merge_usage(self, usage):
        if not usage:
            return
        self._usage["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
        self._usage["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
        self._usage["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
        self._usage_calls += 1

    def _record_usage(self, response):
        self._merge_usage(extract_usage(response))

    def _reset_run(self, streaming=False):
        self._iteration_count = 0
        self._force_finalize = False
        self._streaming = streaming
        self._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self._usage_calls = 0
        self._action_done = False
        self._guard_retries = 0
        self._guard_retry_pending = False
        if self._ctx is not None and hasattr(self._ctx, "drain_usage"):
            self._ctx.drain_usage()  # 清掉上一次运行可能残留的用量记录

    def _finalize_messages(self, messages):
        # The last requested tools were not executed; do not send dangling tool_calls.
        if messages and isinstance(messages[-1], AIMessage) and messages[-1].tool_calls:
            return messages[:-1]
        return messages

    def _agent_node(self,state:AgentState)->Dict[str,Any]:
        #"Agent 思考节点：调 LLM 决定下一步（调工具 or 生成回答)
        messages = state["messages"]
        logger.info(f"[ReAct] Agent思考中... (消息数={len(messages)})")
        # 调 LLM（已绑定工具，LLM会决定是否调工具）
        self._check_cancelled()
        if self._streaming and callable(getattr(self.llm_with_tools, "stream", None)):
            writer = get_stream_writer()
            response = None
            stream = self.llm_with_tools.stream(messages)
            try:
                for part in stream:
                    self._check_cancelled()
                    response = part if response is None else response + part
                    if isinstance(part.content, str) and part.content:
                        writer(make_answer_delta(part.content))
            finally:
                if hasattr(stream, "close"):
                    stream.close()
            if response is None:
                raise ValueError("Model returned an empty stream")
            response = message_chunk_to_message(response)
        else:
            response = self.llm_with_tools.invoke(messages)
        self._check_cancelled()
        self._record_usage(response)
        # ===== 新增:详细日志,看LLM返回了什么 =====
        tool_calls = response.tool_calls if hasattr(response, "tool_calls") else []
        content_preview = (response.content or "")[:100] if response.content else "(空)"
        logger.info(
            f"[ReAct] LLM返回: content='{content_preview}', "
            f"tool_calls数量={len(tool_calls)}"
        )
        for i, tc in enumerate(tool_calls):
            logger.info(f"[ReAct]   tool_call[{i}]: name={tc.get('name')}, args={tc.get('args')}")
        #记录
        step_info = {
            "step_type": "thought",
            "content": response.content,
            "tool_calls": response.tool_calls if hasattr(response, "tool_calls") else None,
        }
        if self.step_callback:
            self.step_callback(step_info)
        if not tool_calls:
            guarded = self._guard_final_answer(response)
            if guarded is not None:
                return guarded
        #返回新消息
        return {"messages": [response], "steps": [step_info]}

    def _guard_final_answer(self, response):
        """最终回答说“已办理”，这一轮却没有办理类工具办成：第一次让模型重来，第二次在回答末尾附上核对提示。
        返回 None 表示放行。"""
        content = response.content if isinstance(response.content, str) else ""
        if not self._guard_enabled or self._action_done or not action_guard.claims_action(content):
            return None
        if self._guard_retries < 1:
            self._guard_retries += 1
            self._guard_retry_pending = True
            logger.warning("[ReAct] 回答声称已办理但本轮没有执行任何办理类工具，要求模型重新办理")
            guard_step = {"step_type": "guard", "content": "回答声称已办理但没有调用办理类工具，已要求重新办理"}
            if self.step_callback:
                self.step_callback(guard_step)
            return {"messages": [response, HumanMessage(content=action_guard.GUARD_RETRY_PROMPT)],
                    "steps": [guard_step]}
        logger.warning("[ReAct] 重试后回答仍声称已办理但没有执行办理类工具，附上系统核对提示")
        guard_step = {"step_type": "guard", "content": "重试后仍声称已办理但没有调用办理类工具，已在回答后附上核对提示"}
        if self.step_callback:
            self.step_callback(guard_step)
        flagged = AIMessage(content=content + action_guard.GUARD_WARNING)
        return {"messages": [flagged], "steps": [guard_step]}

    def _should_continue(self, state: AgentState) -> str:
        # 条件判断，是否调用工具
        if self._guard_retry_pending:
            self._guard_retry_pending = False
            return "retry"
        last_message = state["messages"][-1]
        # 查询次数，防止死循环
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            if self._iteration_count >= self.max_iterations:
                logger.warning(f"[ReAct] 达到最大迭代次数 {self.max_iterations}，强制生成最终回答")
                # 不直接结束，而是注入一条提示让 LLM 基于已有信息收尾
                self._force_finalize = True
                return "end"
            self._iteration_count += 1
            logger.info(
                f"[ReAct] 决定调用工具: "
                f"{[tc['name'] for tc in last_message.tool_calls]}"
            )
            return "continue"
            # 没有工具调用 → LLM 生成了最终回答
        logger.info("[ReAct] 生成最终回答，结束循环")
        return "end"
    def invoke(self,system_prompt:str,user_message:str,
                history:Optional[List[Dict[str,str]]]=None,
               ) -> Dict[str, Any]:
        """执行 ReAct 循环
         :param system_prompt: 系统提示词（Agent的YML prompt + RAG上下文 + 记忆）
        :param user_message: 用户问题
        :param history: 历史对话（可选，OpenAI格式 [{"role":"user","content":"..."}]）
        :return: {
            "answer": 最终回答,
            "steps": 轨迹列表,
            "total_iterations": 实际迭代次数}"""
        #1，组装初始消息
        self._reset_run()
        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        #历史对话转LangChain消息
        if history:
            for msg in history:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))#用户说的话
                elif msg["role"]=="assistant":
                    messages.append(AIMessage(content=msg["content"]))#模型的回复
        # 当前用户问题
        messages.append(HumanMessage(content=user_message))
        #2，初始状态
        initial_state ={
            "messages":messages,
            "steps":[]
        }
        #3,执行图
        logger.info(f"[ReAct] 开始执行，system_prompt长度={len(system_prompt) if system_prompt else 0}")
        final_state = self.graph.invoke(initial_state, {"recursion_limit": max(25, self.max_iterations * 2 + 5)})

        # 3.5 如果因达到最大迭代强制结束，再调一次 LLM（不绑工具）让它收尾
        if self._force_finalize:
            logger.info("[ReAct] 触发收尾调用：让 LLM 基于已有工具结果生成最终回答")
            finalize_messages = self._finalize_messages(final_state["messages"]) + [
                HumanMessage(content=(
                    "已达到工具调用上限。请根据已有的工具执行结果，"
                    "直接给用户一个完整、自然的最终回答。"
                    "不要再次调用工具。"
                ))
            ]
            try:
                self._check_cancelled()
                final_response = self.llm.invoke(finalize_messages)
                self._record_usage(final_response)
                final_state["messages"].append(final_response)
            except Exception as e:
                self._check_cancelled()
                logger.warning(f"[ReAct] 收尾调用失败: {e}")

        if self.step_callback:
            for msg in final_state.get("messages", []):
                if isinstance(msg, ToolMessage):
                    content = getattr(msg, "content", "") or ""
                    self.step_callback({
                        "step_type": "tool_result",
                        "tool_name": getattr(msg, "name", ""),
                        "tool_result": content,
                        "permission_denied": "工具权限不足" in content,
                    })

        # 4. 提取最终回答（最后一条 AI 消息）
        final_messages = final_state["messages"]
        final_answer=""
        for msg in reversed(final_messages):#从后往前遍历。
            if isinstance(msg,AIMessage)and msg.content:#判断这个msg是不是 AIMessage,已经是否为空
                final_answer = msg.content
                break
        logger.info(
            f"[ReAct] 执行完成，迭代{self._iteration_count}次，"
            f"回答长度={len(final_answer)}"
        )
        return {
            "answer": final_answer,
            "steps": final_state.get("steps", []),
            "total_iterations": self._iteration_count,
            "usage": dict(self._usage) if self._usage_calls else None,
            "all_messages": final_messages,  # 完整消息历史（调试用）
        }
    def invoke_stream(self,system_prompt:str,
                      user_message: str,
                      history: Optional[List[Dict[str, str]]] = None,
    ):
        """流式执行 ReAct 循环（生成器，yield SSE 事件字符串）
            返回值：通过 yield 逐段推送事件，最终在 StopIteration.value 里返回完整结果
            （Python 生成器规则：生成器内部 return 会把值放进 StopIteration.value，供外部捕获）
                 for event in engine.invoke_stream(...):
                    print(event)   # 每条是 format_event 格式的 SSE 字符串
                """
        # 1. 组装初始消息（和 invoke 完全一致）
        self._reset_run(streaming=True)
        messages= []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        if history:
            for msg in history:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))
        messages.append(HumanMessage(content=user_message))
        initial_state = {"messages": messages, "steps": []}
        logger.info(f"[ReAct][stream] 开始执行，system_prompt长度={len(system_prompt) if system_prompt else 0}")

        final_answer = ""
        final_messages = messages[:]  #累积消息,供收尾用
        step_no = 0
        # 2. 使用 graph.stream() 逐节点产出状态
        #    LangGraph stream() 的每一项形如：
        #      {"agent": {"messages": [...], "steps": [...]}}
        #      {"tools": {"messages": [ToolMessage, ...]}}
        for mode, chunk in self.graph.stream(
            initial_state,
            {"recursion_limit": max(25, self.max_iterations * 2 + 5)},
            stream_mode=["custom", "updates"],
        ):
            self._check_cancelled()
            if mode == "custom":
                yield chunk
                continue
            for node_name, node_output in chunk.items():
                # ---- 节点: agent（LLM 思考完成） ----
                if node_name =="agent":
                    msgs = node_output.get("messages",[])
                    if msgs and isinstance(msgs[-1], HumanMessage):
                        # 防编造重试：刚才流出去的“已办理”是假的，清空页面上的回答，等模型重新办理
                        yield make_answer("")
                    elif msgs:
                        last_ai = msgs[-1]  # AIMessage
                        content = getattr(last_ai, "content", "") or ""
                        tool_calls = getattr(last_ai, "tool_calls", None) or []

                        # 有工具调用 → 同时发 thinking + 每个 tool_call 事件
                        if tool_calls:
                            yield make_thinking(content, tool_calls=tool_calls)
                            for tc in ([] if self._force_finalize else tool_calls):
                                step_no += 1
                                yield make_tool_call(
                                    name=tc.get("name", ""),
                                    args=tc.get("args", {}),
                                    step_no=step_no,
                                )
                        else:
                            # 无工具调用：这就是最终回答
                            final_answer = content
                            yield make_thinking(content)
                            yield make_answer(content)

                        # step_callback 不在这里重复调用：_agent_node 已经记过一次这个 thought，
                        # 这里再调会让同一步骤在轨迹里出现两次。

                        # 累积最终 messages（供收尾用）
                    if "messages" in node_output:
                        final_messages += list(node_output["messages"])

                        # ---- 节点: tools（工具执行完成） ----
                elif node_name == "tools":
                    tool_msgs = node_output.get("messages", [])
                    for tm in tool_msgs:
                        # ToolMessage 的 name 字段 = 工具名，content = 工具结果
                        tname = getattr(tm, "name", "")
                        tresult = getattr(tm, "content", "") or ""
                        step_no += 1
                        yield make_tool_result(
                            name=tname, result=tresult, step_no=step_no
                        )
                        if self.step_callback:
                            self.step_callback({
                                "step_type": "tool_result",
                                "tool_name": tname,
                                "tool_result": tresult,
                                "permission_denied": "工具权限不足" in tresult,
                            })
                        # 累积 messages
                    if "messages" in node_output:
                        final_messages += list(tool_msgs)
        # 3. 达到最大迭代强制结束 → 再调一次 LLM（不绑工具）生成最终回答
        if self._force_finalize and not final_answer:
            logger.info("[ReAct][stream] 触发收尾调用：让 LLM 基于已有工具结果生成最终回答")
            finalize_messages = self._finalize_messages(final_messages) + [
                HumanMessage(
                    content=(
                        "已达到工具调用上限。请根据已有的工具执行结果，"
                        "直接给用户一个完整、自然的最终回答。"
                        "不要再次调用工具。"
                    )
                )
            ]
            try:
                self._check_cancelled()
                final_response = self.llm.invoke(finalize_messages)
                self._record_usage(final_response)
                final_answer = getattr(final_response, "content", "") or ""
                final_messages.append(final_response)
                yield make_thinking(final_answer)
                yield make_answer(final_answer)
            except Exception as e:
                self._check_cancelled()
                logger.warning(f"[ReAct][stream] 收尾调用失败: {e}")

        logger.info(
            f"[ReAct][stream] 执行完成，迭代{self._iteration_count}次，"
            f"回答长度={len(final_answer)}"
        )

        # 4. 通过 StopIteration 返回完整结果（供 Runtime 持久化用）
        result = {
            "answer": final_answer,
            "steps": [],  # 轨迹由 step_callback 已写入 DB，这里不再重复攒
            "total_iterations": self._iteration_count,
            "usage": dict(self._usage) if self._usage_calls else None,
            "all_messages": final_messages,
        }
        return result
