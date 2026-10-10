"""部门责任执行接口。team_id 是调用者当前所在部门（工作台当前部门），用来确定企业范围；
身份与权限由 service/responsibility_service.py 计算并交给 Java 业务服务强制执行，不是路由级门槛。"""
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from models.async_db import get_async_db
from models.init_db import User
from service import responsibility_service as rs
from service.dependencies import get_current_user_async

router = APIRouter(prefix="/enterprise/responsibility", tags=["部门责任执行"])

DATE = r"^\d{4}-\d{2}-\d{2}$"


class TaskBody(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    responsible_user_id: Optional[int] = None
    collaborator_user_ids: List[int] = Field(default_factory=list, max_length=20)
    reviewer_user_id: Optional[int] = None
    due_date: Optional[str] = Field(default=None, pattern=DATE)
    deliverable: Optional[str] = Field(default=None, max_length=300)
    acceptance_criteria: Optional[str] = Field(default=None, max_length=500)
    priority: str = Field(default="NORMAL", pattern="^(LOW|NORMAL|HIGH|URGENT)$")
    evidence: Optional[str] = Field(default=None, max_length=500)
    depends_on_seq: List[int] = Field(default_factory=list, max_length=20)

    def dump(self):
        return self.model_dump()


class PlanBody(BaseModel):
    team_id: int
    title: str = Field(min_length=1, max_length=160)
    source_type: str = Field(default="OTHER", pattern="^(MEETING|CHAT|EMAIL|NOTICE|OTHER)$")
    source_text: Optional[str] = Field(default=None, max_length=100000)
    summary: Optional[str] = Field(default=None, max_length=1000)
    unresolved: List[str] = Field(default_factory=list, max_length=50)
    tasks: List[TaskBody] = Field(min_length=1, max_length=30)


class EditPlanBody(BaseModel):
    team_id: int
    title: Optional[str] = Field(default=None, max_length=160)
    summary: Optional[str] = Field(default=None, max_length=1000)
    unresolved: List[str] = Field(default_factory=list, max_length=50)


class TaskEditBody(BaseModel):
    team_id: int
    task: TaskBody


class TeamBody(BaseModel):
    team_id: int
    note: Optional[str] = Field(default=None, max_length=500)
    reason: Optional[str] = Field(default=None, max_length=500)


class ActionBody(BaseModel):
    team_id: int
    note: Optional[str] = Field(default=None, max_length=500)
    reason: Optional[str] = Field(default=None, max_length=500)
    percent: Optional[int] = Field(default=None, ge=0, le=100)
    proposed_date: Optional[str] = Field(default=None, pattern=DATE)
    approve: Optional[bool] = None
    waiting_on_user_id: Optional[int] = None
    summary: Optional[str] = Field(default=None, max_length=1000)
    link: Optional[str] = Field(default=None, max_length=500)
    responsible_user_id: Optional[int] = None
    reviewer_user_id: Optional[int] = None
    due_date: Optional[str] = Field(default=None, pattern=DATE)
    deliverable: Optional[str] = Field(default=None, max_length=300)
    acceptance_criteria: Optional[str] = Field(default=None, max_length=500)
    collaborator_user_ids: Optional[List[int]] = Field(default=None, max_length=20)
    priority: Optional[str] = Field(default=None, pattern="^(LOW|NORMAL|HIGH|URGENT)$")


@router.get("/candidates")
async def candidates(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.candidates_async(db, user.id, team_id)


@router.get("/mine")
async def mine(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.mine_async(db, user.id, team_id)


@router.get("/summary")
async def summary(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.summary_async(db, user.id, team_id)


@router.get("/tasks")
async def tasks(team_id: int, view: str = "mine", status: Optional[str] = None, db=Depends(get_async_db),
                user: User = Depends(get_current_user_async)):
    return await rs.list_tasks_async(db, user.id, team_id, view, status)


@router.get("/tasks/{task_id}")
async def task_detail(task_id: int, team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.get_task_async(db, user.id, team_id, task_id)


@router.post("/tasks/{task_id}/edit")
async def edit_task(task_id: int, body: TaskEditBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.edit_task_async(db, user.id, body.team_id, task_id, body.task.dump())


@router.post("/tasks/{task_id}/remove")
async def remove_task(task_id: int, body: TeamBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.remove_task_async(db, user.id, body.team_id, task_id)


@router.post("/tasks/{task_id}/{action}")
async def act(task_id: int, action: str, body: ActionBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.act_async(db, user.id, body.team_id, task_id, action, body.model_dump())


@router.get("/plans")
async def plans(team_id: int, status: Optional[str] = None, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.list_plans_async(db, user.id, team_id, status)


@router.post("/plans")
async def create_plan(body: PlanBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    data = body.model_dump()
    return await rs.create_plan_async(db, user.id, body.team_id, data)


@router.get("/plans/{plan_id}")
async def plan_detail(plan_id: int, team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.get_plan_async(db, user.id, team_id, plan_id)


@router.post("/plans/{plan_id}/edit")
async def edit_plan(plan_id: int, body: EditPlanBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.edit_plan_async(db, user.id, body.team_id, plan_id, body.model_dump())


@router.post("/plans/{plan_id}/tasks")
async def add_task(plan_id: int, body: TaskEditBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.add_task_async(db, user.id, body.team_id, plan_id, body.task.dump())


@router.post("/plans/{plan_id}/publish")
async def publish(plan_id: int, body: TeamBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.publish_async(db, user.id, body.team_id, plan_id, body.note)


@router.post("/plans/{plan_id}/cancel")
async def cancel_plan(plan_id: int, body: TeamBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await rs.cancel_plan_async(db, user.id, body.team_id, plan_id, body.reason or body.note)
