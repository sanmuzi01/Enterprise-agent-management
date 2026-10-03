"""
聊天对话路由层
接口：
  POST /chat/{agent_id}                  同步对话（兼容旧版 + conversation_id 可选）
  POST /chat/{agent_id}/stream           SSE 流式对话（兼容旧版 + conversation_id 可选）
  GET  /chat/{agent_id}/history          旧版（查询Chat表），保留向后兼容

迁移边界：整个 chat 路由已收口到 AsyncSession（阶段 2/3）——
  - history 纯读            → get_async_db + chat_async_service
  - POST /chat/{id}         → get_async_db → chat_service.chat_with_agent → run_with_history_async
  - POST /chat/{id}/stream  → get_async_db → chat_service.chat_with_agent_stream_async
                              → run_stream_with_history_async（LangGraph 同步流经 worker 线程 + Queue 桥回）
ReAct 引擎（含 ToolExecutor 装配）整段跑在 asyncio.to_thread + 自带同步 Session。
"""
from fastapi import APIRouter, Depends, HTTPException, status
from service.exceptions import InvalidInput, PermissionDenied
from pydantic import BaseModel, Field
from typing import List, Optional
from models.async_db import get_async_db
from models.init_db import User
from service.dependencies import get_current_user_async
from service import attachment_service, chat_async_service, chat_service, quota_service, tool_confirmation_service
from service.runtime import central_router
from fastapi.responses import StreamingResponse
from service.runtime.sse_events import SSE_HEADERS, format_event
from utils.rate_limit import LimitExceeded, concurrency_guard, require_limit

router = APIRouter(prefix="/chat", tags=["聊天对话"])

class ChatRequest(BaseModel):
    """对话请求体
    :param message: 用户消息（必填）
    :param conversation_id: 会话ID（可选）
        None = 自动新建会话，向后兼容旧调用方
        有值 = 复用已有会话，追加消息 + 历史上下文
    """
    message: str = Field(min_length=1, max_length=5000)
    conversation_id: Optional[int] = Field(default=None, ge=1)
    # 先经 POST /attachment 上传拿到 ID；只是把文件名和 ID 写进消息，供助手用 run_skill_script 传给脚本
    attachment_ids: List[str] = Field(default_factory=list, max_length=attachment_service.MAX_ATTACHMENTS_PER_MESSAGE)


def _message_with_attachments(user: User, request: ChatRequest) -> str:
    if not request.attachment_ids:
        return request.message
    lines = []
    for att_id in request.attachment_ids:
        found = attachment_service.resolve(user.id, att_id)
        if not found:
            raise InvalidInput("有附件不存在或已过期，请重新上传")
        lines.append(f"- {found[1]}（附件ID：{att_id}）")
    return f"{request.message}\n\n【用户上传的附件】\n" + "\n".join(lines)


def _limit_error(exc: LimitExceeded) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=exc.message,
        headers={"Retry-After": str(exc.retry_after)},
    )


