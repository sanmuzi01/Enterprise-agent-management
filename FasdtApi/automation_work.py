from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
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


@router.patch("/{work_id}/tasks/{index}")
async def task(work_id: UUID, index: int, data: TaskRequest, db=Depends(get_async_db),
               user: User = Depends(get_current_user_async)):
    return await svc.complete_task(db, user.id, str(work_id), index, data.done)
