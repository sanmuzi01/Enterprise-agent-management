"""考勤异常发现接口。team_id 是调用者当前所在部门（用来确定企业范围）；
导入/规则/日历/分析只有人事；员工看自己的；部门负责人看本部门；认定不能认定自己的——权限在 service/attendance_service.py 里计算。"""
import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from models.async_db import get_async_db
from models.init_db import User
from service import attendance_service as svc
from service.dependencies import get_current_user_async
from service.exceptions import InvalidInput
from pydantic import BaseModel, Field

router = APIRouter(prefix="/enterprise/attendance", tags=["考勤异常"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
DAY = r"^\d{4}-\d{2}-\d{2}$"


class RuleBody(BaseModel):
    team_id: int
    for_team_id: Optional[int] = None          # 空 = 企业默认
    work_start: str = Field(pattern=r"^\d{1,2}:\d{2}$")
    work_end: str = Field(pattern=r"^\d{1,2}:\d{2}$")
    grace_minutes: int = Field(ge=0, le=60)


class CalendarBody(BaseModel):
    team_id: int
    text: str = Field(min_length=1, max_length=40000)


class AnalyzeBody(BaseModel):
    team_id: int
    start: str = Field(pattern=DAY)
    end: str = Field(pattern=DAY)


class ExplainBody(BaseModel):
    team_id: int
    text: str = Field(min_length=1, max_length=500)


class DecideBody(BaseModel):
    team_id: int
    action: str = Field(pattern="^(confirm|dismiss)$")
    note: str = Field(min_length=1, max_length=500)


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvalidInput("日期无效") from None


@router.get("/me")
async def me(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.me(db, user.id, team_id)


@router.post("/import")
async def import_file(team_id: int = Form(gt=0), aliases: str = Form("{}"), file: UploadFile = File(...), db=Depends(get_async_db),
                      user: User = Depends(get_current_user_async)):
    """上传打卡机/钉钉/企业微信导出的 Excel 或 CSV。aliases 是 JSON：{"考勤里的名字": 平台账号编号}，用来补齐对不上的人。"""
    try:
        mapping = json.loads(aliases or "{}")
        if not isinstance(mapping, dict) or len(mapping) > 500:
            raise ValueError
        mapping = {str(k): int(v) for k, v in mapping.items()}
    except (ValueError, TypeError):
        raise InvalidInput("名字对应格式不对") from None
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise InvalidInput("文件超过 15MB，请按月份拆开导出")
    return await svc.import_file(db, user.id, team_id, file.filename or "", content, mapping)


@router.get("/imports")
async def imports(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.list_imports(db, user.id, team_id)


@router.get("/members")
async def members(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.members_for_mapping(db, user.id, team_id)


@router.get("/rules")
async def rules(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.get_rules(db, user.id, team_id)


@router.post("/rules")
async def set_rule(body: RuleBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.set_rule(db, user.id, body.team_id, body.for_team_id, body.work_start, body.work_end, body.grace_minutes)


@router.get("/calendar")
async def calendar(team_id: int, start: str = Query(pattern=DAY), end: str = Query(pattern=DAY), db=Depends(get_async_db),
                   user: User = Depends(get_current_user_async)):
    return await svc.get_calendar(db, user.id, team_id, _day(start), _day(end))


@router.post("/calendar")
async def import_calendar(body: CalendarBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.import_calendar(db, user.id, body.team_id, body.text)


@router.post("/analyze")
async def analyze(body: AnalyzeBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.analyze(db, user.id, body.team_id, _day(body.start), _day(body.end))


@router.get("/anomalies")
async def anomalies(team_id: int, view: str = "mine", status: Optional[str] = None, start: Optional[str] = Query(default=None, pattern=DAY),
                    end: Optional[str] = Query(default=None, pattern=DAY), db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.list_anomalies(db, user.id, team_id, view, status, _day(start) if start else None, _day(end) if end else None)


@router.get("/summary")
async def summary(team_id: int, start: str = Query(pattern=DAY), end: str = Query(pattern=DAY), db=Depends(get_async_db),
                  user: User = Depends(get_current_user_async)):
    return await svc.summary(db, user.id, team_id, _day(start), _day(end))


@router.post("/anomalies/{anomaly_id}/explain")
async def explain(anomaly_id: int, body: ExplainBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.explain(db, user.id, body.team_id, anomaly_id, body.text)


@router.post("/anomalies/{anomaly_id}/decide")
async def decide(anomaly_id: int, body: DecideBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.decide(db, user.id, body.team_id, anomaly_id, body.action, body.note)
