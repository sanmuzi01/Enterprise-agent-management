"""统一提效事实表（productivity_fact）与仪表盘。

各个来源的原始记录各自在自己的表里，这里定期（以及打开仪表盘时，最多每 5 分钟一次）把它们整理成同一种“事实”，
按 source_key 幂等写入——重复整理不会重复计数：

  automation       AI 整理成果（材料 → 业务草稿）：草稿 = 整理成功；采纳 = 保存成业务草稿；原样采纳 = 没改字段；
                   完成 = 已保存且后续待办都办完
  tool_confirm     Agent 请求的正式业务操作（高风险工具确认单）：发起 = 员工确认执行；完成 = 执行成功
  invoice          发票识别：采纳 = 员工确认；原样 = 没改字段；完成 = 用于报销
  crm_suggestion   CRM 下一步建议：采纳 = 创建任务；原样 = 没改直接创建；完成 = 待办办完
  it_self_service  IT 自助：采纳 / 完成 = 员工明确点“已解决”且没有重新打开
  agent_run        Agent 运行：只用来算失败率和失败原因

指标定义（和需求一致）：
  节省时间 = 基准分钟 − Agent 用时 − 人工核对用时，最低为 0（只算采纳 / 完成的）
  草稿采纳率 = 被确认的草稿 ÷ Agent 生成的草稿；原样采纳率 = 没改字段直接确认的 ÷ 被确认的
  业务完成率 = 最终完成的 ÷ Agent 发起的正式业务；失败率 = 失败的 AgentRun ÷ 全部 AgentRun
三种时间分开展示、不相加：估算节省时间（按基准）、实测用时（Agent + 人工核对的真实耗时）、用户反馈节省时间。
"""
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert

from models.init_db import (AgentRun, AutomationWork, CrmActionSuggestion, InvoiceExtraction, ItSelfServiceSession,
                            ProductivityFact, Team, ToolConfirmation, WorkItem)
from utils.logger_handler import get_logger
from utils.timeutil import utcnow

logger = get_logger("productivity")

DEFAULT_BASELINES = {"invoice": 5.0, "crm_suggestion": 3.0, "it_self_service": 20.0, "tool_confirm": 5.0}
SOURCE_LABELS = {"automation": "AI 材料整理", "tool_confirm": "助手办理的业务", "invoice": "发票识别",
                 "crm_suggestion": "CRM 下一步建议", "it_self_service": "IT 自助解决", "agent_run": "助手运行"}
REFRESH_INTERVAL = 300
_last_refresh = {"at": 0.0}


def baselines() -> Dict[str, float]:
    """各来源手工办理一次的基准分钟。可用 PRODUCTIVITY_BASELINES（JSON）覆盖；AI 整理成果用各工作流自己的基准。"""
    values = dict(DEFAULT_BASELINES)
    try:
        values.update({k: float(v) for k, v in json.loads(os.getenv("PRODUCTIVITY_BASELINES", "{}")).items()})
    except (ValueError, TypeError):
        logger.warning("PRODUCTIVITY_BASELINES 格式不对，使用默认基准")
    return values


def saved(baseline: float, agent_seconds: float, review_seconds: float, counted: bool) -> float:
    if not counted:
        return 0.0
    return round(max(0.0, baseline - agent_seconds / 60 - review_seconds / 60), 2)


def _seconds(start: Optional[datetime], end: Optional[datetime]) -> float:
    if not start or not end:
        return 0.0
    return max(0.0, (end - start).total_seconds())


def _changed_fields(proposal: Optional[str], accepted: Optional[str]) -> List[str]:
    try:
        before, after = json.loads(proposal or "{}"), json.loads(accepted or "{}")
    except ValueError:
        return []
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


# ------------------------------------------------------------------ 各来源 → 事实

