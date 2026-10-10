"""入站事件去重（external_event_inbox）：(provider, tenant_id, event_id) 唯一。

飞书、钉钉在没收到及时响应时会重推同一个事件；不去重的话，同一句“帮我报销 300 元”会生成两张单子。
先 INSERT 占位，撞唯一约束就说明处理过（或正在处理），直接确认收到、不再执行。
"""
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from models.init_db import ExternalEventInbox
from utils.timeutil import utcnow


def claim_sync(db, provider: str, tenant_id: str, event_id: str, event_type: str, trace_id: Optional[str] = None) -> Optional[int]:
    """第一次收到：建一条 received 记录并返回它的 id；重复的：返回 None。"""
    row = ExternalEventInbox(provider=provider, tenant_id=tenant_id or "-", event_id=event_id[:120], event_type=event_type[:80],
                             status="received", trace_id=trace_id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return row.id


def finish_sync(db, inbox_id: int, status: str, error: Optional[str] = None) -> None:
    db.execute(update(ExternalEventInbox).where(ExternalEventInbox.id == inbox_id)
               .values(status=status, processed_at=utcnow(), last_error=(error or None) and error[:500]))
    db.commit()
