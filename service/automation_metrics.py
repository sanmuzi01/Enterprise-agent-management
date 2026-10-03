"""AI 工作成果的效果指标。

只统计能从系统数据里直接算出来的量；"节省时间"依赖管理员设定的手工办理基准（估算值，
页面上明确标注），不把模型耗时包装成节省的人工时间。
"""
import json
import os
from datetime import timedelta
from statistics import median
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from models.init_db import AutomationWork, Team, WorkflowBaseline
from service.exceptions import InvalidInput
from service.workflows import all_workflows, get_workflow
from utils.timeutil import utcnow

# 路线图里的试点目标；None 表示没有可测量的对应指标，不展示目标。
TARGETS = {"first_pass_rate": 0.8, "duplicate_drafts": 0, "median_apply_seconds": {"crm": 120, "expense": 180}}
SKIP_KEYS = {"evidence", "warnings"}
_MISSING = object()


def _leaves(value, prefix=""):
    """展开成 {路径: 值}，忽略原文依据和疑点（它们不是员工需要核对修改的内容）。"""
    out = {}
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in SKIP_KEYS:
                out.update(_leaves(item, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            out.update(_leaves(item, f"{prefix}[{index}]"))
    else:
        out[prefix] = value
    return out


def edit_ratio(proposal: Optional[dict], accepted: Optional[dict]) -> float:
    """员工改动了多少比例的字段：值变了、新增或删除的字段都算一处改动。"""
    if proposal is None or accepted is None:
        return 0.0
    before, after = _leaves(proposal), _leaves(accepted)
    paths = set(before) | set(after)
    if not paths:
        return 0.0
    # 用哨兵区分"字段不存在"和"值为 None"：新增一个空字段也是一处改动。
    changed = sum(1 for p in paths if before.get(p, _MISSING) != after.get(p, _MISSING))
    return changed / len(paths)


def _median(values: List[float]) -> Optional[float]:
    return round(median(values), 1) if values else None


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 3) if denominator else None


def _token_price() -> Optional[float]:
    """每 1000 token 的单价（元），未配置则只展示 token 数，不编造金额。"""
    try:
        return float(os.environ["AUTOMATION_TOKEN_PRICE_PER_1K"])
    except (KeyError, ValueError):
        return None


async def baselines(db) -> Dict[str, float]:
    saved = {row.kind: row.minutes for row in (await db.execute(select(WorkflowBaseline))).scalars().all()}
    return {w.id: saved.get(w.id, w.baseline_minutes) for w in all_workflows()}


async def set_baseline(db, user_id: int, kind: str, minutes: float) -> Dict[str, float]:
    get_workflow(kind)
    if not 0.5 <= minutes <= 600:
        raise InvalidInput("手工办理基准时间应在 0.5 到 600 分钟之间")
    row = await db.get(WorkflowBaseline, kind)
    if row is None:
        db.add(WorkflowBaseline(kind=kind, minutes=minutes, updated_by=user_id))
    else:
        row.minutes, row.updated_by = minutes, user_id
    await db.commit()
    return await baselines(db)


def summarize(kind: str, works: List[AutomationWork], baseline_minutes: float) -> Dict[str, Any]:
    applied = [w for w in works if w.status == "applied"]
    total = len(works)
    apply_seconds = [(w.applied_at - w.created_at).total_seconds() for w in applied if w.applied_at]
    first_pass = [w for w in applied if not w.edited and w.apply_attempts <= 1]
    edit_ratios = [edit_ratio(json.loads(w.proposal_json or "null"), json.loads(w.accepted_json or "null"))
                   for w in applied]
    blocked = sum(1 for w in works if any(c.get("level") == "blocker" for c in json.loads(w.business_checks_json or "[]")))
    business_ids = [(json.loads(w.business_result_json or "{}") or {}).get("id") for w in applied]
    business_ids = [i for i in business_ids if i is not None]
    median_apply = _median(apply_seconds)
    saved_minutes_each = None if median_apply is None else max(0.0, baseline_minutes - median_apply / 60)
    tokens = sum(w.total_tokens or 0 for w in works)
    price = _token_price()
    return {
        "kind": kind, "total": total, "applied": len(applied),
        "failed": sum(1 for w in works if w.status == "failed"),
        "apply_rate": _ratio(len(applied), total),
        "first_pass_rate": _ratio(len(first_pass), len(applied)),
        "avg_edit_ratio": round(sum(edit_ratios) / len(edit_ratios), 3) if edit_ratios else None,
        "retry_rate": _ratio(sum(1 for w in works if w.apply_attempts > 1), sum(1 for w in works if w.apply_attempts > 0)),
        "median_generate_seconds": _median([w.elapsed_ms / 1000 for w in works if w.status in ("ready", "applied", "applying", "retry")]),
        "median_apply_seconds": median_apply,
        "business_blocked": blocked,
        "duplicate_drafts": len(business_ids) - len(set(business_ids)),
        "users": len({w.user_id for w in works}),
        "tokens": tokens,
        "tokens_per_applied": round(tokens / len(applied)) if applied else None,
        "cost_per_applied": round(tokens / 1000 * price / len(applied), 4) if price and applied else None,
        "baseline_minutes": baseline_minutes,
        "estimated_saved_minutes": None if saved_minutes_each is None else round(saved_minutes_each * len(applied), 1),
    }


async def metrics(db, days: int = 30, team_id: Optional[int] = None) -> Dict[str, Any]:
    if not 1 <= days <= 365:
        raise InvalidInput("统计天数应在 1 到 365 之间")
    since = utcnow() - timedelta(days=days)
    query = select(AutomationWork).where(AutomationWork.created_at >= since)
    if team_id is not None:
        query = query.where(AutomationWork.team_id == team_id)
    works = (await db.execute(query)).scalars().all()
    base = await baselines(db)
    by_kind = {w.id: [] for w in all_workflows()}
    for work in works:
        by_kind.setdefault(work.kind, []).append(work)
    rows = []
    for kind, items in by_kind.items():
        row = summarize(kind, items, base.get(kind, 10.0))
        try:
            row["name"] = get_workflow(kind).name
        except InvalidInput:
            row["name"] = kind
        rows.append(row)
    overall = summarize("all", list(works), 0)
    overall.pop("baseline_minutes"), overall.pop("estimated_saved_minutes")
    overall["estimated_saved_minutes"] = round(sum(r["estimated_saved_minutes"] or 0 for r in rows), 1)
    team_name = None
    if team_id is not None:
        team_name = (await db.execute(select(Team.name).where(Team.id == team_id))).scalar_one_or_none()
    return {"days": days, "team_id": team_id, "team_name": team_name, "overall": overall, "workflows": rows,
            "targets": TARGETS, "token_price_configured": _token_price() is not None}
