"""会议纪要 / 聊天记录 / 通知 → 责任计划草稿（所有部门）。

模型只做"摘出来"：决定、行动项、原文里出现的人名、原文里的期限说法、交付物与验收标准——每一项都带逐字依据。
人员匹配和日期换算都是确定性的（见 enrich）：姓名必须在原文出现、必须唯一匹配本企业有效成员，
期限说法按"今天"换算成具体日期，换算不了的留空待补充。模型不能编造员工、日期和验收标准。
保存只生成"草稿"（责任计划，状态 DRAFT）：补全缺口、正式指派必须由部门负责人在责任协同里发布，
员工接受、验收人验收也是各自的人工决定。
"""
import re
from datetime import date, timedelta
from typing import List, Literal, Optional

from pydantic import Field, field_validator

from service.exceptions import InvalidInput
from service.workflows.base import StrictModel, WorkflowDefinition, WriteRequest
from service.workflows.date_text import resolve_due
from utils.timeutil import utcnow

PRIORITIES = {"LOW": "低", "NORMAL": "普通", "HIGH": "高", "URGENT": "紧急"}
SOURCES = {"MEETING": "会议纪要", "CHAT": "聊天记录", "EMAIL": "邮件", "NOTICE": "通知", "OTHER": "其他材料"}
VAGUE = ("相关工作", "做好", "配合工作", "跟进一下", "处理一下", "推进一下")


class Decision(StrictModel):
    content: str = Field(min_length=1, max_length=300)
    evidence: str = Field(min_length=1, max_length=500)


class TaskItem(StrictModel):
    title: str = Field(min_length=2, max_length=160)
    responsible_name: Optional[str] = Field(default=None, max_length=40)
    collaborator_names: List[str] = Field(default_factory=list, max_length=10)
    reviewer_name: Optional[str] = Field(default=None, max_length=40)
    due_text: Optional[str] = Field(default=None, max_length=80)
    due_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    deliverable: Optional[str] = Field(default=None, max_length=300)
    acceptance_criteria: Optional[str] = Field(default=None, max_length=500)
    priority: Literal["LOW", "NORMAL", "HIGH", "URGENT"] = "NORMAL"
    depends_on: List[int] = Field(default_factory=list, max_length=10)
    evidence: str = Field(min_length=1, max_length=500)
    # 以下由系统按企业成员名单匹配得出，模型输出的值会被覆盖
    responsible_user_id: Optional[int] = None
    collaborator_user_ids: List[int] = Field(default_factory=list, max_length=10)
    reviewer_user_id: Optional[int] = None
    match_text: str = Field(default="", max_length=600)

    @field_validator("due_date")
    @classmethod
    def _real_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value


class ResponsibilityProposal(StrictModel):
    title: str = Field(min_length=2, max_length=160)
    source_type: Literal["MEETING", "CHAT", "EMAIL", "NOTICE", "OTHER"] = "OTHER"
    summary: str = Field(default="", max_length=1000)
    decisions: List[Decision] = Field(default_factory=list, max_length=30)
    tasks: List[TaskItem] = Field(default_factory=list, max_length=30)
    unresolved: List[str] = Field(default_factory=list, max_length=30)
    warnings: List[str] = Field(default_factory=list, max_length=20)

    @field_validator("unresolved")
    @classmethod
    def _short(cls, value):
        if any(len(item) > 300 for item in value):
            raise ValueError("unresolved item too long")
        return value


def _check(proposal, for_save):
    count = len(proposal.tasks)
    for index, task in enumerate(proposal.tasks, start=1):
        if any(not 1 <= d <= count or d == index for d in task.depends_on):
            raise InvalidInput(f"第 {index} 项的前置事项序号不正确（应是其他事项的序号 1–{count}）")
        if task.responsible_user_id is not None and task.responsible_user_id in task.collaborator_user_ids:
            raise InvalidInput(f"第 {index} 项的主责员工不能同时是协办人")
    if for_save and not proposal.tasks:
        raise InvalidInput("没有可以生成的责任事项：原文里没有明确要谁去做的具体动作，请补充材料后重新整理")


# ---------------------------------------------------------------- 人员匹配与日期换算（确定性）

def beijing_today() -> date:
    return (utcnow() + timedelta(hours=8)).date()


def _in_source(name: str, source: str) -> bool:
    """姓名必须逐字出现在原文；账号名是英文/数字时还要求前后不是字母数字（demo_emp 不能匹配 demo_emp2 里的片段）。"""
    if re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        return re.search(r"(?<![A-Za-z0-9_\-])" + re.escape(name) + r"(?![A-Za-z0-9_\-])", source) is not None
    return name in source


def resolve_name(name: Optional[str], people, source: str):
    """原文里的姓名 → 本企业有效成员（平台里员工的唯一标识是账号名）。返回 (user_id 或 None, 提示或 None)；
    姓名必须逐字出现在原文，且必须唯一匹配。"""
    clean = (name or "").strip()
    if not clean:
        return None, None
    if not _in_source(clean, source):
        return None, f"姓名「{clean}」没有出现在原文中，已忽略"
    matches = [p for p in people if p["name"] == clean]
    if len(matches) == 1:
        return int(matches[0]["user_id"]), None
    if len(matches) > 1:
        return None, f"有 {len(matches)} 位同名员工「{clean}」，请手动选择"
    return None, f"没有匹配到名为「{clean}」的有效成员（可能不在本部门或已离职），请手动选择"


