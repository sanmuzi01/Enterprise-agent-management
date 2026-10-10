"""财务与 IT 补齐的接口。

发票识别（员工）：
  POST /enterprise/finance/invoices/extract        上传图片 / PDF 识别发票
  GET  /enterprise/finance/invoices                我的识别记录
  GET  /enterprise/finance/invoices/{id}
  POST /enterprise/finance/invoices/{id}/confirm   核对确认（低置信度字段必须逐项确认）
  POST /enterprise/finance/invoices/{id}/discard
  POST /enterprise/finance/policy/check            报销明细按费用标准预检（填表时提示）
费用标准（平台管理员）：GET/POST /admin/expense-policies，PUT/DELETE /admin/expense-policies/{id}
ERP（财务人员）：POST /enterprise/finance/vouchers/{id}/erp-export，GET 同路径查看推送状态
IT 自助：
  POST /enterprise/it/self-service                       描述问题 → 推荐文章（建会话）
  POST /enterprise/it/self-service/{id}/articles/{aid}/view | rate
  POST /enterprise/it/self-service/{id}/solved           明确确认已解决
  POST /enterprise/it/self-service/{id}/ticket           没有解决，创建工单
  GET  /enterprise/it/self-service/metrics               IT 部门看指标与文章维护清单
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from models.async_db import get_async_db
from models.init_db import User
from service import erp_connector, expense_policy_service, invoice_service, it_self_service
from service.dependencies import get_current_admin_user_async, get_current_user_async
from service.exceptions import InvalidInput

router = APIRouter(tags=["财务与 IT 补齐"])


# ------------------------------------------------------------------ 发票

@router.post("/enterprise/finance/invoices/extract", summary="识别发票")
async def extract_invoice(team_id: int = Form(...), file: UploadFile = File(...), db=Depends(get_async_db),
                          user: User = Depends(get_current_user_async)):
    content = await file.read(10 * 1024 * 1024 + 1)
    return await invoice_service.extract(db, user.id, team_id, file.filename or "invoice", content)


@router.get("/enterprise/finance/invoices", summary="我的发票识别记录")
async def list_invoices(status: Optional[str] = None, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await invoice_service.list_mine(db, user.id, status)


@router.get("/enterprise/finance/invoices/{extraction_id}", summary="发票识别详情")
async def get_invoice(extraction_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await invoice_service.get(db, user.id, extraction_id)


class ConfirmInvoiceBody(BaseModel):
    values: Dict[str, Optional[str]] = Field(default_factory=dict, description="改过的字段（不改的可以不传）")
    confirmed_fields: List[str] = Field(default_factory=list, description="逐项核对过的字段")


@router.post("/enterprise/finance/invoices/{extraction_id}/confirm", summary="核对确认发票")
async def confirm_invoice(extraction_id: int, body: ConfirmInvoiceBody, db=Depends(get_async_db),
                          user: User = Depends(get_current_user_async)):
    return await invoice_service.confirm(db, user.id, extraction_id, body.values, body.confirmed_fields)


@router.post("/enterprise/finance/invoices/{extraction_id}/discard", summary="作废发票识别记录")
async def discard_invoice(extraction_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await invoice_service.discard(db, user.id, extraction_id)


class PolicyLine(BaseModel):
    category: str
    amount: float = Field(gt=0)
    invoice_no: Optional[str] = None


class PolicyCheckBody(BaseModel):
    team_id: int
    city_level: Optional[str] = Field(default=None, pattern="^(tier1|tier2|other)$")
    lines: List[PolicyLine]


@router.post("/enterprise/finance/policy/check", summary="按费用标准预检报销明细")
async def check_policy(body: PolicyCheckBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    from service.department_access import require_team_member_async
    await require_team_member_async(db, user.id, body.team_id, "finance")
    lines = [{"category": l.category, "amount": l.amount, "invoiceNo": l.invoice_no} for l in body.lines]
    return await expense_policy_service.check_claim(db, user.id, body.team_id, lines, body.city_level)


# ------------------------------------------------------------------ 费用标准（管理员）

class PolicyRuleBody(BaseModel):
    category: str
    city_level: Optional[str] = None
    employee_level: Optional[str] = None
    amount_limit: Optional[str] = Field(default=None, max_length=20)
    receipt_required: bool = True
    approval_level: str = "team_admin"
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=300)


@router.get("/admin/expense-policies", summary="费用标准列表")
async def list_policies(db=Depends(get_async_db), _: User = Depends(get_current_admin_user_async)):
    return {"rules": await expense_policy_service.list_rules(db), "categories": expense_policy_service.CATEGORIES,
            "city_levels": expense_policy_service.CITY_LEVELS, "employee_levels": expense_policy_service.EMPLOYEE_LEVELS,
            "approval_levels": expense_policy_service.APPROVAL_LEVELS}


@router.post("/admin/expense-policies", summary="新增费用标准")
async def create_policy(body: PolicyRuleBody, db=Depends(get_async_db), admin: User = Depends(get_current_admin_user_async)):
    return await expense_policy_service.save_rule(db, admin.id, body.model_dump())


@router.put("/admin/expense-policies/{rule_id}", summary="修改费用标准")
async def update_policy(rule_id: int, body: PolicyRuleBody, db=Depends(get_async_db),
                        admin: User = Depends(get_current_admin_user_async)):
    return await expense_policy_service.save_rule(db, admin.id, body.model_dump(), rule_id)


@router.delete("/admin/expense-policies/{rule_id}", summary="删除费用标准")
async def delete_policy(rule_id: int, db=Depends(get_async_db), admin: User = Depends(get_current_admin_user_async)):
    await expense_policy_service.delete_rule(db, admin.id, rule_id)
    return {"ok": True}


# ------------------------------------------------------------------ ERP

class TeamBody(BaseModel):
    team_id: int


@router.post("/enterprise/finance/vouchers/{voucher_id}/erp-export", summary="已入账凭证推送到 ERP（幂等）")
async def erp_export(voucher_id: int, body: TeamBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await erp_connector.push_voucher(db, user.id, body.team_id, voucher_id)


@router.get("/enterprise/finance/vouchers/{voucher_id}/erp-export", summary="凭证的 ERP 推送状态")
async def erp_export_status(voucher_id: int, team_id: int, db=Depends(get_async_db),
                            user: User = Depends(get_current_user_async)):
    return await erp_connector.export_status(db, user.id, team_id, voucher_id)


# ------------------------------------------------------------------ IT 自助

class SelfServiceStart(BaseModel):
    team_id: Optional[int] = None
    question: str = Field(min_length=2, max_length=2000)


@router.post("/enterprise/it/self-service", summary="描述问题，推荐解决办法")
async def start_self_service(body: SelfServiceStart, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it_self_service.start(db, user.id, body.team_id, body.question)


@router.post("/enterprise/it/self-service/{session_id}/articles/{article_id}/view", summary="打开了一篇文章（不算解决）")
async def view_article(session_id: int, article_id: int, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it_self_service.view_article(db, user.id, session_id, article_id)


class RateBody(BaseModel):
    helpful: bool


@router.post("/enterprise/it/self-service/{session_id}/articles/{article_id}/rate", summary="评价文章有没有用")
async def rate_article(session_id: int, article_id: int, body: RateBody, db=Depends(get_async_db),
                       user: User = Depends(get_current_user_async)):
    return await it_self_service.rate_article(db, user.id, session_id, article_id, body.helpful)


class SolvedBody(BaseModel):
    article_id: Optional[int] = None


@router.post("/enterprise/it/self-service/{session_id}/solved", summary="已解决")
async def solved(session_id: int, body: SolvedBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it_self_service.mark_solved(db, user.id, session_id, body.article_id)


class ToTicketBody(BaseModel):
    team_id: int
    category: str = Field(min_length=1, max_length=30)
    priority: Optional[str] = Field(default=None, max_length=20)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)


@router.post("/enterprise/it/self-service/{session_id}/ticket", summary="没有解决，创建工单")
async def to_ticket(session_id: int, body: ToTicketBody, db=Depends(get_async_db), user: User = Depends(get_current_user_async)):
    return await it_self_service.convert_to_ticket(db, user.id, session_id, body.team_id, body.category, body.priority,
                                                   body.title, body.description)


@router.get("/enterprise/it/self-service/metrics", summary="自助解决指标（IT 部门）")
async def self_service_metrics(team_id: int, days: int = 30, db=Depends(get_async_db),
                               user: User = Depends(get_current_user_async)) -> Dict[str, Any]:
    if not 1 <= days <= 365:
        raise InvalidInput("统计天数为 1～365")
    return await it_self_service.metrics(db, user.id, team_id, days)
