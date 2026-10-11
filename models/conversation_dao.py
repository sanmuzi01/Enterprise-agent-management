"""
会话系统 DAO 层
职责：纯数据库操作（CRUD），不含业务逻辑
遵循现有 DAO 风格：db.query + flush（不 commit，由路由层管事务）
"""
from sqlalchemy import or_

from utils.timeutil import utcnow
from typing import List, Optional
from models.init_db import Conversation, Message
# ========== Conversation 相关 ==========
def create_conversation(
        db,user_id: int,agent_id:int,title: str = "新会话"
) -> Conversation:
    """创建新会话"""
    conv = Conversation(
        user_id=user_id,
        agent_id=agent_id,
        title=title,
        create_time=utcnow(),
        update_time=utcnow(),
    )
    db.add(conv)
    db.flush()
    return conv
def get_conversation_by_id(db,conversation_id:int) -> Optional[Conversation]:
    """根据ID查询会话"""
    return db.query(Conversation).filter(Conversation.id == conversation_id).first()
def list_conversations_by_agent(
        db, user_id: int, agent_id: int, limit: int = 50
) -> List[Conversation]:
    """查询某用户某Agent下的所有会话（按最近更新时间倒序）"""
    return (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id,
                or_(Conversation.agent_id == agent_id, Conversation.entry_agent_id == agent_id))
        .order_by(
            Conversation.is_archived.asc(),
            Conversation.is_pinned.desc(),
            Conversation.update_time.desc(),
        )
        .limit(limit)
        .all()
    )

def update_conversation_title(db,conv: Conversation,title: str) -> Conversation:
    """更新会话标题"""
    conv.title = title
    conv.update_time = utcnow()
    db.flush()
    return conv

def touch_conversation(db,conv:Conversation) -> Conversation:
    """更新会话的最后活跃时间（发消息时调用）"""
    conv.update_time = utcnow()
    db.flush()
    return conv


def update_conversation_flags(db, conv: Conversation, is_pinned: int = None, is_archived: int = None) -> Conversation:
    """更新会话置顶/归档状态。"""
    if is_pinned is not None:
        conv.is_pinned = is_pinned
    if is_archived is not None:
        conv.is_archived = is_archived
    conv.update_time = utcnow()
    db.flush()
    return conv

def delete_conversation(db, conv: Conversation) -> None:
    """删除会话（级联删除其下所有 Message，由 relationship cascade 处理）"""
    db.delete(conv)
    db.flush()

# ========== Message 相关 ==========

def create_message(
        db,conversation_id:int,role:str,content: str
) -> Message:
    """创建一条消息"""
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        create_time=utcnow(),
    )
    db.add(msg)
    db.flush()
    return msg

def list_messages_by_conversation(
        db,conversation_id:int,limit:int = 100
) -> List[Message]:
    """查询某会话下的所有消息（按时间正序，用于聊天历史展示）"""
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.create_time.asc())
        .limit(limit)
        .all()
    )
def list_messages_for_history(
        db,conversation_id:int,limit:int = 20
) -> List[Message]:
    """查询最近N条消息作为 LLM 上下文历史（正序，用于喂给 ReAct 引擎）"""
    # 先倒序取 limit 条，再正序返回（保证取最近的消息且顺序正确）
    msgs=(
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.create_time.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(msgs))