def match_people(data, source: str, members, reviewers, today: date):
    """把模型摘出的姓名与期限说法确定性地落成人员 id 与具体日期（原地修改并返回 data）。"""
    names = {m["user_id"]: m["name"] for m in members}
    for task in data["tasks"]:
        notes: List[str] = []
        task["responsible_user_id"], note = resolve_name(task.get("responsible_name"), members, source)
        notes.append(note)
        collaborators = []
        for name in task.get("collaborator_names") or []:
            uid, note = resolve_name(name, members, source)
            notes.append(note)
            if uid is not None and uid != task["responsible_user_id"] and uid not in collaborators:
                collaborators.append(uid)
        task["collaborator_user_ids"] = collaborators
        task["reviewer_user_id"], note = resolve_name(task.get("reviewer_name"), reviewers, source)
        notes.append(note)
        # 日期只来自原文里的期限说法；模型自己给的 due_date 一律丢弃
        due_text = (task.get("due_text") or "").strip()
        resolved = resolve_due(due_text, today) if due_text else None
        task["due_date"] = resolved.isoformat() if resolved else None
        if due_text and resolved is None:
            notes.append(f"原文写的期限「{due_text}」无法换算成具体日期，请补充")
        if collaborators:
            notes.append("协办：" + "、".join(names.get(c, f"用户 {c}") for c in collaborators))
        task["match_text"] = "；".join(n for n in notes if n)[:600]
    return data


async def _enrich(db, user_id, team_id, data, source):
    from service import responsibility_service as rs
    people = await rs.candidates_async(db, user_id, team_id)
    return match_people(data, source, people["members"], people["reviewers"], beijing_today())


# ---------------------------------------------------------------- 业务系统核对（完整性）

def completeness(task, today: date):
    """与 Java 业务系统发布前的检查保持一致：缺少这些就不能正式指派。返回 [(级别, 文字)]。"""
    problems = []
    if task.get("responsible_user_id") is None:
        problems.append(("warning", "还没有主责员工"))
    if not task.get("due_date"):
        problems.append(("warning", "没有截止日期"))
    elif task["due_date"] < today.isoformat():
        problems.append(("warning", f"截止日期 {task['due_date']} 已经过了"))
    if not (task.get("deliverable") or "").strip():
        problems.append(("warning", "没有写明交付物"))
    if not (task.get("acceptance_criteria") or "").strip():
        problems.append(("warning", "没有验收标准"))
    if task.get("reviewer_user_id") is None:
        problems.append(("warning", "没有验收人"))
    elif task.get("reviewer_user_id") == task.get("responsible_user_id"):
        problems.append(("warning", "主责人不能验收自己的成果"))
    title = task.get("title", "")
    if len(title) < 4 or any(word in title for word in VAGUE):
        problems.append(("info", "事项表述比较空泛，建议写成可执行、可检查的动作"))
    return problems


async def _business_checks(user_id, team_id, data, work):
    from service.workflows.business_checks import info, warning
    today = beijing_today()
    results = []
    incomplete = 0
    for index, task in enumerate(data["tasks"], start=1):
        problems = completeness(task, today)
        if any(level == "warning" for level, _ in problems):
            incomplete += 1
        label = f"第 {index} 项「{task['title'][:20]}」"
        for level, message in problems:
            results.append((warning if level == "warning" else info)(f"{label}：{message}"))
        if task.get("match_text"):
            results.append(info(f"{label}：{task['match_text']}"))
    if data["tasks"]:
        results.insert(0, info(f"共整理出 {len(data['tasks'])} 项责任，其中 {incomplete} 项还需要补充才能正式指派；"
                               "保存只生成草稿，由部门负责人核对并发布后才会通知员工"))
    else:
        results.append(warning("没有识别出明确要谁去做的具体动作；仅讨论、征求意见的内容不会被当作责任事项"))
    for item in data.get("unresolved", []):
        results.append(info(f"待确认：{item}"))
    return results


# ---------------------------------------------------------------- 保存：生成责任计划草稿

def _write(data, work):
    # 实际写入走 _apply（需要校验企业成员身份并生成可被指派的名单）；这里只是声明目标接口
    return WriteRequest("/responsibility/plans", "responsibility.write", "create_responsibility_plan", {})


