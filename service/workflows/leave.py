"""请假描述 → OA 请假草稿（所有部门）。"""
from datetime import date
from typing import List, Literal

from pydantic import Field

from service.exceptions import InvalidInput
from service.workflows.base import StrictModel, WorkflowDefinition, WriteRequest

LEAVE_TYPES = {"annual": "年假", "sick": "病假", "personal": "事假"}


class LeaveProposal(StrictModel):
    leave_type_code: Literal["annual", "sick", "personal"] | None = None
    start_date: date | None = None
    end_date: date | None = None
    reason: str = Field(default="", max_length=500)
    evidence: str = Field(min_length=1, max_length=500)
    warnings: List[str] = Field(default_factory=list, max_length=20)


def _check(proposal, for_save):
    if proposal.start_date and proposal.end_date and proposal.start_date > proposal.end_date:
        raise InvalidInput("请假结束日期不能早于开始日期")
    if for_save and not all((proposal.leave_type_code, proposal.start_date, proposal.end_date)):
        raise InvalidInput("请补全假期类型、开始日期和结束日期")


async def _business_checks(user_id, team_id, data, work):
    from service.workflows.business_checks import info, read, warning

    code, start, end = data.get("leave_type_code"), data.get("start_date"), data.get("end_date")
    if not (code and start and end):
        return [info("补全假期类型和起止日期后，可重新核对余额与时间冲突")]
    results = []
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    year = date.fromisoformat(start).year
    balances = await read("/oa/leave/balance", user_id, team_id, "oa.leave.read", "get_leave_balance", {"year": year})
    balance = next((b for b in balances if b["leaveTypeCode"] == code), None)
    name = LEAVE_TYPES[code]
    if balance is None:
        results.append(warning(f"没有 {year} 年度的{name}余额记录，提交时会被拒绝，请联系人事"))
    elif balance["remainingDays"] < days:
        results.append(warning(f"{name}余额 {balance['remainingDays']:g} 天，本次 {days} 天，提交时会被拒绝"))
    else:
        results.append(info(f"{name}余额 {balance['remainingDays']:g} 天，本次 {days} 天，批准后剩 "
                            f"{balance['remainingDays'] - days:g} 天"))
    mine = await read("/oa/leave/requests/mine", user_id, None, "oa.leave.read", "get_my_leave_requests")
    overlap = [r for r in mine if r["status"] != "REJECTED" and r["startDate"] <= end and r["endDate"] >= start]
    for r in overlap[:3]:
        results.append(warning(f"与已有请假单 #{r['id']}（{r['startDate']} 至 {r['endDate']}，{r['status']}）时间重叠"))
    return results


def _write(data, work):
    return WriteRequest("/oa/leave/requests", "oa.leave.write", "create_leave_draft", {
        "leaveTypeCode": data["leave_type_code"], "startDate": data["start_date"],
        "endDate": data["end_date"], "reason": data["reason"]})


WORKFLOW = WorkflowDefinition(
    id="leave", title="请假描述 → OA 请假草稿", name="请假申请整理", draft_name="请假",
    schema=LeaveProposal,
    instructions=("leave_type_code 仅使用 annual 年假、sick 病假、personal 事假；类型或完整日期不明确则填 null "
                  "并在 warnings 提示补全。相对日期（如明天、下周）或缺少年份的日期不能自行推算；reason 只依据原文。"),
    evidence=lambda p: [p.evidence],
    write=_write,
    form=[
        {"type": "select", "key": "leave_type_code", "label": "假期类型", "required": True,
         "placeholder": "请补全假期类型", "options": [{"value": k, "label": v} for k, v in LEAVE_TYPES.items()]},
        {"type": "date", "key": "start_date", "label": "开始日期", "required": True},
        {"type": "date", "key": "end_date", "label": "结束日期", "required": True},
        {"type": "textarea", "key": "reason", "label": "请假原因", "max": 500, "rows": 3},
        {"type": "evidence", "key": "evidence", "label": "原文依据"},
        {"type": "note", "text": "请假天数与余额以 OA 系统为准。"},
    ],
    source_label="描述假期类型、起止日期与原因",
    example="我要申请年假，2026年10月12日至2026年10月14日，原因是家庭事务。",
    hint="提取假期类型与日期；未明确的年份、日期不会自行推算。",
    preferred_for=frozenset({"hr"}),
    check=_check, business_checks=_business_checks, order=20,
    baseline_minutes=6,
    extra={"rules": [{"type": "date_order", "start": "start_date", "end": "end_date",
                      "message": "结束日期不能早于开始日期。"}]},
)
