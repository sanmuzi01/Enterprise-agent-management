"""费用材料 → 报销草稿（所有部门）。"""
from decimal import Decimal
from typing import List, Literal

from pydantic import Field

from service.exceptions import InvalidInput
from service.workflows.base import StrictModel, WorkflowDefinition, WriteRequest

CATEGORIES = {"TRAVEL": "差旅", "MEAL": "餐饮", "OFFICE_SUPPLY": "办公用品", "TRANSPORT": "交通", "OTHER": "其他"}


class ExpenseLine(StrictModel):
    category: Literal["TRAVEL", "MEAL", "OFFICE_SUPPLY", "TRANSPORT", "OTHER"]
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    description: str = Field(min_length=1, max_length=300)
    invoice_no: str | None = Field(default=None, max_length=80)
    evidence: str = Field(min_length=1, max_length=500)


class ExpenseProposal(StrictModel):
    lines: List[ExpenseLine] = Field(min_length=1, max_length=50)
    warnings: List[str] = Field(default_factory=list, max_length=20)


def _check(proposal, for_save):
    seen = set()
    for line in proposal.lines:
        if line.invoice_no:
            if line.invoice_no in seen:
                raise InvalidInput("同一材料中出现重复发票号，请合并或删除重复费用")
            seen.add(line.invoice_no)


async def _business_checks(user_id, team_id, data, work):
    from service import enterprise_hub_client as hub
    from service.workflows.business_checks import blocker, info, money, read, warning

    results = []
    numbers = sorted({line["invoice_no"].strip() for line in data["lines"] if line.get("invoice_no")})
    if numbers:
        used = await read("/finance/invoices/usage", user_id, team_id, "finance.read", "check_invoice_usage",
                          {"numbers": ",".join(numbers)})
        for row in used:
            results.append(blocker(f"发票号 {row['invoiceNo']} 已在报销单 #{row['claimId']} 中使用，"
                                   "保存会被拒绝，请删除这条费用或更正发票号"))
    missing = sum(1 for line in data["lines"] if not line.get("invoice_no"))
    if missing:
        results.append(warning(f"{missing} 条费用没有发票号，审批时可能需要补充票据"))
    total = sum(float(line["amount"]) for line in data["lines"])
    try:
        budget = await read("/finance/budget", user_id, team_id, "finance.read", "get_expense_budget")
        remaining = float(budget["remainingAmount"])
        if total > remaining:
            results.append(warning(f"报销合计 {money(total)} 超出部门剩余报销预算 {money(remaining)}，审批时会被拒绝"))
        else:
            results.append(info(f"报销合计 {money(total)}，部门剩余报销预算 {money(remaining)}"))
    except hub.EnterpriseHubError as exc:
        if exc.status_code != 400:
            raise
        results.append(warning("本部门没有今年的报销预算记录，审批时会被拒绝，请联系管理员配置预算"))
    return results


def _write(data, work):
    return WriteRequest("/finance/expenses", "finance.write", "create_expense_draft", {"lines": [
        {"category": line["category"], "amount": line["amount"], "description": line["description"],
         "invoiceNo": line.get("invoice_no")} for line in data["lines"]]})


WORKFLOW = WorkflowDefinition(
    id="expense", title="费用材料 → 报销草稿", name="费用材料整理", draft_name="报销",
    schema=ExpenseProposal,
    instructions=("只提取明确金额的实际支出，不把合计重复当明细；同一发票不得重复生成费用行。"
                  "币种仅支持人民币，外币不要转换，列入 warnings。不要编造金额和发票号。"),
    evidence=lambda p: [line.evidence for line in p.lines],
    write=_write,
    form=[
        {"type": "list", "key": "lines", "label": "费用明细", "item": "费用", "min": 1, "fields": [
            {"type": "select", "key": "category", "label": "类别", "required": True,
             "options": [{"value": k, "label": v} for k, v in CATEGORIES.items()]},
            {"type": "money", "key": "amount", "label": "金额（元）", "required": True},
            {"type": "text", "key": "invoice_no", "label": "发票号", "max": 80, "nullable": True},
            {"type": "text", "key": "description", "label": "说明", "max": 300, "required": True, "wide": True},
            {"type": "evidence", "key": "evidence", "label": "依据"},
        ]},
        {"type": "sum", "list": "lines", "field": "amount", "label": "合计"},
    ],
    source_label="粘贴费用明细、票据文字或导出的流水",
    example="10月1日出差高铁票260元，发票号G001；出租车48元，暂无发票。",
    hint="提取费用分类、金额与发票号，核对后生成报销草稿。",
    preferred_for=frozenset({"finance", None}),
    check=_check, business_checks=_business_checks, order=10,
    baseline_minutes=15,
)
