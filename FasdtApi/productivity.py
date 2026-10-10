"""提效仪表盘。

  GET  /admin/productivity                 全企业（平台管理员）
  GET  /department/productivity?team_id=   本部门（部门负责人 / 企业管理员）
  POST /productivity/feedback              员工反馈“这次大概省了多少分钟”（单独记，不和估算值相加）
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from models.async_db import get_async_db
from models.init_db import User
from service import productivity_service
from service.dependencies import get_current_admin_user_async, get_current_user_async
from service.exceptions import InvalidInput

router = APIRouter(tags=["提效统计"])


def _days(days: int) -> int:
    if not 1 <= days <= 365:
        raise InvalidInput("统计天数为 1～365")
    return days


@router.get("/admin/productivity", summary="全企业提效仪表盘")
async def admin_productivity(days: int = 30, db=Depends(get_async_db), _: User = Depends(get_current_admin_user_async)):
    return await productivity_service.dashboard(db, None, _days(days))


@router.get("/department/productivity", summary="部门提效仪表盘")
async def department_productivity(team_id: int, days: int = 30, db=Depends(get_async_db),
                                  user: User = Depends(get_current_user_async)):
    return await productivity_service.department_dashboard(db, user.id, team_id, _days(days))


class FeedbackBody(BaseModel):
    source_key: str = Field(min_length=3, max_length=120)
    minutes: float = Field(ge=0, le=600)


@router.post("/productivity/feedback", summary="反馈这次节省了多少时间")
async def feedback(body: FeedbackBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await productivity_service.report_saved(db, user.id, body.source_key, body.minutes)