@router.post("/{agent_id}", summary="发送对话（同步）")
async def chat(
        agent_id: int,
        request: ChatRequest,
        db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    """同步对话。conversation_id=None 时自动新建会话并返回 conversation_id。"""
    # 提前把 id 存成普通 int：下面这次请求里 service 层会提交好几次事务（AsyncSession 默认
    # expire_on_commit=True，每提交一次就把 current_user 这个 ORM 对象的所有属性标记为"过期"），
    # 之后再读 current_user.id 会触发一次隐式懒加载去刷新它——如果这次访问发生在请求本来的
    # greenlet 上下文之外（比如流式响应收尾、生成器清理阶段），SQLAlchemy 找不到桥接的
    # greenlet 就会直接崩：MissingGreenlet。先存成 int，后面全用这个值，彻底不碰那次懒加载。
    user_id = current_user.id
    try:
        require_limit(
            key=f"chat:user:{user_id}",
            limit_env="CHAT_RATE_LIMIT",
            default_limit=20,
            window_env="CHAT_RATE_WINDOW_SECONDS",
            default_window=60,
            label="聊天请求",
        )
    except LimitExceeded as e:
        raise _limit_error(e)
    user_message = _message_with_attachments(current_user, request)
    # Phase 3D 阶段3：只有 agent_id 指向的是 agent_type="central" 的 Agent 才会真的路由，
    # 现存所有 Agent 默认值都是 personal，这一步对它们是原样返回 agent_id 的空操作。
    plan = await central_router.plan_route_async(db, user_id, agent_id, user_message, request.conversation_id)
    agent_id = plan.target_agent_id
    await central_router.record_handoff_async(db, user_id, plan, user_message)
    quota_before = await quota_service.enforce_quota_async(db, user_id)
    try:
        with concurrency_guard(
            key=f"agent_run:user:{user_id}",
            limit_env="USER_MAX_CONCURRENT_AGENT_RUNS",
            default_limit=2,
            ttl_env="AGENT_RUN_CONCURRENCY_TTL_SECONDS",
            default_ttl=300,
            label="Agent",
        ):
            result =  await chat_service.chat_with_agent(
                db=db,
                user=current_user,
                agent_id=agent_id,
                user_message=user_message,
                conversation_id=request.conversation_id,
            )
    except LimitExceeded as e:
        raise _limit_error(e)
    except ValueError as e:
        raise PermissionDenied(str(e))
    if "message" in result and "answer" not in result:
        raise InvalidInput(result["message"])
    await quota_service.check_and_notify_threshold_async(db, user_id, quota_before["used_tokens"])
    if plan.public():
        result["routing"] = plan.public()
    return result

@router.post("/{agent_id}/stream", summary="发送对话（SSE流式）")
async def chat_stream(
        agent_id: int,
        request: ChatRequest,
        db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    """SSE 流式对话（阶段 3：AsyncSession + 异步生成器）。conversation_id=None 时自动新建会话。
    事务：路由层不统一 commit，chat_service 内部每步 flush、存 AI 消息后 commit 一次；
    LangGraph 同步流经 worker 线程 + asyncio.Queue 桥回。
    """
    # 提前把 id 存成普通 int，原因见 chat()：流式响应收尾时（limited_generator 的 finally 块，
    # 可能跑在生成器清理阶段而不是本次请求原本的 greenlet 上下文里）如果这时候才第一次去读
    # current_user.id，遇上属性因为中途 commit 过而"过期"，隐式懒加载会直接 MissingGreenlet 崩掉。
    user_id = current_user.id
    try:
        require_limit(
            key=f"chat:user:{user_id}",
            limit_env="CHAT_RATE_LIMIT",
            default_limit=20,
            window_env="CHAT_RATE_WINDOW_SECONDS",
            default_window=60,
            label="聊天请求",
        )
    except LimitExceeded as e:
        raise _limit_error(e)
    user_message = _message_with_attachments(current_user, request)
    plan = await central_router.plan_route_async(db, user_id, agent_id, user_message, request.conversation_id)
    agent_id = plan.target_agent_id
    await central_router.record_handoff_async(db, user_id, plan, user_message)
    quota_before = await quota_service.enforce_quota_async(db, user_id)
    try:
        lease_guard = concurrency_guard(
            key=f"agent_run:user:{user_id}",
            limit_env="USER_MAX_CONCURRENT_AGENT_RUNS",
            default_limit=2,
            ttl_env="AGENT_RUN_CONCURRENCY_TTL_SECONDS",
            default_ttl=300,
            label="Agent",
        )
        lease_guard.__enter__()
    except LimitExceeded as e:
        raise _limit_error(e)

    generator = chat_service.chat_with_agent_stream_async(
        db=db,
        user=current_user,
        agent_id=agent_id,
        user_message=user_message,
        conversation_id=request.conversation_id,
    )

    async def limited_generator():
        try:
            if plan.public():
                yield format_event("route", plan.public())
            async for event in generator:
                yield event
        finally:
            try:
                await generator.aclose()
            finally:
                lease_guard.__exit__(None, None, None)
            await quota_service.check_and_notify_threshold_async(db, user_id, quota_before["used_tokens"])

    return StreamingResponse(
        limited_generator(),
        headers=SSE_HEADERS,
        media_type="text/event-stream",
    )

@router.get("/{agent_id}/history", summary="获取对话历史（旧版Chat表，兼容）")
async def get_history(
        agent_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    """旧版历史（从Chat表查），保留向后兼容。新版请用：
    GET /conversation/{agent_id} → 会话列表
    GET /conversation/{conversation_id}/messages → 消息历史
    """
    return await chat_async_service.list_legacy_history(async_db, current_user.id, agent_id, limit=20)


@router.post("/tool-confirmations/{token}/confirm", summary="确认并执行一次高风险 Agent 工具调用")
async def confirm_tool_call(token: str, current_user: User = Depends(get_current_user_async)):
    """第五轮审计 P0-2：submit/approve/reject 这类高风险工具在 ReAct 循环里只会生成一条
    待确认单（见 service/tool_confirmation_service.py），必须用户在这里主动确认才会
    真正执行——ReAct 循环本身没有任何路径能触达这个接口。"""
    try:
        return await tool_confirmation_service.confirm_and_execute_async(token, current_user.id)
    except tool_confirmation_service.ConfirmationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/tool-confirmations/{token}/reject", summary="取消一次待确认的高风险 Agent 工具调用")
async def reject_tool_call(token: str, current_user: User = Depends(get_current_user_async)):
    try:
        await tool_confirmation_service.reject_async(token, current_user.id)
    except tool_confirmation_service.ConfirmationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    return {"status": "rejected"}
