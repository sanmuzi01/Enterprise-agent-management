from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field, ConfigDict

from models.async_db import get_async_db
from models.init_db import User
from service.dependencies import get_current_user_async
from service import automation_work_service as svc
from service.exceptions import RateLimited
from utils.rate_limit import require_limit, concurrency_guard, LimitExceeded

router = APIRouter(prefix="/enterprise/automation", tags=["AI 工作成果"])


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_key: UUID
    team_id: int = Field(gt=0)
    kind: str = Field(min_length=1, max_length=30, description="工作流 ID，见 GET /enterprise/automation/workflows")
    model_name: str = Field(min_length=1, max_length=100)
    source_text: str = Field(min_length=10, max_length=15000)
    customer_id: int | None = Field(default=None, gt=0)
    sensitivity: Literal["internal", "confidential", "restricted"] = "internal"


class BatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="", max_length=255)
    text: str = Field(min_length=1, max_length=15000)


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    batch_id: UUID
    team_id: int = Field(gt=0)
    kind: str = Field(min_length=1, max_length=30)
    model_name: str = Field(min_length=1, max_length=100)
    sensitivity: Literal["internal", "confidential", "restricted"] = "internal"
    items: list[BatchItem] = Field(min_length=1, max_length=10)


class TimeSampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    team_id: int = Field(gt=0)
    kind: str = Field(min_length=1, max_length=30)
    minutes: float = Field(ge=0.5, le=600)
    note: str = Field(default="", max_length=200)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=300)
    manual_minutes: float | None = Field(default=None, ge=0.5, le=600)


class ApplyRequest(BaseModel):
    proposal: dict


class TaskRequest(BaseModel):
    done: bool


@router.get("")
async def history(team_id: int, offset: int = Query(default=0, ge=0, le=100000),
                  db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.history(db, user.id, team_id, offset)


@router.get("/workflows")
async def workflows(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.workflows_for_team(db, user.id, team_id)


@router.post("/import")
async def import_file(team_id: int = Form(gt=0), sensitivity: Literal["internal", "confidential", "restricted"] = Form("internal"),
                      file: UploadFile = File(...), db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    """把 PDF / Word / Excel / 邮件 / 图片 / 文本文件提取成文字（不保存文件），由用户核对后再整理。"""
    from service import document_intake
    try:
        require_limit(f"automation_import:{user.id}", "AUTOMATION_IMPORT_RATE_LIMIT", 20,
                      "AUTOMATION_RATE_WINDOW_SECONDS", 60, "文件导入")
    except LimitExceeded:
        raise RateLimited("文件导入过于频繁，请稍后重试") from None
    content = await file.read(document_intake.MAX_BYTES + 1)
    return await document_intake.extract_text(db, user.id, team_id, file.filename or "", content, sensitivity)


@router.post("/time-samples")
async def add_time_sample(data: TimeSampleRequest, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    """记录“这类工作手工做一次实际花了多少分钟”（试点计时），用来算真实的节省工时。"""
    from service import pilot_service
    return await pilot_service.add_time_sample(db, user.id, data.team_id, data.kind, data.minutes, data.note)


@router.get("/time-samples/mine")
async def my_time_samples(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    from service import pilot_service
    return await pilot_service.my_time_samples(db, user.id, team_id)


@router.post("/batches")
async def create_batch(data: BatchRequest, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    """批量整理：立即返回批次（材料排队中），整理由事件消费者在后台进行；用 GET /batches/{id} 看进度。"""
    from service import automation_batch_service as batches
    try:
        require_limit(f"automation_batch:{user.id}", "AUTOMATION_BATCH_RATE_LIMIT", 6,
                      "AUTOMATION_RATE_WINDOW_SECONDS", 60, "批量整理")
    except LimitExceeded:
        raise RateLimited("批量整理请求过于频繁，请稍后重试") from None
    return await batches.create_batch(db, user.id, data)


@router.get("/batches")
async def list_batches(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    from service import automation_batch_service as batches
    return await batches.list_batches(db, user.id, team_id)


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: UUID, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    from service import automation_batch_service as batches
    return await batches.get_batch(db, user.id, str(batch_id))


@router.post("/batches/{batch_id}/items/{work_id}/retry")
async def retry_batch_item(batch_id: UUID, work_id: UUID, db=Depends(get_async_db),
                           user: User = Depends(get_current_user_async)):
    from service import automation_batch_service as batches
    await batches.retry_item(db, user.id, str(batch_id), str(work_id))
    return await batches.get_batch(db, user.id, str(batch_id))


@router.post("")
async def generate(data: GenerateRequest, db=Depends(get_async_db),
                   user: User = Depends(get_current_user_async)):
    try:
        require_limit(f"automation:{user.id}", "AUTOMATION_RATE_LIMIT", 6,
                      "AUTOMATION_RATE_WINDOW_SECONDS", 60, "材料整理")
        with concurrency_guard(f"agent_run:user:{user.id}", "USER_MAX_CONCURRENT_AGENT_RUNS", 2,
                               "AGENT_RUN_CONCURRENCY_TTL_SECONDS", 300, "材料整理"):
            return await svc.generate(db, user.id, data)
    except LimitExceeded:
        raise RateLimited("材料整理请求过于频繁，请稍后重试") from None


@router.get("/{work_id}")
async def detail(work_id: UUID, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return svc.payload(await svc.get_work(db, user.id, str(work_id)))


@router.post("/{work_id}/apply")
async def apply(work_id: UUID, data: ApplyRequest, db=Depends(get_async_db),
                user: User = Depends(get_current_user_async)):
    return await svc.apply_work(db, user.id, str(work_id), data.proposal)


@router.post("/{work_id}/checks")
async def recheck(work_id: UUID, data: ApplyRequest, db=Depends(get_async_db),
                  user: User = Depends(get_current_user_async)):
    return await svc.recheck(db, user.id, str(work_id), data.proposal)


@router.patch("/{work_id}/tasks/{index}")
async def task(work_id: UUID, index: int, data: TaskRequest, db=Depends(get_async_db),
               user: User = Depends(get_current_user_async)):
    return await svc.complete_task(db, user.id, str(work_id), index, data.done)


@router.get("/{work_id}/feedback")
async def get_feedback(work_id: UUID, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    from service import pilot_service
    await svc.get_work(db, user.id, str(work_id))
    return await pilot_service.feedback_for(db, user.id, str(work_id))


@router.put("/{work_id}/feedback")
async def put_feedback(work_id: UUID, data: FeedbackRequest, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    from service import pilot_service
    return await pilot_service.give_feedback(db, user.id, str(work_id), data.rating, data.comment, data.manual_minutes)