def from_automation(work: AutomationWork, baseline: float) -> Dict[str, Any]:
    applied = work.status == "applied"
    drafted = work.status in ("ready", "applied")
    result = json.loads(work.business_result_json or "{}") or {}
    done_tasks = json.loads(work.completed_tasks_json or "[]")
    followups = []
    try:
        from service.workflows import get_workflow
        definition = get_workflow(work.kind)
        if definition.followups and work.accepted_json:
            followups = json.loads(work.accepted_json).get(definition.followups["key"]) or []
    except Exception:  # noqa: BLE001 —— 工作流下线了也照样统计
        followups = []
    completed = applied and len(done_tasks) >= len(followups)
    agent_s = (work.elapsed_ms or 0) / 1000
    review_s = max(0.0, _seconds(work.created_at, work.applied_at) - agent_s) if applied else 0.0
    changed = _changed_fields(work.proposal_json, work.accepted_json) if applied else []
    return dict(source_key=f"automation:{work.id}", source_type="automation", workflow_type=work.kind,
                user_id=work.user_id, team_id=work.team_id, agent_id=None,
                business_object_type="draft", business_object_id=str(result.get("id") or "") or None,
                baseline_minutes=baseline, agent_seconds=agent_s, review_seconds=review_s,
                saved_minutes=saved(baseline, agent_s, review_s, applied),
                draft_created=int(drafted), draft_adopted=int(applied), adopted_as_is=int(applied and not work.edited),
                business_initiated=int(applied), completed=int(completed), failed=int(work.status == "failed"),
                failure_code="AUTOMATION_FAILED" if work.status == "failed" else None,
                fields_changed_json=json.dumps(changed, ensure_ascii=False) if changed else None,
                started_at=work.created_at, completed_at=work.applied_at)


