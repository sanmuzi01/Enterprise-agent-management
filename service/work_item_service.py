"""统一待办：提醒规则、AI 工作成果后续事项、手工待办都落在 work_item 表里。"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError

from models.init_db import WorkItem
from service.exceptions import InvalidInput, NotFound
from utils.timeutil import utcnow

PRIORITIES = ("low", "normal", "high")
STATUSES = ("open", "done", "dismissed")


def payload(item: WorkItem) -> Dict[str, Any]:
    return {
        "id": item.id, "team_id": item.team_id, "source_type": item.source_type, "rule": item.rule,
        "title": item.title, "detail": item.detail, "link": item.link, "priority": item.priority,
        "status": item.status, "resolved_by": item.resolved_by,
        "due_at": item.due_at.isoformat() + "Z" if item.due_at else None,
        "completed_at": item.completed_at.isoformat() + "Z" if item.completed_at else None,
        "created_at": item.created_at.isoformat() + "Z",
    }


async def list_items(db, user_id: int, status: str = "open", limit: int = 200) -> Dict[str, Any]:
    if status not in STATUSES + ("all",):
        raise InvalidInput("status 只能是 open/done/dismissed/all")
    query = select(WorkItem).where(WorkItem.user_id == user_id)
    if status != "all":
        query = query.where(WorkItem.status == status)
    priority_rank = case((WorkItem.priority == "high", 0), (WorkItem.priority == "normal", 1), else_=2)
    rows = (await db.execute(query.order_by(
        WorkItem.due_at.is_(None), WorkItem.due_at, priority_rank, WorkItem.id.desc()).limit(limit))).scalars().all()
    return {"items": [payload(r) for r in rows], "counts": await counts(db, user_id)}


async def counts(db, user_id: int) -> Dict[str, int]:
    now = utcnow()
    end_of_day = (now + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) - timedelta(hours=8)
    row = (await db.execute(select(
        func.count(),
        func.sum(case((WorkItem.due_at < now, 1), else_=0)),
        func.sum(case(((WorkItem.due_at >= now) & (WorkItem.due_at <= end_of_day), 1), else_=0)),
        func.sum(case((WorkItem.priority == "high", 1), else_=0)),
    ).where(WorkItem.user_id == user_id, WorkItem.status == "open"))).one()
    return {"open": int(row[0] or 0), "overdue": int(row[1] or 0), "due_today": int(row[2] or 0),
            "high": int(row[3] or 0)}


def parse_due(value: Optional[str]) -> Optional[datetime]:
    """只有日期时按北京时间当天 18:00 截止；带时区的时间换算成 UTC；存储一律为 UTC。"""
    if not value:
        return None
    try:
        if len(value) == 10:
            day = datetime.fromisoformat(value)
            return day.replace(hour=10)  # 18:00 北京时间
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise InvalidInput("截止时间格式不正确") from None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


async def create_manual(db, user_id: int, title: str, due_at: Optional[str] = None, priority: str = "normal",
                        detail: Optional[str] = None) -> Dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise InvalidInput("待办内容不能为空")
    if priority not in PRIORITIES:
        raise InvalidInput("优先级只能是 low/normal/high")
    import uuid
    item = WorkItem(user_id=user_id, source_type="manual", source_key=f"manual:{uuid.uuid4()}", title=title[:200],
                    detail=(detail or None) and detail[:500], priority=priority, status="open",
                    due_at=parse_due(due_at))
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return payload(item)


async def set_status(db, user_id: int, item_id: int, status: str) -> Dict[str, Any]:
    if status not in STATUSES:
        raise InvalidInput("status 只能是 open/done/dismissed")
    item = (await db.execute(select(WorkItem).where(WorkItem.id == item_id, WorkItem.user_id == user_id))).scalar_one_or_none()
    if item is None:
        raise NotFound("待办不存在")
    item.status = status
    item.resolved_by = None if status == "open" else "user"
    item.completed_at = None if status == "open" else utcnow()
    if item.source_type == "automation":
        await _sync_automation_task(db, item, status == "done")
    await db.commit()
    await db.refresh(item)
    return payload(item)


async def _sync_automation_task(db, item: WorkItem, done: bool) -> None:
    # source_key = automation:{work_id}:{index}；在待办中心勾选时同步到工作成果里的待办状态。
    from service import automation_work_service
    _, work_id, index = item.source_key.split(":")
    try:
        await automation_work_service.complete_task(db, item.user_id, work_id, int(index), done, sync_work_item=False)
    except Exception:  # noqa: BLE001 —— 成果已不存在或已变化时，以待办中心的状态为准
        await db.rollback()


async def upsert(db, user_id: int, source_type: str, source_key: str, *, title: str, rule: Optional[str] = None,
                 team_id: Optional[int] = None, detail: Optional[str] = None, link: Optional[str] = None,
                 priority: str = "normal", due_at: Optional[datetime] = None, reopen: bool = False) -> bool:
    """存在则刷新内容（不覆盖用户的完成/忽略状态，除非 reopen），不存在则创建。返回是否新建。"""
    existing = (await db.execute(select(WorkItem).where(
        WorkItem.user_id == user_id, WorkItem.source_key == source_key))).scalar_one_or_none()
    if existing is not None:
        existing.title, existing.detail, existing.link = title[:200], detail and detail[:500], link
        existing.priority, existing.due_at, existing.team_id = priority, due_at, team_id
        if reopen and existing.status != "open" and existing.resolved_by == "rule":
            existing.status, existing.resolved_by, existing.completed_at = "open", None, None
        await db.commit()
        return False
    db.add(WorkItem(user_id=user_id, team_id=team_id, source_type=source_type, source_key=source_key, rule=rule,
                    title=title[:200], detail=detail and detail[:500], link=link, priority=priority,
                    status="open", due_at=due_at))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return False
    return True


async def resolve_missing(db, rule: str, current_keys: Iterable[tuple]) -> int:
    """规则这次没有再产出的开放待办 = 条件已不成立（已审批、已跟进、已补发票），自动关闭。"""
    current = set(current_keys)
    rows = (await db.execute(select(WorkItem.id, WorkItem.user_id, WorkItem.source_key).where(
        WorkItem.rule == rule, WorkItem.status == "open"))).all()
    stale = [row.id for row in rows if (row.user_id, row.source_key) not in current]
    if stale:
        await db.execute(update(WorkItem).where(WorkItem.id.in_(stale)).values(
            status="done", resolved_by="rule", completed_at=utcnow()))
        await db.commit()
    return len(stale)
