"""站内通知中心：去重、按类别静音、免打扰时段（只影响外部推送）、可选推送到用户已配置的 Webhook 通道。"""
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from models.init_db import Notification, NotificationPreference
from service.exceptions import InvalidInput
from utils.logger_handler import get_logger
from utils.timeutil import utcnow

logger = get_logger("notification_center")

CATEGORIES = {
    "approval": "审批等待提醒",
    "crm_followup": "客户跟进提醒",
    "crm_risk": "商机风险提醒",
    "expense_invoice": "报销缺发票提醒",
    "voucher_pending": "记账凭证待核对提醒",
    "it_ticket": "IT 工单超时提醒",
    "ticket_followup": "工单待你处理提醒",
    "hr_task": "人事办理任务提醒",
    "responsibility": "责任协同提醒",
    "attendance": "考勤异常提醒",
    "task_due": "待办到期提醒",
    "digest": "工作摘要",
    "system": "系统告警",
}
BEIJING = timedelta(hours=8)


def payload(n: Notification) -> Dict[str, Any]:
    return {"id": n.id, "category": n.category, "category_label": CATEGORIES.get(n.category, n.category),
            "title": n.title, "body": n.body, "link": n.link, "read": n.read_at is not None,
            "created_at": n.created_at.isoformat() + "Z"}


async def get_preference(db, user_id: int) -> Dict[str, Any]:
    pref = await db.get(NotificationPreference, user_id)
    return {
        "muted_categories": json.loads(pref.muted_categories) if pref else [],
        "quiet_start": pref.quiet_start if pref else None,
        "quiet_end": pref.quiet_end if pref else None,
        "push_external": bool(pref.push_external) if pref else False,
        "categories": [{"value": k, "label": v} for k, v in CATEGORIES.items() if k != "system"],
    }


def _valid_hhmm(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError:
        raise InvalidInput("免打扰时间格式应为 HH:MM") from None
    return value


async def update_preference(db, user_id: int, muted_categories: List[str], quiet_start: Optional[str],
                            quiet_end: Optional[str], push_external: bool) -> Dict[str, Any]:
    unknown = [c for c in muted_categories if c not in CATEGORIES or c == "system"]
    if unknown:
        raise InvalidInput(f"未知或不可静音的通知类别: {', '.join(unknown)}")
    start, end = _valid_hhmm(quiet_start), _valid_hhmm(quiet_end)
    if bool(start) != bool(end):
        raise InvalidInput("免打扰需要同时设置开始和结束时间")
    pref = await db.get(NotificationPreference, user_id)
    if pref is None:
        pref = NotificationPreference(user_id=user_id)
        db.add(pref)
    pref.muted_categories = json.dumps(sorted(set(muted_categories)))
    pref.quiet_start, pref.quiet_end, pref.push_external = start, end, int(bool(push_external))
    await db.commit()
    return await get_preference(db, user_id)


def in_quiet_hours(start: Optional[str], end: Optional[str], now_utc: datetime) -> bool:
    if not (start and end):
        return False
    local = (now_utc + BEIJING).strftime("%H:%M")
    return start <= local < end if start < end else (local >= start or local < end)


async def notify(db, user_id: int, category: str, title: str, *, dedupe_key: str, body: Optional[str] = None,
                 link: Optional[str] = None) -> bool:
    """同一 dedupe_key 只通知一次；类别被静音时不通知。返回是否新建了通知。"""
    pref = await db.get(NotificationPreference, user_id)
    if pref and category != "system" and category in json.loads(pref.muted_categories or "[]"):
        return False
    db.add(Notification(user_id=user_id, category=category, title=title[:200], body=body and body[:500],
                        link=link, dedupe_key=dedupe_key[:160]))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return False
    if pref and pref.push_external and not in_quiet_hours(pref.quiet_start, pref.quiet_end, utcnow()):
        try:
            from service.notification_service import dispatch_alert_async
            await dispatch_alert_async(db, user_id, title=title, message=body or title)
        except Exception:  # noqa: BLE001 —— 外部推送失败不影响站内通知
            logger.warning(f"外部推送失败 user_id={user_id}", exc_info=True)
    return True


async def list_notifications(db, user_id: int, unread_only: bool = False, limit: int = 50) -> Dict[str, Any]:
    query = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    rows = (await db.execute(query.order_by(Notification.created_at.desc(), Notification.id.desc())
                             .limit(min(limit, 200)))).scalars().all()
    return {"items": [payload(n) for n in rows], "unread": await unread_count(db, user_id)}


async def unread_count(db, user_id: int) -> int:
    return int((await db.execute(select(func.count()).where(
        Notification.user_id == user_id, Notification.read_at.is_(None)))).scalar() or 0)


async def mark_read(db, user_id: int, ids: Optional[List[int]] = None) -> Dict[str, int]:
    stmt = update(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    if ids is not None:
        stmt = stmt.where(Notification.id.in_(ids or [-1]))
    await db.execute(stmt.values(read_at=utcnow()))
    await db.commit()
    return {"unread": await unread_count(db, user_id)}
