"""财务部门的记账凭证接口：报销单批准后自动生成凭证草稿，财务人员核对后确认入账。

team_id 是调用者所在的财务部门（工作台当前部门）；权限、可见范围都由 service 层判断，
不是路由级门槛——非财务部门的人即使直接请求也会被拒绝（见 tests/test_finance_vouchers.py）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from models.async_db import get_async_db
from models.init_db import User
from service import finance_voucher_service as vouchers
from service.dependencies import get_current_user_async

router = APIRouter(prefix="/enterprise/finance/vouchers", tags=["财务记账"])


class EntrySubjectBody(BaseModel):
    team_id: int
    subject_code: str = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=2, max_length=200)


class VoucherDateBody(BaseModel):
    team_id: int
    voucher_date: str = Field(min_length=8, max_length=10)


class ConfirmBody(BaseModel):
    team_id: int
    note: Optional[str] = Field(default=None, max_length=500)
    acknowledge_warnings: bool = False


class VoidBody(BaseModel):
    team_id: int
    reason: str = Field(min_length=2, max_length=300)


class TeamBody(BaseModel):
    team_id: int


@router.get("")
async def list_vouchers(team_id: int, status: Optional[str] = None, period: Optional[str] = None,
                        limit: int = Query(default=100, ge=1, le=200),
                        db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await vouchers.list_vouchers_async(db, user.id, team_id, status, period, limit)


@router.get("/summary")
async def monthly_summary(team_id: int, period: Optional[str] = None,
                          db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await vouchers.monthly_summary_async(db, user.id, team_id, period)


@router.get("/unbooked")
async def unbooked_claims(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await vouchers.list_unbooked_async(db, user.id, team_id)


@router.get("/subjects")
async def subjects(team_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await vouchers.list_subjects_async(db, user.id, team_id)


@router.get("/by-claim/{claim_id}")
async def voucher_by_claim(claim_id: int, team_id: int, db=Depends(get_async_db),
                           user: User = Depends(get_current_user_async)):
    return await vouchers.voucher_by_claim_async(db, user.id, team_id, claim_id)


@router.get("/{voucher_id}")
async def get_voucher(voucher_id: int, team_id: int, db=Depends(get_async_db),
                      user: User = Depends(get_current_user_async)):
    return await vouchers.get_voucher_async(db, user.id, team_id, voucher_id)


@router.post("/from-claim/{claim_id}")
async def generate_from_claim(claim_id: int, body: TeamBody, db=Depends(get_async_db),
                              user: User = Depends(get_current_user_async)):
    return await vouchers.generate_from_claim_async(db, user.id, body.team_id, claim_id)


@router.post("/{voucher_id}/entries/{entry_id}/subject")
async def update_entry_subject(voucher_id: int, entry_id: int, body: EntrySubjectBody, db=Depends(get_async_db),
                               user: User = Depends(get_current_user_async)):
    return await vouchers.update_entry_subject_async(db, user.id, body.team_id, voucher_id, entry_id,
                                                     body.subject_code, body.reason)


@router.post("/{voucher_id}/date")
async def update_voucher_date(voucher_id: int, body: VoucherDateBody, db=Depends(get_async_db),
                              user: User = Depends(get_current_user_async)):
    return await vouchers.update_voucher_date_async(db, user.id, body.team_id, voucher_id, body.voucher_date)


@router.post("/{voucher_id}/recheck")
async def recheck(voucher_id: int, body: TeamBody, db=Depends(get_async_db),
                  user: User = Depends(get_current_user_async)):
    return await vouchers.recheck_async(db, user.id, body.team_id, voucher_id)


@router.post("/{voucher_id}/regenerate")
async def regenerate(voucher_id: int, body: TeamBody, db=Depends(get_async_db),
                     user: User = Depends(get_current_user_async)):
    return await vouchers.regenerate_async(db, user.id, body.team_id, voucher_id)


@router.post("/{voucher_id}/confirm")
async def confirm(voucher_id: int, body: ConfirmBody, db=Depends(get_async_db),
                  user: User = Depends(get_current_user_async)):
    return await vouchers.confirm_async(db, user.id, body.team_id, voucher_id, body.note, body.acknowledge_warnings)


@router.post("/{voucher_id}/void")
async def void(voucher_id: int, body: VoidBody, db=Depends(get_async_db),
               user: User = Depends(get_current_user_async)):
    return await vouchers.void_async(db, user.id, body.team_id, voucher_id, body.reason)
