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
    check=_check, order=20,
    extra={"rules": [{"type": "date_order", "start": "start_date", "end": "end_date",
                      "message": "结束日期不能早于开始日期。"}]},
)
