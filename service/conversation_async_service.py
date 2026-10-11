"""会话异步服务。"""

from typing import Any, Dict, List, Optional

from models import conversation_async_dao as dao
from models.init_db import Conversation, Message
from service.access_control import get_usable_agent_async
from service.conversation_service import auto_generate_title
from utils.logger_handler import get_logger

logger = get_logger("conversation_async_service")


def _conv_to_dict(conv: Conversation) -> Dict[str, Any]:
    return {
        "id": conv.id,
        "user_id": conv.user_id,
        "agent_id": conv.agent_id,
        "entry_agent_id": getattr(conv, "entry_agent_id", None),
        "title": conv.title,
        "is_pinned": conv.is_pinned or 0,
        "is_archived": conv.is_archived or 0,
        "create_time": conv.create_time.strftime("%Y-%m-%d %H:%M:%S") if conv.create_time else None,
        "update_time": conv.update_time.strftime("%Y-%m-%d %H:%M:%S") if conv.update_time else None,
    }


def _msg_to_dict(msg: Message) -> Dict[str, Any]:
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "role": msg.role,
        "content": msg.content,
        "create_time": msg.create_time.strftime("%Y-%m-%d %H:%M:%S") if msg.create_time else None,
    }


async def list_conversations(
        db, user_id: int, agent_id: int, limit: int = 50,
) -> List[Dict[str, Any]]:
    """异步查询会话列表。"""

    if await get_usable_agent_async(db, user_id, agent_id) is None:
        logger.warning(f"权限拒绝：用户{user_id}尝试列出助手 {agent_id} 的会话")
        return []
    convs = await dao.list_conversations_by_agent_async(db, user_id, agent_id, limit)
    items = [_conv_to_dict(conv) for conv in convs]
    # 经中央助手转交出去的会话：标出实际是哪个部门助手在办（会话列表里显示“已转交给 ××”）
    others = {c["agent_id"] for c in items if c["agent_id"] != agent_id}
    if others:
        from sqlalchemy import select
        from models.init_db import Agent
        names = dict((await db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(others)))).all())
        for c in items:
            if c["agent_id"] != agent_id:
                c["handled_by"] = names.get(c["agent_id"])
    return items


async def create_conversation(
        db, user_id: int, agent_id: int, title: str = None,
) -> Optional[Dict[str, Any]]:
    """异步创建会话。"""
    if await get_usable_agent_async(db, user_id, agent_id) is None:
        logger.warning(f"权限拒绝：用户{user_id}尝试为助手 {agent_id} 创建会话")
        return None
    conv = await dao.create_conversation_async(
        db,
        user_id=user_id,
        agent_id=agent_id,
        title=title or "新会话",
    )
    await db.commit()
    return _conv_to_dict(conv)


async def get_conversation(db, user_id: int, conversation_id: int) -> Optional[Dict[str, Any]]:
    """异步查询单个会话。"""

    conv = await dao.get_owned_conversation_async(db, user_id, conversation_id)
    if not conv:
        logger.warning(f"权限拒绝：用户{user_id}尝试访问会话{conversation_id}")
        return None
    return _conv_to_dict(conv)


async def list_messages(
        db, user_id: int, conversation_id: int, limit: int = 100,
) -> Optional[List[Dict[str, Any]]]:
    """异步查询会话消息。"""

    conv = await dao.get_owned_conversation_async(db, user_id, conversation_id)
    if not conv:
        return None
    msgs = await dao.list_messages_by_conversation_async(db, conversation_id, limit)
    return [_msg_to_dict(msg) for msg in msgs]


async def update_conversation_title(
        db, user_id: int, conversation_id: int, title: str,
) -> Optional[Dict[str, Any]]:
    conv = await dao.get_owned_conversation_async(db, user_id, conversation_id)
    if not conv:
        return None
    conv = await dao.update_conversation_title_async(db, conv, title)
    await db.commit()
    return _conv_to_dict(conv)


async def update_conversation_flags(
        db,
        user_id: int,
        conversation_id: int,
        is_pinned: Optional[int] = None,
        is_archived: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    conv = await dao.get_owned_conversation_async(db, user_id, conversation_id)
    if not conv:
        return None
    if is_pinned is not None:
        is_pinned = 1 if is_pinned else 0
    if is_archived is not None:
        is_archived = 1 if is_archived else 0
    conv = await dao.update_conversation_flags_async(
        db,
        conv,
        is_pinned=is_pinned,
        is_archived=is_archived,
    )
    await db.commit()
    return _conv_to_dict(conv)


async def export_conversation(
        db, user_id: int, conversation_id: int, fmt: str = "markdown",
) -> Optional[Dict[str, str]]:
    """异步导出会话为 markdown / json（纯读 + 字符串拼装）。"""

    conv = await dao.get_owned_conversation_async(db, user_id, conversation_id)
    if not conv:
        return None
    messages = await dao.list_messages_by_conversation_async(db, conversation_id, 1000)
    safe_title = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (conv.title or "")
    ).strip("_") or "conversation"

    if fmt == "json":
        import json

        content = json.dumps(
            {
                "conversation": _conv_to_dict(conv),
                "messages": [_msg_to_dict(msg) for msg in messages],
            },
            ensure_ascii=False,
            indent=2,
        )
        return {
            "filename": f"{safe_title}_{conv.id}.json",
            "media_type": "application/json",
            "content": content,
        }

    lines = [f"# {conv.title}", "", f"- 会话ID: {conv.id}", f"- Agent ID: {conv.agent_id}", ""]
    for msg in messages:
        role = {"user": "用户", "assistant": "助手", "system": "系统"}.get(msg.role, msg.role)
        lines.extend([f"## {role}", "", msg.content or "", ""])
    return {
        "filename": f"{safe_title}_{conv.id}.md",
        "media_type": "text/markdown",
        "content": "\n".join(lines),
    }


async def delete_conversation(db, user_id: int, conversation_id: int) -> bool:
    conv = await dao.get_owned_conversation_async(db, user_id, conversation_id)
    if not conv:
        return False
    await dao.delete_conversation_async(db, conv)
    await db.commit()
    return True


# ========== 供 chat_service async 调用的辅助方法（对齐同步 conversation_service）==========

async def save_message_async(db, conversation_id: int, role: str, content: str) -> Message:
    """保存一条消息 + 刷新会话活跃时间。对齐同步 save_message；不在此 commit。"""
    msg = await dao.create_message_async(db, conversation_id, role, content)
    conv = await dao.get_conversation_by_id_async(db, conversation_id)
    if conv:
        await dao.touch_conversation_async(db, conv)
    return msg


async def load_history_for_llm_async(
        db, conversation_id: int, limit: int = 20,
) -> List[Dict[str, str]]:
    """最近 N 条消息作为 LLM 上下文，只取 user / assistant。对齐同步 load_history_for_llm。"""
    msgs = await dao.list_messages_for_history_async(db, conversation_id, limit)
    return [
        {"role": m.role, "content": m.content}
        for m in msgs if m.role in ("user", "assistant")
    ]


async def maybe_update_title_by_first_message_async(
        db, conversation_id: int, user_message: str,
) -> None:
    """首条消息时用消息内容自动生成会话标题。对齐同步 maybe_update_title_by_first_message。"""
    conv = await dao.get_conversation_by_id_async(db, conversation_id)
    if not conv:
        return
    if conv.title == "新会话":
        new_title = auto_generate_title(user_message)
        await dao.update_conversation_title_async(db, conv, new_title)
        logger.info(f"会话{conversation_id}自动生成标题: {new_title}")
