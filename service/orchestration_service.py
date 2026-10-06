"""跨部门协同办理：把一段话拆成多个部门步骤，每一步交给对应的 AI 整理工作流，逐步人工核对后各自写入业务系统。

拆分是确定性规则（按句子和连接词切分，再按关键词判断属于哪类工作），每一步都给出依据，用户可以删掉、改类型：
- 当前部门能办的工作流（请假、报销、IT 工单所有部门都能办；采购、客户跟进只在对应部门）→ 可以“开始整理”，
  生成一份 AI 工作成果（复用 automation_work_service，核对与保存规则完全一样）；
- 当前部门办不了的 → 标为“需要其他部门办理”，给出能转交的部门助手；
- 入职/调岗/离职这类人事事项没有 AI 工作流，标为由人事部门在人事台发起。
计划本身不写任何业务数据，状态由各步骤关联的工作成果实时汇总。
"""
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from models.init_db import AutomationWork, OrchestrationPlan, OrchestrationStep
from service.department_access import DEPARTMENT_LABELS, require_team_member_async
from service.exceptions import InvalidInput, NotFound
from service.workflows import get_workflow
from utils.timeutil import utcnow

# (类型, 负责部门, 关键词)。顺序就是优先级：同一句命中多类时取命中词最多的，相同时取靠前的。
RULES: List[Tuple[str, Optional[str], Tuple[str, ...]]] = [
    ("hr_case", "hr", ("入职", "离职", "转正", "调岗", "办理入职", "办离职")),
    ("leave", None, ("请假", "年假", "病假", "事假", "休假", "调休")),
    ("expense", None, ("报销", "发票", "差旅费", "打车费", "住宿费", "餐费", "高铁票", "机票", "火车票")),
    ("procurement", "procurement", ("采购", "购买", "买一批", "买些", "订购", "办公用品", "SKU")),
    ("ticket", "it", ("电脑", "笔记本", "账号", "权限", "密码", "打印机", "网络", "VPN", "邮箱", "显示器",
                      "故障", "报修", "开通", "系统登录", "蓝屏")),
    ("crm", "sales", ("客户", "跟进", "拜访", "商机", "回访")),
]
KIND_LABELS = {"hr_case": "人事事项", "leave": "请假", "expense": "报销", "procurement": "采购申请",
               "ticket": "IT 工单", "crm": "客户跟进"}
_STRONG = re.compile(r"[。；;！!？?\n]+|(?:另外|此外|同时|还要|还需要|还得|以及|并且|顺便|然后)")
_COMMA = re.compile(r"[，,]+")


def split_clauses(text: str) -> List[str]:
    """先按句号、分号和“另外/还要”等连接词切；一句话里逗号前后明显是不同类事务（如“请年假，报销打车费”）才继续按逗号切，
    否则整句保留（“高铁票 260 元，发票号 G001”是同一笔费用，不能拆开）。"""
    result: List[str] = []
    for piece in _STRONG.split(text or ""):
        piece = piece.strip(" ，,、：:")
        if len(piece) < 2:
            continue
        parts = [p.strip(" ，,、：:") for p in _COMMA.split(piece) if p.strip(" ，,、：:")]
        kinds = {classify(p)[0] for p in parts} - {None}
        if len(kinds) < 2:
            result.append(piece)
            continue
        pending = ""
        for part in parts:
            if classify(part)[0] is None:
                pending += ("，" if pending else "") + part      # 没有命中的并入后面第一个命中的
                continue
            result.append((pending + "，" if pending else "") + part)
            pending = ""
        if pending:
            result[-1] += "，" + pending
    return result


def classify(clause: str) -> Tuple[Optional[str], List[str]]:
    best, best_hits = None, []
    for kind, _, keywords in RULES:
        hits = [k for k in keywords if k.lower() in clause.lower()]
        if len(hits) > len(best_hits):
            best, best_hits = kind, hits
    return best, best_hits


def plan_steps(text: str) -> List[Dict[str, Any]]:
    """拆分 + 归类；相邻同类句子合并为一步，没有命中的句子并入上一步作为补充说明。"""
    steps: List[Dict[str, Any]] = []
    leading = ""   # 开头没有命中的句子，并入后面第一个命中的步骤，不丢
    for clause in split_clauses(text):
        kind, hits = classify(clause)
        if kind is None:
            if steps:
                steps[-1]["clause"] += "；" + clause
            else:
                leading += ("；" if leading else "") + clause
            continue
        if leading:
            clause, leading = leading + "；" + clause, ""
        # 入转调离的办理清单本身就含开通账号、配发设备等 IT 任务：紧跟在人事事项后面的 IT 需求并入，不另开工单
        if steps and (steps[-1]["kind"] == kind or (steps[-1]["kind"] == "hr_case" and kind == "ticket")):
            steps[-1]["clause"] += "；" + clause
            steps[-1]["hits"] = sorted(set(steps[-1]["hits"]) | set(hits))
            continue
        steps.append({"kind": kind, "clause": clause, "hits": hits})
    for step in steps:
        department = next(d for k, d, _ in RULES if k == step["kind"])
        step["department_code"] = department
        step["reason"] = f"命中“{'、'.join(step['hits'])}” → {KIND_LABELS[step['kind']]}"
    return steps


