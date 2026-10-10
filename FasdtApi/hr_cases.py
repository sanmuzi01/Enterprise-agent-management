"""人事入转调离接口。team_id 是调用者当前所在部门（工作台当前部门），用来确定企业范围；
身份与权限由 service/hr_service.py 计算，不是路由级门槛（见 tests/test_hr_service.py）。"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from models.async_db import get_async_db
from models.init_db import User
from service import hr_service as hr
from service.dependencies import get_current_user_async

router = APIRouter(prefix="/enterprise/hr", tags=["人事入转调离"])


class CaseBody(BaseModel):
    team_id: int = Field(description="调用者当前所在部门")
    case_type: str = Field(pattern="^(ONBOARDING|PROBATION|TRANSFER|OFFBOARDING)$")
    employee_user_id: int
    employee_team_id: int = Field(description="员工当前所在部门")
    target_team_id: Optional[int] = None
    position: Optional[str] = Field(default=None, max_length=80)
    effective_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    reason: Optional[str] = Field(default=None, max_length=500)

    def payload(self):
        return {"case_type": self.case_type, "employee_user_id": self.employee_user_id, "team_id": self.employee_team_id,
                "target_team_id": self.target_team_id, "position": self.position, "effective_date": self.effective_date,
                "reason": self.reason}


class ActionBody(BaseModel):
    team_id: int
    note: Optional[str] = Field(default=None, max_length=500)


@router.get("/me")
async def me(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await hr.actor_summary_async(db, user.id, team_id)


@router.get("/cases")
async def list_cases(team_id: int, view: str = "hr", status: Optional[str] = None, type: Optional[str] = None,
                     db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await hr.list_cases_async(db, user.id, team_id, view, status, type)


@router.get("/cases/my-tasks")
async def my_tasks(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await hr.my_tasks_async(db, user.id, team_id)


@router.get("/cases/summary")
async def summary(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await hr.summary_async(db, user.id, team_id)


@router.get("/candidates")
async def candidates(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await hr.candidates_async(db, user.id, team_id)


@router.get("/cases/{case_id}")
async def get_case(case_id: int, team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await hr.get_case_async(db, user.id, team_id, case_id)


@router.post("/cases/precheck")
async def precheck(body: CaseBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await hr.precheck_async(db, user.id, body.team_id, body.payload())


@router.post("/cases")
async def create_case(body: CaseBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await hr.create_case_async(db, user.id, body.team_id, body.payload())


@router.post("/cases/{case_id}/tasks/{task_id}/{result}")
async def finish_task(case_id: int, task_id: int, result: str, body: ActionBody, db=Depends(get_async_db),
                      user: User = Depends(get_current_user_async)):
    return await hr.finish_task_async(db, user.id, body.team_id, case_id, task_id, result, body.note)


@router.post("/cases/{case_id}/apply-effect")
async def apply_effect(case_id: int, body: ActionBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await hr.apply_effect_async(db, user.id, body.team_id, case_id)


@router.post("/cases/{case_id}/{action}")
async def act(case_id: int, action: str, body: ActionBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await hr.act_async(db, user.id, body.team_id, case_id, action, body.note)
