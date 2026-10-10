"""问题描述 → IT 工单草稿（所有部门；IT 部门默认选中）。"""
from typing import List, Literal

from pydantic import Field

from service.exceptions import InvalidInput
from service.workflows.base import StrictModel, WorkflowDefinition, WriteRequest

CATEGORIES = {"INCIDENT": "故障", "ACCOUNT": "账号申请", "PERMISSION": "权限申请", "DEVICE": "设备申请", "OTHER": "咨询/其他"}
PRIORITIES = {"LOW": "低", "NORMAL": "普通", "HIGH": "高", "URGENT": "紧急"}
NEEDS_APPROVAL = {"ACCOUNT", "PERMISSION", "DEVICE"}


class TicketProposal(StrictModel):
    category: Literal["INCIDENT", "ACCOUNT", "PERMISSION", "DEVICE", "OTHER"]
    priority: Literal["LOW", "NORMAL", "HIGH", "URGENT"] = "NORMAL"
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=4, max_length=2000)
    evidence: str = Field(min_length=1, max_length=500)
    warnings: List[str] = Field(default_factory=list, max_length=20)


def _check(proposal, for_save):
    if for_save and len(proposal.description.strip()) < 4:
        raise InvalidInput("请写清问题现象和影响")


async def _business_checks(user_id, team_id, data, work):
    from service.workflows.business_checks import info, read, warning

    results = []
    text_ = f"{data['title']} {data['description']}"
    classification = await read("/it/classify", user_id, team_id, "it.ticket.read", "classify_it_ticket", {"text": text_})
    if classification["category"] != data["category"]:
        results.append(warning(
            f"规则判断更像「{CATEGORIES[classification['category']]}」而不是「{CATEGORIES[data['category']]}」："
            + "；".join(classification["reasons"]) + "。请核对分类，分类决定是否需要先审批"))
    if classification["priority"] != data["priority"] and data["priority"] != "NORMAL":
        results.append(info(f"规则建议优先级「{PRIORITIES[classification['priority']]}」，当前是「{PRIORITIES[data['priority']]}」"))
    elif classification["priority"] == "URGENT" and data["priority"] == "NORMAL":
        results.append(warning("描述里提到影响范围很大（" + "；".join(classification["reasons"]) + "），建议把优先级调成紧急"))

    mine = await read("/it/tickets/mine", user_id, team_id, "it.ticket.read", "get_my_it_tickets")
    open_same = [t for t in mine if t["title"] == data["title"]
                 and t["status"] in ("PENDING_APPROVAL", "OPEN", "IN_PROGRESS", "WAITING_USER")]
    for t in open_same[:1]:
        results.append(warning(f"你已有标题相同且未结束的工单 #{t['id']}，保存会被拒绝，请在原工单里补充信息"))

    if data["category"] in NEEDS_APPROVAL:
        results.append(info(f"「{CATEGORIES[data['category']]}」提交后需要先由本部门负责人批准，批准后 IT 才会处理"))
    if data["category"] == "DEVICE":
        devices = await read("/it/devices/mine", user_id, team_id, "it.ticket.read", "get_my_devices")
        if devices:
            names = "、".join(f"{d['assetNo']}（{d['model']}）" for d in devices[:3])
            results.append(warning(f"你名下已有 {len(devices)} 台设备：{names}；确认是新增还是更换"))

    articles = await read("/it/kb/suggest", user_id, team_id, "it.ticket.read", "suggest_it_solutions", {"text": text_})
    for article in articles[:2]:
        first_step = article["steps"].splitlines()[0] if article["steps"] else ""
        results.append(info(f"可先自助排查：{article['title']}（{first_step}）"))
    return results


def _write(data, work):
    return WriteRequest("/it/tickets", "it.ticket.write", "create_it_ticket", {
        "category": data["category"], "priority": data["priority"], "title": data["title"],
        "description": data["description"]})


WORKFLOW = WorkflowDefinition(
    id="ticket", title="问题描述 → IT 工单", name="IT 工单整理", draft_name="IT 工单",
    schema=TicketProposal,
    instructions=("category：INCIDENT 故障（含忘记密码、账号被锁）、ACCOUNT 开通/变更/注销账号、PERMISSION 开通权限、"
                  "DEVICE 申请领用设备、OTHER 咨询。priority：URGENT 仅用于多人/整个部门受影响或业务中断，HIGH 影响当前工作且有时限，"
                  "NORMAL 默认，LOW 不急的咨询。title 是一句话概括（不超过 30 字），description 只保留原文提到的现象、影响、位置、"
                  "设备型号等事实，不要编造原文没有的报错代码或型号。"),
    evidence=lambda p: [p.evidence],
    write=_write,
    form=[
        {"type": "select", "key": "category", "label": "工单类型", "required": True,
         "options": [{"value": k, "label": v} for k, v in CATEGORIES.items()]},
        {"type": "select", "key": "priority", "label": "优先级", "required": True,
         "options": [{"value": k, "label": v} for k, v in PRIORITIES.items()]},
        {"type": "text", "key": "title", "label": "标题", "max": 120, "required": True, "wide": True},
        {"type": "textarea", "key": "description", "label": "问题描述", "max": 2000, "rows": 4, "required": True},
        {"type": "evidence", "key": "evidence", "label": "原文依据"},
        {"type": "note", "text": "账号、权限、设备申请需要先经本部门负责人批准；故障和咨询直接进入 IT 队列。"},
    ],
    source_label="描述遇到的问题或需要的服务（现象、位置、影响范围）",
    example="三楼打印机卡纸后一直脱机，整个部门都没法打印，下午客户会议要用资料，比较着急。",
    hint="提取工单类型、优先级、标题和描述；提交前会给出自助排查建议。",
    preferred_for=frozenset({"it"}),
    check=_check, business_checks=_business_checks, order=15,
    baseline_minutes=8,
)