# ---------------------------------------------------------------- 持久化与状态

async def _team_code(db, team_id: int) -> Optional[str]:
    from sqlalchemy import text
    return (await db.execute(text("SELECT department_code FROM teams WHERE id = :t"), {"t": team_id})).scalar()


def _availability(kind: Optional[str], team_code: Optional[str]) -> Tuple[str, str]:
    """(state, 说明)：pending 当前部门可以直接整理；handoff 需要其他部门办理。"""
    if kind == "hr_case":
        return "handoff", "入转调离由人事部门在“人事办理”里发起，这里只记录需求"
    workflow = get_workflow(kind)
    if workflow.available_for(team_code):
        return "pending", f"在本部门整理为{workflow.draft_name}草稿，核对后保存"
    return "handoff", f"「{workflow.name}」仅对{workflow.availability_label()}开放，需要转交"


async def _agents_by_department(db, user_id: int) -> Dict[str, Dict[str, Any]]:
    from service.enterprise_workspace_service import get_workspace
    workspace = await get_workspace(db, user_id)
    result: Dict[str, Dict[str, Any]] = {}
    for agent in workspace["agents"]:
        if agent["agent_type"] == "department" and agent["department_code"] and agent["department_code"] not in result:
            result[agent["department_code"]] = {"id": agent["id"], "name": agent["name"], "team_name": agent["team_name"]}
    central = next((a for a in workspace["agents"] if a["agent_type"] == "central"), None)
    if central:
        result["__central__"] = {"id": central["id"], "name": central["name"]}
    return result


def _where(step: OrchestrationStep) -> str:
    """这一步在哪里办：本部门提交（由哪个部门处理），或需要其他部门办理。"""
    label = DEPARTMENT_LABELS.get(step.department_code) if step.department_code else None
    if step.state == "handoff":
        return f"需{label}部门办理" if label else "需其他部门办理"
    return f"本部门提交，{label}部门处理" if label else "本部门办理"


async def _payload(db, plan: OrchestrationPlan, user_id: int) -> Dict[str, Any]:
    steps = (await db.execute(select(OrchestrationStep).where(OrchestrationStep.plan_id == plan.id)
                              .order_by(OrchestrationStep.seq))).scalars().all()
    work_ids = [s.automation_work_id for s in steps if s.automation_work_id]
    works = {w.id: w for w in (await db.execute(select(AutomationWork).where(AutomationWork.id.in_(work_ids)))).scalars().all()} \
        if work_ids else {}
    agents = await _agents_by_department(db, user_id)
    items, done = [], 0
    for s in steps:
        work = works.get(s.automation_work_id) if s.automation_work_id else None
        status = work.status if work else s.state           # linked → 工作成果的真实状态
        if status in ("applied", "skipped"):
            done += 1
        handoff_agent = (agents.get(s.department_code) or agents.get("__central__")) if s.state == "handoff" else None
        items.append({
            "id": s.id, "seq": s.seq, "kind": s.kind, "kind_label": KIND_LABELS.get(s.kind, s.kind),
            "department_code": s.department_code,
            "department_label": _where(s),
            "clause": s.clause, "reason": s.reason, "state": s.state, "status": status,
            "automation_work_id": s.automation_work_id,
            "error_message": work.error_message if work else None,
            "business_result": json.loads(work.business_result_json) if work and work.business_result_json else None,
            "handoff_agent": handoff_agent,
        })
    actionable = [i for i in items if i["state"] != "handoff"]
    return {"id": plan.id, "team_id": plan.team_id, "source_text": plan.source_text, "status": plan.status,
            "created_at": plan.created_at.isoformat() + "Z", "steps": items,
            "progress": {"done": done, "total": len(actionable), "handoff": len(items) - len(actionable)}}


