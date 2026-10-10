"""跨部门协同办理：一段话拆成多个部门步骤，逐步交给 AI 整理并人工核对。见 service/orchestration_service.py。"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from models.async_db import get_async_db
from models.init_db import User
from service import orchestration_service as svc
from service.dependencies import get_current_user_async
from service.exceptions import RateLimited
from utils.rate_limit import LimitExceeded, concurrency_guard, require_limit

router = APIRouter(prefix="/enterprise/orchestration", tags=["跨部门协同办理"])


class PlanBody(BaseModel):
    team_id: int = Field(gt=0)
    text: str = Field(min_length=6, max_length=5000)


class StepBody(BaseModel):
    kind: Optional[str] = Field(default=None, max_length=30)
    clause: Optional[str] = Field(default=None, max_length=2000)
    skip: Optional[bool] = None


class StartBody(BaseModel):
    model_name: str = Field(min_length=1, max_length=100)
    customer_id: Optional[int] = Field(default=None, gt=0)


@router.post("/plans")
async def create_plan(body: PlanBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.create_plan(db, user.id, body.team_id, body.text)


@router.get("/plans")
async def list_plans(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.list_plans(db, user.id, team_id)


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await svc.get_plan(db, user.id, plan_id)


@router.patch("/plans/{plan_id}/steps/{step_id}")
async def update_step(plan_id: int, step_id: int, body: StepBody, db=Depends(get_async_db),
                      user: User = Depends(get_current_user_async)):
    return await svc.update_step(db, user.id, plan_id, step_id, body.kind, body.clause, body.skip)


@router.post("/plans/{plan_id}/steps/{step_id}/start")
async def start_step(plan_id: int, step_id: int, body: StartBody, db=Depends(get_async_db),
                     user: User = Depends(get_current_user_async)):
    try:
        require_limit(f"automation:{user.id}", "AUTOMATION_RATE_LIMIT", 6, "AUTOMATION_RATE_WINDOW_SECONDS", 60, "材料整理")
        with concurrency_guard(f"agent_run:user:{user.id}", "USER_MAX_CONCURRENT_AGENT_RUNS", 2,
                               "AGENT_RUN_CONCURRENCY_TTL_SECONDS", 300, "材料整理"):
            return await svc.start_step(db, user.id, plan_id, step_id, body.model_name, body.customer_id)
    except LimitExceeded:
        raise RateLimited("材料整理请求过于频繁，请稍后重试") from None
