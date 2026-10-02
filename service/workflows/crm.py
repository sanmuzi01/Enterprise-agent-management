"""沟通记录 → CRM 跟进草稿 + 个人后续待办（仅销售部门）。"""
from datetime import date
from typing import List

from pydantic import Field

from service.exceptions import InvalidInput
from service.workflows.base import StrictModel, WorkflowDefinition, WriteRequest


class FollowupTask(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    due_date: date | None = None
    evidence: str = Field(min_length=1, max_length=500)


class CrmProposal(StrictModel):
    content: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(min_length=1, max_length=500)
    tasks: List[FollowupTask] = Field(default_factory=list, max_length=20)
    warnings: List[str] = Field(default_factory=list, max_length=20)


async def _precheck(db, user_id, team_id, data):
    if data.customer_id is None:
        raise InvalidInput("请先选择当前部门的客户")
    from service import crm_workspace_service
    await crm_workspace_service.get_customer_summary_async(db, user_id, team_id, data.customer_id)


STAGES = {"LEAD": "线索", "QUALIFIED": "已确认需求", "PROPOSAL": "方案", "NEGOTIATION": "谈判", "WON": "赢单", "LOST": "输单"}


async def _business_checks(user_id, team_id, data, work):
    from datetime import datetime, timezone
    from service.workflows.business_checks import info, money, read, warning

    results = []
    summary = await read(f"/crm/customers/{work.customer_id}", user_id, team_id, "crm.read", "get_customer_summary")
    followups = summary.get("recentFollowUps") or []
    if followups:
        last = datetime.fromisoformat(followups[0]["createdAt"].replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - last).days
        results.append(info(f"{summary['name']} 上次跟进：{last.date().isoformat()}（{days} 天前），共 {len(followups)} 条近期记录"))
    else:
        results.append(info(f"{summary['name']} 还没有跟进记录，这是首次跟进"))
    open_ops = [o for o in summary.get("opportunities") or [] if o["stage"] not in ("WON", "LOST")]
    for o in open_ops[:3]:
        results.append(info(f"在谈商机 #{o['id']}：{STAGES.get(o['stage'], o['stage'])}，金额 {money(o['amount'])}；"
                            "如沟通改变了阶段或金额，请在 CRM 模块更新"))
    undated = sum(1 for t in data.get("tasks") or [] if not t.get("due_date"))
    if undated:
        results.append(warning(f"{undated} 条后续待办没有截止日期，建议补充以免遗漏"))
    return results


def _write(data, work):
    return WriteRequest(f"/crm/customers/{work.customer_id}/followups", "crm.write", "create_followup_draft",
                        {"content": data["content"]})


WORKFLOW = WorkflowDefinition(
    id="crm", title="沟通记录 → CRM 跟进与待办", name="沟通记录整理", draft_name="跟进",
    schema=CrmProposal,
    instructions=("content 总结客户需求、实际沟通和下一步；tasks 只提取材料中明确的后续事项，"
                  "日期不明确时 due_date=null。不要编造客户意向、承诺和成交结果。"),
    evidence=lambda p: [p.evidence] + [t.evidence for t in p.tasks],
    write=_write,
    form=[
        {"type": "textarea", "key": "content", "label": "跟进内容", "max": 1000, "rows": 4, "required": True},
        {"type": "evidence", "key": "evidence", "label": "原文依据"},
        {"type": "list", "key": "tasks", "label": "后续待办", "item": "待办", "fields": [
            {"type": "text", "key": "title", "label": "后续待办", "max": 200, "required": True},
            {"type": "date", "key": "due_date", "label": "截止日期"},
            {"type": "evidence", "key": "evidence", "label": "依据"},
        ]},
    ],
    source_label="粘贴会议纪要、沟通记录",
    example="客户希望试用，约定2026年10月8日发送方案；预算尚未确定。",
    hint="提取跟进内容与后续待办，保存到你选择的客户。",
    departments=frozenset({"sales"}), preferred_for=frozenset({"sales"}),
    needs_customer=True, precheck=_precheck, business_checks=_business_checks,
    followups={"key": "tasks", "title": "title", "due": "due_date"},
    order=40,
)