async def create_plan(db, user_id: int, team_id: int, text: str) -> Dict[str, Any]:
    await require_team_member_async(db, user_id, team_id, "leave")   # 只校验成员身份；各步骤的模块权限在整理时再校验
    text = (text or "").strip()
    if len(text) < 6:
        raise InvalidInput("请描述要办的事情")
    steps = plan_steps(text)
    if not steps:
        raise InvalidInput("没有识别出需要哪个部门办理，请写得具体一些（例如：请假、报销、电脑坏了、采购、客户跟进）")
    team_code = await _team_code(db, team_id)
    plan = OrchestrationPlan(user_id=user_id, team_id=team_id, source_text=text[:5000], status="open")
    db.add(plan)
    await db.flush()
    for seq, step in enumerate(steps, start=1):
        state, note = _availability(step["kind"], team_code)
        db.add(OrchestrationStep(plan_id=plan.id, seq=seq, kind=None if step["kind"] == "hr_case" else step["kind"],
                                 department_code=step["department_code"], clause=step["clause"][:2000],
                                 reason=(step["reason"] + "；" + note)[:300], state=state))
    await db.commit()
    return await _payload(db, plan, user_id)


async def _own_plan(db, user_id: int, plan_id: int) -> OrchestrationPlan:
    plan = await db.get(OrchestrationPlan, plan_id)
    if plan is None or plan.user_id != user_id:
        raise NotFound("协同计划不存在")
    await require_team_member_async(db, user_id, plan.team_id, "leave")
    return plan


async def get_plan(db, user_id: int, plan_id: int) -> Dict[str, Any]:
    return await _payload(db, await _own_plan(db, user_id, plan_id), user_id)


async def list_plans(db, user_id: int, team_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    await require_team_member_async(db, user_id, team_id, "leave")
    plans = (await db.execute(select(OrchestrationPlan).where(OrchestrationPlan.user_id == user_id,
                                                               OrchestrationPlan.team_id == team_id)
                              .order_by(OrchestrationPlan.id.desc()).limit(limit))).scalars().all()
    return [await _payload(db, p, user_id) for p in plans]


async def _step(db, plan: OrchestrationPlan, step_id: int) -> OrchestrationStep:
    step = await db.get(OrchestrationStep, step_id)
    if step is None or step.plan_id != plan.id:
        raise NotFound("步骤不存在")
    return step


async def update_step(db, user_id: int, plan_id: int, step_id: int, kind: Optional[str] = None,
                      clause: Optional[str] = None, skip: Optional[bool] = None) -> Dict[str, Any]:
    """整理之前可以改类型、改原文或跳过；已经开始整理的步骤以工作成果为准，不能再改。"""
    plan = await _own_plan(db, user_id, plan_id)
    step = await _step(db, plan, step_id)
    if step.automation_work_id:
        raise InvalidInput("这一步已经开始整理，请在工作成果里核对或修改")
    if skip is not None:
        step.state = "skipped" if skip else _availability(step.kind or "hr_case", await _team_code(db, plan.team_id))[0]
    if clause is not None:
        if len(clause.strip()) < 2:
            raise InvalidInput("原文不能为空")
        step.clause = clause.strip()[:2000]
    if kind is not None:
        if kind not in KIND_LABELS:
            raise InvalidInput("不支持的类型")
        state, note = _availability(kind, await _team_code(db, plan.team_id))
        step.kind = None if kind == "hr_case" else kind
        step.department_code = next(d for k, d, _ in RULES if k == kind)
        step.state = state
        step.reason = f"已手动改为{KIND_LABELS[kind]}；{note}"[:300]
    plan.updated_at = utcnow()
    await db.commit()
    return await _payload(db, plan, user_id)


async def start_step(db, user_id: int, plan_id: int, step_id: int, model_name: str,
                     customer_id: Optional[int] = None) -> Dict[str, Any]:
    """把这一步交给对应的 AI 整理工作流（权限、额度、核对规则与单独整理完全一样）。"""
    from service import automation_work_service as works
    plan = await _own_plan(db, user_id, plan_id)
    step = await _step(db, plan, step_id)
    if step.state != "pending" or step.automation_work_id:
        raise InvalidInput("这一步不能在本部门整理（已整理、已跳过或需要其他部门办理）")
    source = step.clause if len(step.clause) >= 10 else f"{step.clause}（来自：{plan.source_text[:300]}）"

    class Request:   # 与 FasdtApi.automation_work.GenerateRequest 同形；请求编号由步骤决定，重复点击不会重复整理
        pass
    req = Request()
    req.request_key = uuid.uuid5(uuid.NAMESPACE_URL, f"orchestration-step-{step.id}")
    req.team_id, req.kind, req.model_name, req.source_text = plan.team_id, step.kind, model_name, source[:15000]
    req.customer_id, req.sensitivity = customer_id, "internal"
    work = await works.generate(db, user_id, req)
    step.automation_work_id = work["id"]
    step.state = "linked"
    plan.updated_at = utcnow()
    await db.commit()
    return await _payload(db, plan, user_id)
