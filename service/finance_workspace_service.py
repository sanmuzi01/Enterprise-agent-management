"""部门工作台：财务报销闭环（里程碑4）。

跟 `procurement_workspace_service.py` 同款设计——独立文件、独立
`_translate_hub_error`/`_caller_admin_flags_async`，权限判断在 FastAPI 这层显式做、
调 Java 之前就拦截。跟采购一样，审批类操作需要判断"是不是部门负责人或企业管理员"
（不是像 CRM 那样只判断"是否本部门成员"——报销走审批流程，跟请假/采购同一套权限
模型）。
"""
import asyncio
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from models.enterprise_dao import is_team_admin_of_team_async
from service.department_access import check_team_module_async, require_team_member_async
from service import enterprise_access
from service import enterprise_hub_client as hub
from service.exceptions import AppError, Conflict, InvalidInput, NotFound, PermissionDenied, UpstreamError


def _translate_hub_error(exc: hub.EnterpriseHubError) -> AppError:
    status = exc.status_code
    if status == 400:
        return InvalidInput(exc.detail)
    if status == 403:
        return PermissionDenied(exc.detail)
    if status == 404:
        return NotFound(exc.detail)
    if status == 409:
        return Conflict(exc.detail)
    return UpstreamError(exc.detail)


async def _caller_admin_flags_async(db, user_id: int, team_id: Optional[int]) -> Dict[str, bool]:
    if team_id is not None:
        await check_team_module_async(db, team_id, "finance")
    org_admin = await enterprise_access.is_org_admin_async(db, user_id)
    team_admin = await is_team_admin_of_team_async(db, user_id, team_id) if team_id is not None else False
    return {"is_org_admin": org_admin, "is_team_admin": team_admin}


async def list_my_expense_claims_async(user_id: int) -> List[Dict[str, Any]]:
    # team_id 传 None——Java 端 GET /expenses/mine 不需要 team 上下文，跟"我的请假"/
    # "我的采购申请"完全对称：一个人的报销单不分部门查，天然靠 applicantUserId 过滤。
    try:
        return await asyncio.to_thread(
            hub.call, "GET", "/finance/expenses/mine", user_id, None,
            ["finance.read"], "get_my_expense_claims",
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def create_my_expense_draft_async(
        db, user_id: int, team_id: int, lines: List[Dict[str, Any]], city_level: Optional[str] = None,
) -> Dict[str, Any]:
    """lines 里每条可以带 invoiceExtractionId（已确认的发票识别结果：发票号由它带入，金额不能超过发票金额）
    和 overStandardReason（超标准说明）。先按费用标准检查：缺发票不能保存；超标准必须写说明，
    说明和需要的审批级别写进明细说明。"""
    from decimal import Decimal
    from service import expense_policy_service, invoice_service
    await require_team_member_async(db, user_id, team_id, "finance", message="不属于该部门，无法为该部门创建报销申请")
    team = await check_team_module_async(db, team_id, "finance")
    invoices = await invoice_service.take_for_claim(
        db, user_id, [int(line["invoiceExtractionId"]) for line in lines if line.get("invoiceExtractionId")])
    prepared = []
    for line in lines:
        item = {k: line[k] for k in ("category", "amount", "description", "invoiceNo") if k in line}
        invoice = invoices.get(int(line["invoiceExtractionId"])) if line.get("invoiceExtractionId") else None
        if invoice is not None:
            item["invoiceNo"] = invoice.invoice_number
            if invoice.total_amount and Decimal(str(item["amount"])) > Decimal(invoice.total_amount):
                raise InvalidInput(f"报销金额 {item['amount']} 超过了发票金额 {invoice.total_amount}")
        prepared.append(item)
    checks = await expense_policy_service.check_claim(db, user_id, team_id, prepared, city_level)
    for check, line, item in zip(checks, lines, prepared):
        if check["status"] == "missing_receipt":
            raise InvalidInput(f"第 {check['index'] + 1} 条：{check['message']}")
        if check["status"] == "over_limit":
            reason = (line.get("overStandardReason") or "").strip()
            if not reason:
                raise InvalidInput(f"第 {check['index'] + 1} 条{check['message']}")
            approver = expense_policy_service.APPROVAL_LEVELS.get(check["approval_level"])
            note = f"【超标准 {check['over_by']}（上限 {check['limit']}），需{approver}审批；说明：{reason[:100]}】"
            item["description"] = ((item.get("description") or "") + note)[:200]
    try:
        claim = await asyncio.to_thread(
            hub.call, "POST", "/finance/expenses", user_id, team_id,
            ["finance.write"], "create_expense_draft",
            # departmentCode：销售部门的报销入账走销售费用，其他走管理费用
            json_body={"lines": prepared, "departmentCode": team.department_code}, idempotency_key=str(uuid.uuid4()),
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)
    if invoices:
        await invoice_service.mark_used(db, invoices, (claim or {}).get("id"))
    return claim


async def submit_my_expense_claim_async(user_id: int, request_id: int) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(
            hub.call, "POST", f"/finance/expenses/{int(request_id)}/submit", user_id, None,
            ["finance.write"], "submit_expense_claim",
            idempotency_key=str(uuid.uuid4()),
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def list_team_pending_expense_claims_async(db, user_id: int, team_id: int) -> List[Dict[str, Any]]:
    flags = await _caller_admin_flags_async(db, user_id, team_id)
    if not (flags["is_org_admin"] or flags["is_team_admin"]):
        raise PermissionDenied("不是该部门负责人或企业管理员，无法查看待审批列表")
    try:
        return await asyncio.to_thread(
            hub.call, "GET", f"/finance/expenses/team-pending?teamId={int(team_id)}", user_id, team_id,
            ["finance.read"], "get_team_pending_expense_claims",
            is_org_admin=flags["is_org_admin"], is_team_admin=flags["is_team_admin"],
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)


async def decide_expense_claim_async(
        db, user_id: int, request_id: int, team_id: int, action: str, note: Optional[str],
) -> Dict[str, Any]:
    if action not in ("approve", "reject"):
        raise InvalidInput(f"未知的处理动作: {action}")
    flags = await _caller_admin_flags_async(db, user_id, team_id)
    if not (flags["is_org_admin"] or flags["is_team_admin"]):
        raise PermissionDenied("不是该部门负责人或企业管理员，无法处理该报销单")
    department_code = (await db.execute(text("SELECT department_code FROM teams WHERE id = :t"),
                                        {"t": team_id})).scalar()
    try:
        return await asyncio.to_thread(
            hub.call, "POST", f"/finance/expenses/{int(request_id)}/{action}", user_id, team_id,
            ["finance.approve"], f"{action}_expense_claim",
            is_org_admin=flags["is_org_admin"], is_team_admin=flags["is_team_admin"],
            # 批准会自动生成记账凭证草稿；老报销单没有部门类型时，用审批人所在部门补上
            json_body={"note": note, "departmentCode": department_code}, idempotency_key=str(uuid.uuid4()),
        )
    except hub.EnterpriseHubError as exc:
        raise _translate_hub_error(exc)
