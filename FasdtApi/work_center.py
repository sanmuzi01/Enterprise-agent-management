"""统一待办、站内通知、通知偏好，以及管理员查看/手动触发提醒规则。"""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from models.async_db import get_async_db
from models.init_db import User
from service import notification_center, reminders, work_item_service
from service.dependencies import get_current_admin_user_async, get_current_user_async

router = APIRouter(tags=["待办与通知"])


class WorkItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    detail: Optional[str] = Field(default=None, max_length=500)
    due_at: Optional[str] = Field(default=None, description="YYYY-MM-DD（北京时间当天 18:00 截止）或 ISO 时间")
    priority: Literal["low", "normal", "high"] = "normal"


class WorkItemUpdate(BaseModel):
    status: Literal["open", "done", "dismissed"]


class MarkRead(BaseModel):
    ids: Optional[List[int]] = Field(default=None, description="不传表示全部标为已读")


class PreferenceUpdate(BaseModel):
    muted_categories: List[str] = Field(default_factory=list)
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None
    push_external: bool = False


@router.get("/work-items")
async def list_work_items(status: str = Query(default="open"), db=Depends(get_async_db),
                          user: User = Depends(get_current_user_async)):
    return await work_item_service.list_items(db, user.id, status)


@router.get("/work-items/counts")
async def work_item_counts(db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await work_item_service.counts(db, user.id)


@router.post("/work-items")
async def create_work_item(data: WorkItemCreate, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await work_item_service.create_manual(db, user.id, data.title, data.due_at, data.priority, data.detail)


@router.patch("/work-items/{item_id}")
async def update_work_item(item_id: int, data: WorkItemUpdate, db=Depends(get_async_db),
                           user: User = Depends(get_current_user_async)):
    return await work_item_service.set_status(db, user.id, item_id, data.status)


@router.get("/notifications")
async def list_notifications(unread_only: bool = False, db=Depends(get_async_db),
                             user: User = Depends(get_current_user_async)):
    return await notification_center.list_notifications(db, user.id, unread_only)


@router.get("/notifications/unread-count")
async def unread_count(db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return {"unread": await notification_center.unread_count(db, user.id)}


@router.post("/notifications/read")
async def mark_read(data: MarkRead, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await notification_center.mark_read(db, user.id, data.ids)


@router.get("/notification-preferences")
async def get_preferences(db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await notification_center.get_preference(db, user.id)


@router.put("/notification-preferences")
async def update_preferences(data: PreferenceUpdate, db=Depends(get_async_db),
                             user: User = Depends(get_current_user_async)):
    return await notification_center.update_preference(
        db, user.id, data.muted_categories, data.quiet_start, data.quiet_end, data.push_external)


@router.get("/admin/reminders", summary="提醒规则运行状态")
async def reminder_status(db=Depends(get_async_db), user: User = Depends(get_current_admin_user_async)):
    return await reminders.status(db)


@router.post("/admin/reminders/{rule}/run", summary="立即运行一条提醒规则")
async def run_reminder(rule: str, db=Depends(get_async_db), user: User = Depends(get_current_admin_user_async)):
    return await reminders.run_now(db, rule)
