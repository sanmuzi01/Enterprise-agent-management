"""
会话系统路由层
接口设计：
  POST   /conversation                       创建会话
  GET    /conversation/{agent_id}            查询某Agent下的会话列表
  GET    /conversation/{conversation_id}     查询单个会话详情
  GET    /conversation/{conversation_id}/messages  查询会话消息历史
  PUT    /conversation/{conversation_id}     更新会话标题
  DELETE /conversation/{conversation_id}     删除会话（级联删消息）
路由层职责：鉴权(get_current_user) + 入参校验 + HTTP 异常转换
"""
from utils.http_headers import attachment_disposition
from fastapi import APIRouter, Depends, Query, Response
from service.exceptions import InvalidInput, NotFound
from typing import Optional
from pydantic import BaseModel, Field
from models.init_db import User
from models.async_db import get_async_db
from service.dependencies import get_current_user_async
from service import conversation_async_service
router = APIRouter(prefix="/conversation", tags=["会话管理"])
# ========== 请求模型 ==========
class ConversationCreate(BaseModel):
    """创建会话请求体"""
    agent_id: int
    title: Optional[str] = Field(default=None, max_length=255)

class ConversationUpdate(BaseModel):
    """更新会话请求体（目前只支持改标题）"""
    title: str = Field(min_length=1, max_length=255)


class ConversationFlagsUpdate(BaseModel):
    """更新会话标记。"""
    is_pinned: Optional[int] = Field(default=None, ge=0, le=1)
    is_archived: Optional[int] = Field(default=None, ge=0, le=1)

# ========== 接口 ==========

@router.post("", summary="创建会话")
async def create_conversation(
        body: ConversationCreate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    """创建新会话（标题为空时默认"新会话"，发首条消息后自动生成）"""
    result = await conversation_async_service.create_conversation(
        async_db, user_id=current_user.id, agent_id=body.agent_id, title=body.title
    )
    if result is None:
        raise NotFound("智能体不存在或无权限")
    return result

@router.get("/agent/{agent_id}", summary="查询Agent下的会话列表")
async def list_conversations(
        agent_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    """查询当前用户某Agent下的所有会话（按最近活跃倒序）"""
    return await conversation_async_service.list_conversations(async_db, current_user.id, agent_id)

@router.get("/{conversation_id}", summary="查询单个会话")
async def get_conversation(
        conversation_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    """查询单个会话详情（含权限校验）"""
    result = await conversation_async_service.get_conversation(async_db, current_user.id, conversation_id)
    if result is None:
        raise NotFound("会话不存在或无权限")
    return result
@router.get("/{conversation_id}/messages", summary="查询会话消息历史")
async def get_messages(
        conversation_id: int,
        limit: int = Query(default=100, ge=1, le=500),
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    """查询会话下的所有消息（正序，用于聊天历史展示）"""
    result = await conversation_async_service.list_messages(async_db, current_user.id, conversation_id, limit)
    if result is None:
        raise NotFound("会话不存在或无权限")
    return result


@router.get("/{conversation_id}/export", summary="导出会话")
async def export_conversation(
        conversation_id: int,
        format: str = "markdown",
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    fmt = format.lower()
    if fmt not in {"markdown", "json"}:
        raise InvalidInput("format 仅支持 markdown 或 json")
    result = await conversation_async_service.export_conversation(async_db, current_user.id, conversation_id, fmt)
    if result is None:
        raise NotFound("会话不存在或无权限")
    return Response(
        content=result["content"],
        media_type=f'{result["media_type"]}; charset=utf-8',
        headers={"Content-Disposition": attachment_disposition(result["filename"])},
    )

@router.put("/{conversation_id}", summary="更新会话标题")
async def update_conversation(
        conversation_id: int,
        body: ConversationUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    """更新会话标题"""
    result = await conversation_async_service.update_conversation_title(
        async_db, current_user.id, conversation_id, body.title
    )
    if result is None:
        raise NotFound("会话不存在或无权限")
    return result


@router.patch("/{conversation_id}/flags", summary="更新会话置顶/归档状态")
async def update_conversation_flags(
        conversation_id: int,
        body: ConversationFlagsUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    if body.is_pinned is None and body.is_archived is None:
        raise InvalidInput("没有要更新的字段")
    result = await conversation_async_service.update_conversation_flags(
        async_db,
        current_user.id,
        conversation_id,
        is_pinned=body.is_pinned,
        is_archived=body.is_archived,
    )
    if result is None:
        raise NotFound("会话不存在或无权限")
    return result

@router.delete("/{conversation_id}", summary="删除会话")
async def delete_conversation(
        conversation_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    """删除会话（级联删除其下所有消息）"""
    success = await conversation_async_service.delete_conversation(async_db, current_user.id, conversation_id)
    if not success:
        raise NotFound("会话不存在或无权限")
    return {"message": "删除成功", "conversation_id": conversation_id}
