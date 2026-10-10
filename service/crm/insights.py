"""客户时间线、增量摘要、风险扫描、下一步建议。

只读业务系统、只写本平台的分析结果：不会修改商机、不会给客户发任何东西；建议要销售确认后才进待办中心。
"""
import json
import os
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from models.init_db import (CrmActionSuggestion, CrmOpportunitySnapshot, CrmRiskFinding, CustomerActivity,
                            CustomerSummarySnapshot, WorkItem)
from service.crm import activities as acts
from service.crm import risk_rules
from service.department_access import require_team_member_async
from service.exceptions import Conflict, InvalidInput, NotFound
from utils.logger_handler import get_logger
from utils.timeutil import utcnow

logger = get_logger("crm_insights")

MAX_ACTIVITY_CHARS = 1500
MAX_NEW_ACTIVITIES = 30


def _today() -> date:
    return (utcnow() + timedelta(hours=8)).date()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


async def customer_detail(db, user_id: int, team_id: int, customer_id: int) -> Dict[str, Any]:
    """业务系统的客户摘要。业务系统按调用者部门校验：不是本部门的客户直接 404。"""
    await require_team_member_async(db, user_id, team_id, "crm", message="不属于该部门，无法查看客户")
    return await acts.hub_get(user_id, team_id, f"/crm/customers/{int(customer_id)}", "get_customer_summary")


# ------------------------------------------------------------------ 商机变化记录

async def capture_opportunities(db, team_id: int, customer_id: int, opportunities: List[Dict[str, Any]]) -> None:
    """和上一条记录比，阶段 / 金额 / 预计成交日期变了才记一条（阶段变化进时间线，金额下降和延期靠它判断）。"""
    for opp in opportunities or []:
        last = (await db.execute(select(CrmOpportunitySnapshot).where(
            CrmOpportunitySnapshot.opportunity_id == int(opp["id"])).order_by(CrmOpportunitySnapshot.id.desc()).limit(1))
        ).scalar_one_or_none()
        state = (opp.get("stage"), str(opp.get("amount")), opp.get("expectedCloseDate"))
        if last is not None and (last.stage, last.amount, last.expected_close_date) == state:
            continue
        db.add(CrmOpportunitySnapshot(team_id=team_id, customer_id=customer_id, opportunity_id=int(opp["id"]),
                                      stage=state[0], amount=state[1], expected_close_date=state[2]))
    await db.commit()


async def _history(db, customer_id: int) -> Dict[int, List[Dict[str, Any]]]:
    rows = (await db.execute(select(CrmOpportunitySnapshot).where(CrmOpportunitySnapshot.customer_id == customer_id)
                             .order_by(CrmOpportunitySnapshot.id))).scalars().all()
    history: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        history.setdefault(r.opportunity_id, []).append(
            {"stage": r.stage, "amount": r.amount, "expected_close_date": r.expected_close_date, "captured_at": r.captured_at})
    return history


# ------------------------------------------------------------------ 时间线

async def timeline(db, user_id: int, team_id: int, customer_id: int) -> Dict[str, Any]:
    detail = await customer_detail(db, user_id, team_id, customer_id)
    await capture_opportunities(db, team_id, customer_id, detail.get("opportunities") or [])
    items = [{**acts.payload(a), "kind": a.activity_type} for a in await acts.customer_activities(db, team_id, customer_id)]
    for f in detail.get("recentFollowUps") or []:
        items.append({"kind": "followup", "type_label": "人工跟进", "id": f"followup-{f['id']}", "title": "跟进记录",
                      "content": f.get("content"), "occurred_at": f.get("confirmedAt") or f.get("createdAt"),
                      "status": "已确认" if f.get("status") == "CONFIRMED" else "草稿", "source_provider": "crm"})
    history = await _history(db, customer_id)
    for oid, rows in history.items():
        for prev, cur in zip([None] + rows, rows):
            if prev is not None and prev["stage"] == cur["stage"]:
                continue
            label = risk_rules.STAGE_LABELS.get(cur["stage"], cur["stage"])
            items.append({"kind": "stage_change", "type_label": "商机阶段变化", "id": f"stage-{oid}-{cur['captured_at'].isoformat()}",
                          "title": f"商机 #{oid} " + (f"进入「{label}」" if prev else f"记录到阶段「{label}」"),
                          "content": f"金额 {cur['amount']}" + (f"，预计成交 {cur['expected_close_date']}" if cur["expected_close_date"] else ""),
                          "occurred_at": cur["captured_at"].isoformat() + "Z", "source_provider": "crm"})
    tasks = (await db.execute(select(CrmActionSuggestion).where(
        CrmActionSuggestion.team_id == team_id, CrmActionSuggestion.customer_id == customer_id,
        CrmActionSuggestion.status == "created"))).scalars().all()
    for s in tasks:
        items.append({"kind": "todo", "type_label": "待办", "id": f"todo-{s.id}", "title": s.title, "content": s.detail,
                      "occurred_at": (s.decided_at or s.created_at).isoformat() + "Z",
                      "due_date": s.due_date, "source_provider": "crm"})
    items.sort(key=lambda i: str(i.get("occurred_at") or ""), reverse=True)
    return {"customer": {k: detail.get(k) for k in ("id", "name", "industry", "ownerUserId", "contacts", "opportunities")},
            "items": items}