async def _apply(db, user_id, work, data):
    import json
    from service import responsibility_service as rs
    # 「AI 建议的主责人」以服务端保存的原始整理结果为准（按原文依据对应），不信任前端回传的值
    original = json.loads(work.proposal_json or "{}")
    suggested = {t["evidence"]: t.get("responsible_user_id") for t in original.get("tasks", [])}
    tasks = []
    for task in data["tasks"]:
        tasks.append({**task, "evidence": task["evidence"], "ai_responsible_user_id": suggested.get(task["evidence"])})
    body = {"title": data["title"], "source_type": data["source_type"], "source_text": work.source_text,
            "summary": data["summary"], "decisions": data["decisions"], "unresolved": data["unresolved"],
            "tasks": tasks, "automation_work_id": work.id}
    plan = await rs.create_plan_async(db, user_id, work.team_id, body, idempotency_key=f"automation-{work.id}")
    return {"id": plan["id"], "title": plan["title"], "status": plan["status"],
            "taskCount": len(plan["tasks"]), "blockerCount": plan.get("blockerCount", 0)}


INSTRUCTIONS = (
    "从会议纪要、聊天记录或通知中整理部门责任计划。title：计划标题（不超过 30 字）。source_type：MEETING 会议纪要、CHAT 聊天记录、"
    "EMAIL 邮件、NOTICE 通知、OTHER 其他。decisions 只写原文里明确已经作出的决定；仅讨论、征求意见、“可以考虑”的内容既不是决定也不是"
    "责任事项，写入 unresolved。tasks 只写原文明确要某人去做的具体动作：title 必须是可执行的动作，避免“做好相关工作”这类空泛表述；"
    "responsible_name 只写原文里出现的人名，原文没有明确责任人就写 null，一项责任只能有一个主责人（多人共同负责时，原文点名的第一位为主责，"
    "其余写入 collaborator_names）；reviewer_name 只在原文明确了验收/审核/确认的人时填写；due_text 摘录原文里的期限说法"
    "（如“下周五前”“10月15日”“月底”），原文没有就写 null，不要自己推算日期，due_date 一律写 null；deliverable、acceptance_criteria "
    "只在原文提到时填写，不要替用户编造；priority 只在原文强调紧急或重要时调高；depends_on 填原文明确“在某事之后”的前置事项在 tasks 中的"
    "序号（从 1 开始）。每个 evidence 必须是原文里逐字存在的片段。unresolved 写原文没有说清的事（没有责任人、没有期限、验收人不明、"
    "是否已作出决定）。match_text、*_user_id 字段一律留空。")


WORKFLOW = WorkflowDefinition(
    id="responsibility", title="会议纪要 / 工作文本 → 责任计划", name="责任计划整理", draft_name="责任计划",
    schema=ResponsibilityProposal,
    instructions=INSTRUCTIONS,
    evidence=lambda p: [d.evidence for d in p.decisions] + [t.evidence for t in p.tasks]
                       + [t.due_text for t in p.tasks if t.due_text],
    write=_write,
    form=[
        {"type": "text", "key": "title", "label": "计划标题", "max": 160, "required": True, "wide": True},
        {"type": "select", "key": "source_type", "label": "材料类型", "required": True,
         "options": [{"value": k, "label": v} for k, v in SOURCES.items()]},
        {"type": "textarea", "key": "summary", "label": "主要决定摘要", "max": 1000, "rows": 2},
        {"type": "list", "key": "tasks", "label": "责任事项", "item": "责任事项", "min": 0, "fields": [
            {"type": "text", "key": "title", "label": "责任事项（可执行的动作）", "max": 160, "required": True, "wide": True},
            {"type": "select", "key": "responsible_user_id", "label": "主责员工", "options_from": "members", "nullable": True,
             "placeholder": "待补充：选择主责员工"},
            {"type": "select", "key": "reviewer_user_id", "label": "验收人", "options_from": "reviewers", "nullable": True,
             "placeholder": "待补充：选择验收人"},
            {"type": "date", "key": "due_date", "label": "截止日期", "nullable": True},
            {"type": "select", "key": "priority", "label": "优先级", "required": True,
             "options": [{"value": k, "label": v} for k, v in PRIORITIES.items()]},
            {"type": "text", "key": "deliverable", "label": "交付物", "max": 300, "nullable": True, "wide": True},
            {"type": "text", "key": "acceptance_criteria", "label": "验收标准", "max": 500, "nullable": True, "wide": True},
            {"type": "evidence", "key": "evidence", "label": "原文依据"},
            {"type": "evidence", "key": "match_text", "label": "核对提示"},
        ]},
        {"type": "note", "text": "保存只生成责任计划草稿，不会通知任何员工。缺少主责员工、期限、交付物、验收标准或验收人的责任事项不能正式发布；"
                                  "由部门负责人在“责任协同”里核对后发布，员工接受、验收人验收都是各自的人工决定。"},
    ],
    source_label="粘贴会议纪要、工作群聊天记录、通知或工作安排",
    example=("10月12日例会纪要：会议决定新版首页本月发布。张三负责新版首页联调，下周五前提交可部署的前端构建包，"
             "验收标准是测试环境回归通过且无阻断问题，由李四验收。王五负责整理客户反馈清单，月底前完成。"
             "关于是否增加会员页的问题，大家还在讨论，暂不决定。"),
    hint="提取决定与行动项、主责人、期限、交付物和验收标准；姓名只匹配本企业有效成员，找不到的留待补充，不会编造。",
    check=_check, business_checks=_business_checks, order=50,
    baseline_minutes=30,
    enrich=_enrich, apply=_apply,
)