def from_tool_confirmation(row: ToolConfirmation, baseline: float, team_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if row.status not in ("confirmed", "rejected", "expired"):
        return None
    confirmed = row.status == "confirmed"
    ok = confirmed and '"error"' not in (row.result or "")
    review_s = _seconds(row.created_at, row.decided_at) if row.decided_at else 0.0
    return dict(source_key=f"tool_confirm:{row.id}", source_type="tool_confirm", workflow_type=row.tool_name,
                user_id=row.user_id, team_id=team_id, agent_id=row.agent_id,
                business_object_type="tool_call", business_object_id=None, baseline_minutes=baseline,
                agent_seconds=0.0, review_seconds=review_s, saved_minutes=saved(baseline, 0, review_s, ok),
                draft_created=1, draft_adopted=int(confirmed), adopted_as_is=int(confirmed),
                business_initiated=int(confirmed), completed=int(ok), failed=int(confirmed and not ok),
                failure_code="TOOL_EXECUTION_FAILED" if confirmed and not ok else None, fields_changed_json=None,
                started_at=row.created_at, completed_at=row.decided_at)


def from_invoice(row: InvoiceExtraction, baseline: float) -> Dict[str, Any]:
    adopted = row.status in ("confirmed", "used")
    corrected = json.loads(row.corrected_fields_json or "[]")
    review_s = _seconds(row.created_at, row.confirmed_at) if adopted else 0.0
    return dict(source_key=f"invoice:{row.id}", source_type="invoice", workflow_type="invoice_extraction",
                user_id=row.user_id, team_id=row.team_id, agent_id=None, business_object_type="expense_claim",
                business_object_id=str(row.claim_id) if row.claim_id else None, baseline_minutes=baseline,
                agent_seconds=0.0, review_seconds=review_s, saved_minutes=saved(baseline, 0, review_s, adopted),
                draft_created=1, draft_adopted=int(adopted), adopted_as_is=int(adopted and not corrected),
                business_initiated=int(row.status == "used"), completed=int(row.status == "used"), failed=0,
                failure_code=None, fields_changed_json=json.dumps(corrected) if corrected else None,
                started_at=row.created_at, completed_at=row.confirmed_at)


def from_crm_suggestion(row: CrmActionSuggestion, baseline: float, work_done: bool) -> Dict[str, Any]:
    created = row.status == "created"
    review_s = _seconds(row.created_at, row.decided_at) if row.decided_at else 0.0
    return dict(source_key=f"crm_suggestion:{row.id}", source_type="crm_suggestion", workflow_type="crm_next_action",
                user_id=row.decided_by, team_id=row.team_id, agent_id=None, business_object_type="work_item",
                business_object_id=row.work_item_key, baseline_minutes=baseline, agent_seconds=0.0,
                review_seconds=review_s, saved_minutes=saved(baseline, 0, review_s, created),
                draft_created=1, draft_adopted=int(created),
                adopted_as_is=int(created and row.decision == "create"),
                business_initiated=int(created), completed=int(created and work_done), failed=0, failure_code=None,
                fields_changed_json=None, started_at=row.created_at, completed_at=row.decided_at)


def from_it_session(row: ItSelfServiceSession, baseline: float) -> Optional[Dict[str, Any]]:
    if not json.loads(row.article_ids_json or "[]"):
        return None
    solved = row.confirmed_solved == 1 and not row.reopened
    return dict(source_key=f"it_self_service:{row.id}", source_type="it_self_service", workflow_type="it_self_service",
                user_id=row.user_id, team_id=row.team_id, agent_id=None, business_object_type="it_ticket",
                business_object_id=str(row.converted_ticket_id) if row.converted_ticket_id else None,
                baseline_minutes=baseline, agent_seconds=0.0,
                review_seconds=_seconds(row.started_at, row.feedback_at) if row.feedback_at else 0.0,
                saved_minutes=saved(baseline, 0, _seconds(row.started_at, row.feedback_at), solved),
                draft_created=1, draft_adopted=int(row.confirmed_solved == 1), adopted_as_is=int(row.confirmed_solved == 1),
                business_initiated=1, completed=int(solved), failed=0,
                failure_code="REOPENED" if row.reopened else None, fields_changed_json=None,
                started_at=row.started_at, completed_at=row.feedback_at)


def failure_code(row: AgentRun) -> str:
    """优先用运行时记下的统一错误码；早期没有错误码的，按错误信息归类，归不了类才算“原因未知”。"""
    if row.error_code:
        return row.error_code
    if row.status == "max_iter":
        return "MAX_ITERATIONS"
    message = (row.error_msg or "").lower()
    for code, words in (("MODEL_TIMEOUT", ("超时", "timeout", "timed out")),
                        ("MODEL_NOT_CONFIGURED", ("api key", "api_key", "未配置", "请先在")),
                        ("JAVA_SERVICE_UNAVAILABLE", ("业务系统", "enterprise hub", "8090", "connection refused")),
                        ("MODEL_UNAVAILABLE", ("rate limit", "429", "503", "502", "模型服务"))):
        if any(w in message for w in words):
            return code
    return "UNKNOWN"


def from_agent_run(row: AgentRun, team_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if row.status == "running":
        return None
    failed = row.status in ("failed", "max_iter")
    return dict(source_key=f"agent_run:{row.id}", source_type="agent_run", workflow_type="agent_run",
                user_id=row.user_id, team_id=team_id, agent_id=row.agent_id, business_object_type=None,
                business_object_id=None, baseline_minutes=0.0, agent_seconds=_seconds(row.started_at, row.finished_at),
                review_seconds=0.0, saved_minutes=0.0, draft_created=0, draft_adopted=0, adopted_as_is=0,
                business_initiated=0, completed=int(not failed), failed=int(failed),
                failure_code=failure_code(row) if failed else None,
                fields_changed_json=None, started_at=row.started_at, completed_at=row.finished_at)


# ------------------------------------------------------------------ 整理（幂等）

async def _user_teams(db, user_ids: Iterable[int]) -> Dict[int, int]:
    from models.init_db import TeamMember
    ids = [i for i in set(user_ids) if i]
    if not ids:
        return {}
    rows = (await db.execute(select(TeamMember.user_id, TeamMember.team_id).where(
        TeamMember.user_id.in_(ids), TeamMember.status == "active"))).all()
    return {u: t for u, t in rows}


async def _org_of(db, team_ids: Iterable[int]) -> Dict[int, int]:
    ids = [i for i in set(team_ids) if i]
    if not ids:
        return {}
    return {t: o for t, o in (await db.execute(select(Team.id, Team.organization_id).where(Team.id.in_(ids)))).all()}


async def _write(db, facts: List[Dict[str, Any]]) -> int:
    if not facts:
        return 0
    orgs = await _org_of(db, [f["team_id"] for f in facts])
    for fact in facts:
        fact["organization_id"] = orgs.get(fact["team_id"])
        fact["updated_at"] = utcnow()
        stmt = insert(ProductivityFact).values(**fact)
        updates = {k: stmt.inserted[k] for k in fact if k not in ("source_key", "reported_saved_minutes")}
        await db.execute(stmt.on_duplicate_key_update(**updates))
    await db.commit()
    return len(facts)


async def refresh(db, days: int = 90, force: bool = False) -> int:
    """把最近 days 天的原始记录整理成事实。默认最多 5 分钟整理一次（打开仪表盘时触发）。"""
    if not force and time.monotonic() - _last_refresh["at"] < REFRESH_INTERVAL:
        return 0
    since = utcnow() - timedelta(days=days)
    base = baselines()
    facts: List[Dict[str, Any]] = []
    from service.workflows import get_workflow
    for work in (await db.execute(select(AutomationWork).where(AutomationWork.created_at >= since))).scalars():
        try:
            minutes = float(get_workflow(work.kind).baseline_minutes)
        except Exception:  # noqa: BLE001
            minutes = 10.0
        facts.append(from_automation(work, minutes))
    confirmations = list((await db.execute(select(ToolConfirmation).where(ToolConfirmation.created_at >= since))).scalars())
    runs = list((await db.execute(select(AgentRun).where(AgentRun.started_at >= since))).scalars())
    teams = await _user_teams(db, [r.user_id for r in confirmations] + [r.user_id for r in runs])
    for row in confirmations:
        fact = from_tool_confirmation(row, base["tool_confirm"], teams.get(row.user_id))
        if fact:
            facts.append(fact)
    for row in runs:
        fact = from_agent_run(row, teams.get(row.user_id))
        if fact:
            facts.append(fact)
    for row in (await db.execute(select(InvoiceExtraction).where(InvoiceExtraction.created_at >= since,
                                                                 InvoiceExtraction.status != "discarded"))).scalars():
        facts.append(from_invoice(row, base["invoice"]))
    suggestions = list((await db.execute(select(CrmActionSuggestion).where(
        CrmActionSuggestion.created_at >= since, CrmActionSuggestion.status.in_(("created", "ignored"))))).scalars())
    keys = [s.work_item_key for s in suggestions if s.work_item_key]
    done = set((await db.execute(select(WorkItem.source_key).where(WorkItem.source_key.in_(keys), WorkItem.status == "done"))).scalars()) if keys else set()
    for row in suggestions:
        if row.decided_by:
            facts.append(from_crm_suggestion(row, base["crm_suggestion"], row.work_item_key in done))
    for row in (await db.execute(select(ItSelfServiceSession).where(ItSelfServiceSession.started_at >= since))).scalars():
        fact = from_it_session(row, base["it_self_service"])
        if fact:
            facts.append(fact)
    written = await _write(db, facts)
    # 只有完整整理并提交成功后才进入冷却；数据库瞬时故障时下一次请求可以立即重试。
    _last_refresh["at"] = time.monotonic()
    return written


async def report_saved(db, user_id: int, source_key: str, minutes: float) -> Dict[str, Any]:
    """员工自己反馈“这次大概省了多少分钟”。单独存，不和估算值相加。"""
    from service.exceptions import InvalidInput, NotFound
    if not 0 <= minutes <= 600:
        raise InvalidInput("节省时间请填 0～600 分钟")
    await refresh(db, force=True)
    row = (await db.execute(select(ProductivityFact).where(ProductivityFact.source_key == source_key,
                                                           ProductivityFact.user_id == user_id))).scalar_one_or_none()
    if row is None:
        raise NotFound("找不到这条记录")
    row.reported_saved_minutes = round(float(minutes), 1)
    await db.commit()
    return {"source_key": source_key, "reported_saved_minutes": row.reported_saved_minutes}


# ------------------------------------------------------------------ 仪表盘

def _rate(a: float, b: float) -> Optional[float]:
    return round(a / b, 4) if b else None


def summarize(facts: List[Any], names: Dict[str, Dict[int, str]]) -> Dict[str, Any]:
    """纯函数：事实 → 仪表盘数据。facts 有 ProductivityFact 的属性。"""
    work = [f for f in facts if f.source_type != "agent_run"]
    runs = [f for f in facts if f.source_type == "agent_run"]
    drafts = sum(f.draft_created for f in work)
    adopted = sum(f.draft_adopted for f in work)
    as_is = sum(f.adopted_as_is for f in work)
    initiated = sum(f.business_initiated for f in work)
    completed = sum(f.completed for f in work if f.business_initiated)
    reported = [f.reported_saved_minutes for f in work if f.reported_saved_minutes is not None]

    def group(key, label_of):
        buckets: Dict[Any, Dict[str, Any]] = {}
        for f in facts:
            k = key(f)
            if k is None:
                continue
            b = buckets.setdefault(k, {"id": k, "name": label_of(k), "items": 0, "saved_minutes": 0.0, "adopted": 0,
                                       "drafts": 0, "runs": 0, "failed_runs": 0})
            if f.source_type == "agent_run":
                b["runs"] += 1
                b["failed_runs"] += f.failed
            else:
                b["items"] += 1
                b["saved_minutes"] += f.saved_minutes or 0
                b["adopted"] += f.draft_adopted
                b["drafts"] += f.draft_created
        for b in buckets.values():
            b["saved_minutes"] = round(b["saved_minutes"], 1)
            b["adoption_rate"] = _rate(b["adopted"], b["drafts"])
            b["failure_rate"] = _rate(b["failed_runs"], b["runs"])
        return sorted(buckets.values(), key=lambda b: (-b["saved_minutes"], -b["items"]))

    trend: Dict[str, Dict[str, float]] = defaultdict(lambda: {"items": 0, "saved_minutes": 0.0, "failed_runs": 0, "runs": 0})
    for f in facts:
        if not f.started_at:
            continue
        day = (f.started_at + timedelta(hours=8)).date().isoformat()
        if f.source_type == "agent_run":
            trend[day]["runs"] += 1
            trend[day]["failed_runs"] += f.failed
        else:
            trend[day]["items"] += 1
            trend[day]["saved_minutes"] = round(trend[day]["saved_minutes"] + (f.saved_minutes or 0), 1)
    fields = Counter()
    for f in work:
        for name in json.loads(f.fields_changed_json or "[]"):
            fields[name] += 1
    return {
        "totals": {
            "items": len(work), "active_users": len({f.user_id for f in facts if f.user_id}),
            "estimated_saved_minutes": round(sum(f.saved_minutes or 0 for f in work), 1),
            "measured_minutes": round(sum((f.agent_seconds or 0) + (f.review_seconds or 0) for f in work) / 60, 1),
            "reported_saved_minutes": round(sum(reported), 1), "reported_count": len(reported),
            "drafts": drafts, "adopted": adopted, "adopted_as_is": as_is, "initiated": initiated, "completed": completed,
            "runs": len(runs), "failed_runs": sum(f.failed for f in runs),
        },
        "rates": {"adoption": _rate(adopted, drafts), "as_is": _rate(as_is, adopted), "completion": _rate(completed, initiated),
                  "failure": _rate(sum(f.failed for f in runs), len(runs))},
        "by_team": group(lambda f: f.team_id, lambda k: names["team"].get(k, f"部门 {k}")),
        "by_agent": [b for b in group(lambda f: f.agent_id, lambda k: names["agent"].get(k, f"助手 {k}")) if b["id"]],
        "by_source": group(lambda f: f.source_type, lambda k: SOURCE_LABELS.get(k, k)),
        "trend": [{"day": d, **v} for d, v in sorted(trend.items())],
        "failure_reasons": [{"code": c, "count": n} for c, n in Counter(f.failure_code for f in facts if f.failure_code).most_common(10)],
        "changed_fields": [{"field": k, "count": n} for k, n in fields.most_common(15)],
    }


async def dashboard(db, team_ids: Optional[List[int]] = None, days: int = 30) -> Dict[str, Any]:
    from models.init_db import Agent
    await refresh(db, days=max(days, 30))
    since = utcnow() - timedelta(days=days)
    query = select(ProductivityFact).where(ProductivityFact.started_at >= since)
    if team_ids is not None:
        query = query.where(ProductivityFact.team_id.in_(team_ids or [-1]))
    facts = list((await db.execute(query)).scalars().all())
    team_names = {t: n for t, n in (await db.execute(select(Team.id, Team.name).where(
        Team.id.in_({f.team_id for f in facts if f.team_id} or {-1})))).all()}
    agent_names = {a: n for a, n in (await db.execute(select(Agent.id, Agent.name).where(
        Agent.id.in_({f.agent_id for f in facts if f.agent_id} or {-1})))).all()}
    out = summarize(facts, {"team": team_names, "agent": agent_names})
    # 趋势补齐没有数据的日子，图上的时间轴是连续的
    by_day = {t["day"]: t for t in out["trend"]}
    today = (utcnow() + timedelta(hours=8)).date()
    out["trend"] = [by_day.get(d, {"day": d, "items": 0, "saved_minutes": 0.0, "runs": 0, "failed_runs": 0})
                    for d in ((today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1))]
    return {"days": days, **out}


async def department_dashboard(db, user_id: int, team_id: int, days: int = 30) -> Dict[str, Any]:
    """部门负责人 / 企业管理员看本部门。"""
    from models.enterprise_dao import is_team_admin_of_team_async
    from service import enterprise_access
    from service.exceptions import PermissionDenied
    if not (await is_team_admin_of_team_async(db, user_id, team_id) or await enterprise_access.is_org_admin_async(db, user_id)):
        raise PermissionDenied("只有部门负责人或企业管理员能看部门提效数据")
    return await dashboard(db, [team_id], days)