# ------------------------------------------------------------------ 模型

async def _check_model(db, user_id: int, model_name: Optional[str]) -> str:
    from service.data_egress_policy import is_model_allowed
    from service.llm.llm_config_service import async_get_api_config
    from service.llm.model_catalog import model_type
    if not model_name:
        raise InvalidInput("请选择一个模型")
    if model_type(model_name) != "chat":
        raise InvalidInput("请选择对话模型")
    if not is_model_allowed(model_name, "internal"):
        raise InvalidInput("按企业的数据外发策略，这个模型不能处理客户资料")
    if not await async_get_api_config(db, user_id, model_name):
        raise InvalidInput(f"还没有配置 {model_name} 的连接，请先在设置里连接模型")
    return model_name


async def _ask_json(db, user_id: int, model_name: str, system: str, message: str) -> Dict[str, Any]:
    from service.llm.llm_service import async_chat_with_usage
    answer, _usage = await async_chat_with_usage(db, user_id, model_name, system, [], message, temperature=0)
    match = re.search(r"\{.*\}", answer or "", re.S)
    if not match:
        raise InvalidInput("模型没有按要求返回结果，请重试")
    try:
        data = json.loads(match.group(0))
    except ValueError:
        raise InvalidInput("模型返回的结果格式不对，请重试") from None
    if not isinstance(data, dict):
        raise InvalidInput("模型返回的结果格式不对，请重试")
    return data


def _activity_text(a: CustomerActivity) -> str:
    when = (a.occurred_at + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
    return (f"[活动 {a.id}｜{acts.TYPE_LABELS.get(a.activity_type, a.activity_type)}｜{when}] {a.title or ''}\n"
            f"{(a.content or '')[:MAX_ACTIVITY_CHARS]}")


def _strings(value: Any, limit: int = 10) -> List[str]:
    items = value if isinstance(value, list) else []
    return [str(i).strip()[:300] for i in items if str(i).strip()][:limit]


SUMMARY_SYSTEM = """你是企业销售助手，负责维护一个客户的摘要。只依据给出的资料，不编造。
输出一个 JSON 对象，不要输出其他内容：
{"summary": "200 字以内的客户现状", "needs": ["客户需求"], "stakeholders": ["姓名（职务/角色/态度）"],
 "risks": ["风险（写明依据）"], "next_actions": ["具体的下一步行动"]}"""


# ------------------------------------------------------------------ 摘要

def _snapshot_payload(row: Optional[CustomerSummarySnapshot]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {"id": row.id, "summary": row.summary, "needs": json.loads(row.needs_json or "[]"),
            "stakeholders": json.loads(row.stakeholders_json or "[]"), "risks": json.loads(row.risks_json or "[]"),
            "next_actions": json.loads(row.next_actions_json or "[]"), "based_on_activity_id": row.based_on_activity_id,
            "generated_at": row.generated_at.isoformat() + "Z", "model_name": row.model_name}


async def latest_summary(db, team_id: int, customer_id: int) -> Optional[CustomerSummarySnapshot]:
    return (await db.execute(select(CustomerSummarySnapshot).where(
        CustomerSummarySnapshot.team_id == team_id, CustomerSummarySnapshot.customer_id == customer_id)
        .order_by(CustomerSummarySnapshot.id.desc()).limit(1))).scalar_one_or_none()


async def get_summary(db, user_id: int, team_id: int, customer_id: int) -> Dict[str, Any]:
    await customer_detail(db, user_id, team_id, customer_id)        # 校验客户属于本部门
    snapshot = await latest_summary(db, team_id, customer_id)
    newest = (await db.execute(select(func.max(CustomerActivity.id)).where(
        CustomerActivity.team_id == team_id, CustomerActivity.customer_id == customer_id))).scalar()
    stale = bool(newest) and (snapshot is None or (snapshot.based_on_activity_id or 0) < newest)
    return {"snapshot": _snapshot_payload(snapshot), "has_new_activities": stale}


async def refresh_summary(db, user_id: int, team_id: int, customer_id: int, model_name: Optional[str]) -> Dict[str, Any]:
    """增量更新：上一版摘要 + 之后的新活动 → 新摘要。第一次生成时用业务系统的客户资料 + 最近的活动。
    只用本部门、这个客户的数据（别的部门对同一客户的记录看不到，也不会送给模型）。"""
    detail = await customer_detail(db, user_id, team_id, customer_id)
    model_name = await _check_model(db, user_id, model_name)
    previous = await latest_summary(db, team_id, customer_id)
    after = previous.based_on_activity_id or 0 if previous else 0
    new = await acts.customer_activities(db, team_id, customer_id, after_id=after, limit=MAX_NEW_ACTIVITIES)
    if previous is not None and not new:
        return {"snapshot": _snapshot_payload(previous), "unchanged": True}
    parts = []
    if previous is not None:
        parts.append("【上一版摘要】\n" + json.dumps(_snapshot_payload(previous), ensure_ascii=False)
                     .replace('"id": %d, ' % previous.id, ""))
    else:
        opps = [{k: o.get(k) for k in ("id", "stage", "amount", "expectedCloseDate", "nextStep")} for o in detail.get("opportunities") or []]
        parts.append("【客户资料】\n" + json.dumps({"name": detail.get("name"), "industry": detail.get("industry"),
                                                 "contacts": detail.get("contacts"), "opportunities": opps,
                                                 "followups": [f.get("content") for f in detail.get("recentFollowUps") or []][:10]},
                                                ensure_ascii=False, default=str))
    if new:
        parts.append("【新的客户活动】\n" + "\n\n".join(_activity_text(a) for a in new))
    data = await _ask_json(db, user_id, model_name, SUMMARY_SYSTEM, "\n\n".join(parts))
    summary = str(data.get("summary") or "").strip()[:2000]
    if not summary:
        raise InvalidInput("模型没有生成摘要，请重试")
    row = CustomerSummarySnapshot(
        team_id=team_id, customer_id=customer_id, summary=summary,
        needs_json=json.dumps(_strings(data.get("needs")), ensure_ascii=False),
        stakeholders_json=json.dumps(_strings(data.get("stakeholders")), ensure_ascii=False),
        risks_json=json.dumps(_strings(data.get("risks")), ensure_ascii=False),
        next_actions_json=json.dumps(_strings(data.get("next_actions"), 5), ensure_ascii=False),
        based_on_activity_id=new[-1].id if new else after, model_name=model_name, generated_by=user_id)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    for title in _strings(data.get("next_actions"), 5):
        await _suggest(db, team_id, customer_id, None, None, title, "来自客户摘要", f"summary:{team_id}:{customer_id}:{_digest(title)}")
    await db.commit()
    return {"snapshot": _snapshot_payload(row), "unchanged": False, "activities_used": len(new)}


def _digest(text: str) -> str:
    import hashlib
    return hashlib.sha1(re.sub(r"\s+", "", text).encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ 风险

RISK_SYSTEM = """你是企业销售助手。从给出的客户沟通记录里识别下面这些风险（没有就不要写）：
BUDGET_INSUFFICIENT 客户预算不足；DECISION_MAKER_ABSENT 决策人未参与；COMPETITOR 有竞争对手；
CAPABILITY_MISMATCH 产品能力不匹配；TIMELINE_CONFLICT 上线时间冲突。
evidence 必须逐字引用记录里的原话（不要改写），activity_id 写原话所在的活动编号。只输出 JSON：
{"risks": [{"risk_code": "...", "level": "high|medium|low", "activity_id": 1, "evidence": "原话", "suggested_action": "建议"}]}"""


def finding_payload(row: CrmRiskFinding) -> Dict[str, Any]:
    return {"id": row.id, "customer_id": row.customer_id, "opportunity_id": row.opportunity_id, "risk_code": row.risk_code,
            "label": risk_rules.RISK_LABELS.get(row.risk_code, row.risk_code), "level": row.level, "evidence": row.evidence,
            "suggested_action": row.suggested_action, "source": row.source, "status": row.status,
            "first_seen_at": row.first_seen_at.isoformat() + "Z", "last_seen_at": row.last_seen_at.isoformat() + "Z"}


async def scan_customer(db, user_id: int, team_id: int, customer_id: int, model_name: Optional[str] = None) -> Dict[str, Any]:
    """扫描一个客户：规则风险每次全量重算（不再成立的自动标为已解除）；传了模型时再让模型从最近的沟通里找文字风险。
    同一个风险只有一条记录、只生成一条建议，反复扫描不会重复提醒。"""
    detail = await customer_detail(db, user_id, team_id, customer_id)
    await capture_opportunities(db, team_id, customer_id, detail.get("opportunities") or [])
    rows = await acts.customer_activities(db, team_id, customer_id, limit=1000)
    activity_dicts = [{**acts.payload(a), "occurred_at": a.occurred_at} for a in rows]
    open_tasks = (await db.execute(select(func.count()).select_from(WorkItem).where(
        WorkItem.team_id == team_id, WorkItem.status == "open", WorkItem.source_type == "crm",
        WorkItem.source_key.like(f"crm-suggestion:{team_id}:{customer_id}:%")))).scalar() or 0
    risks = risk_rules.evaluate(
        _today(), detail, activity_dicts, await _history(db, customer_id), int(open_tasks),
        stale_days=_env_int("CRM_RISK_STALE_DAYS", 14), quote_wait_days=_env_int("CRM_RISK_QUOTE_WAIT_DAYS", 7),
        close_window_days=_env_int("CRM_RISK_CLOSE_WINDOW_DAYS", 7), stage_stale_days=_env_int("CRM_RISK_STAGE_STALE_DAYS", 14))
    if model_name:
        model_name = await _check_model(db, user_id, model_name)
        recent = rows[-15:]
        if recent:
            data = await _ask_json(db, user_id, model_name, RISK_SYSTEM, "\n\n".join(_activity_text(a) for a in recent))
            by_id = {d["id"]: d for d in activity_dicts}
            risks += risk_rules.grounded_model_risks(data, by_id)
    seen = await _save_findings(db, team_id, customer_id, risks)
    findings = (await db.execute(select(CrmRiskFinding).where(
        CrmRiskFinding.team_id == team_id, CrmRiskFinding.customer_id == customer_id, CrmRiskFinding.status == "open")
        .order_by(CrmRiskFinding.id))).scalars().all()
    return {"risks": [finding_payload(f) for f in findings], "detected": len(seen)}


async def _save_findings(db, team_id: int, customer_id: int, risks: List[risk_rules.Risk]) -> List[int]:
    now, seen = utcnow(), []
    for risk in risks:
        key = f"{team_id}:{customer_id}:{risk.opportunity_id or 0}:{risk.risk_code}"
        row = (await db.execute(select(CrmRiskFinding).where(CrmRiskFinding.dedupe_key == key))).scalar_one_or_none()
        if row is None:
            row = CrmRiskFinding(team_id=team_id, customer_id=customer_id, opportunity_id=risk.opportunity_id,
                                 risk_code=risk.risk_code, dedupe_key=key, first_seen_at=now)
            db.add(row)
        row.level, row.evidence, row.suggested_action = risk.level, risk.evidence, risk.suggested_action
        row.source, row.source_activity_id, row.status, row.last_seen_at, row.resolved_at = (
            risk.source, risk.source_activity_id, "open", now, None)
        await db.flush()
        seen.append(row.id)
        if risk.suggested_action:
            await _suggest(db, team_id, customer_id, risk.opportunity_id, row.id, risk.suggested_action,
                           f"{risk_rules.RISK_LABELS.get(risk.risk_code)}：{risk.evidence}", f"risk:{key}")
    # 规则风险不再成立：标为已解除（模型风险由销售自己关闭）
    stale = (await db.execute(select(CrmRiskFinding).where(
        CrmRiskFinding.team_id == team_id, CrmRiskFinding.customer_id == customer_id, CrmRiskFinding.status == "open",
        CrmRiskFinding.source == "rule"))).scalars().all()
    for row in stale:
        if row.id not in seen:
            row.status, row.resolved_at = "resolved", now
    await db.commit()
    return seen


async def dismiss_risk(db, user_id: int, team_id: int, finding_id: int) -> Dict[str, Any]:
    await require_team_member_async(db, user_id, team_id, "crm")
    row = (await db.execute(select(CrmRiskFinding).where(CrmRiskFinding.id == finding_id,
                                                         CrmRiskFinding.team_id == team_id))).scalar_one_or_none()
    if row is None:
        raise NotFound("风险不存在")
    row.status, row.resolved_at = "dismissed", utcnow()
    await db.commit()
    return finding_payload(row)


async def list_risks(db, user_id: int, team_id: int, customer_id: Optional[int] = None) -> List[Dict[str, Any]]:
    await require_team_member_async(db, user_id, team_id, "crm")
    query = select(CrmRiskFinding).where(CrmRiskFinding.team_id == team_id, CrmRiskFinding.status == "open")
    if customer_id is not None:
        query = query.where(CrmRiskFinding.customer_id == customer_id)
    rank = {"high": 0, "medium": 1, "low": 2}
    rows = (await db.execute(query.limit(500))).scalars().all()
    return sorted((finding_payload(r) for r in rows), key=lambda r: (rank.get(r["level"], 3), r["customer_id"]))


# ------------------------------------------------------------------ 下一步建议

async def _suggest(db, team_id: int, customer_id: int, opportunity_id: Optional[int], finding_id: Optional[int],
                   title: str, detail: str, key: str) -> None:
    """同一个来源只生成一条建议：已有的（哪怕被忽略了）不重新生成。"""
    exists = (await db.execute(select(CrmActionSuggestion.id).where(CrmActionSuggestion.dedupe_key == key[:200]))).scalar()
    if exists:
        return
    db.add(CrmActionSuggestion(team_id=team_id, customer_id=customer_id, opportunity_id=opportunity_id,
                               risk_finding_id=finding_id, title=title[:300], detail=detail[:2000], status="suggested",
                               dedupe_key=key[:200]))
    await db.flush()


def suggestion_payload(row: CrmActionSuggestion) -> Dict[str, Any]:
    return {"id": row.id, "customer_id": row.customer_id, "opportunity_id": row.opportunity_id,
            "risk_finding_id": row.risk_finding_id, "title": row.title, "detail": row.detail, "due_date": row.due_date,
            "status": row.status, "remind_at": row.remind_at.isoformat() + "Z" if row.remind_at else None,
            "created_at": row.created_at.isoformat() + "Z"}


async def list_suggestions(db, user_id: int, team_id: int, customer_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """待处理的建议；“稍后提醒”的到时间后重新出现。"""
    await require_team_member_async(db, user_id, team_id, "crm")
    now = utcnow()
    query = select(CrmActionSuggestion).where(CrmActionSuggestion.team_id == team_id,
                                              CrmActionSuggestion.status.in_(("suggested", "snoozed")))
    if customer_id is not None:
        query = query.where(CrmActionSuggestion.customer_id == customer_id)
    rows = (await db.execute(query.order_by(CrmActionSuggestion.id.desc()).limit(300))).scalars().all()
    return [suggestion_payload(r) for r in rows if r.status == "suggested" or (r.remind_at and r.remind_at <= now)]


async def decide_suggestion(db, user_id: int, team_id: int, suggestion_id: int, decision: str,
                            title: Optional[str] = None, due_date: Optional[str] = None,
                            remind_days: Optional[int] = None) -> Dict[str, Any]:
    """create：原样创建待办；edit_create：改了标题 / 截止日期再创建；ignore：忽略；snooze：过几天再提醒。
    只会在待办中心建一条待办（给自己），不修改商机，不联系客户。"""
    from service import audit_service, work_item_service
    await require_team_member_async(db, user_id, team_id, "crm")
    row = (await db.execute(select(CrmActionSuggestion).where(
        CrmActionSuggestion.id == suggestion_id, CrmActionSuggestion.team_id == team_id))).scalar_one_or_none()
    if row is None:
        raise NotFound("建议不存在")
    if row.status in ("created", "ignored"):
        raise Conflict("这条建议已经处理过了")
    if decision not in ("create", "edit_create", "ignore", "snooze"):
        raise InvalidInput("只能选择：确认创建任务、修改后创建、忽略、稍后提醒")
    now = utcnow()
    if decision in ("create", "edit_create"):
        if decision == "edit_create":
            title = (title or "").strip()
            if not title:
                raise InvalidInput("请填写任务内容")
            row.title = title[:300]
            row.due_date = due_date or None
        key = f"crm-suggestion:{team_id}:{row.customer_id}:{row.id}"
        await work_item_service.upsert(db, user_id, "crm", key, title=row.title, team_id=team_id,
                                       detail=(row.detail or "")[:500], link="/department",
                                       due_at=work_item_service.parse_due(row.due_date))
        row.status, row.work_item_key = "created", key
    elif decision == "ignore":
        row.status = "ignored"
    else:
        days = remind_days if remind_days and 1 <= remind_days <= 30 else 3
        row.status, row.remind_at = "snoozed", now + timedelta(days=days)
    row.decided_by, row.decided_at, row.decision = user_id, now, decision
    await db.commit()
    await audit_service.record_async(user_id, f"crm.suggestion_{decision}", resource_type="crm_action_suggestion",
                                     resource_id=row.id, detail={"customer_id": row.customer_id})
    return suggestion_payload(row)
