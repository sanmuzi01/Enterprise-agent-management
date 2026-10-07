"""试点数据：员工记录的手工办理计时、对 AI 成果的评价，以及据此生成的试点效果报告。

原则：
- 不编造数字——节省工时 = 基准时间 − AI 办理实际耗时，基准时间优先用**员工实测的手工计时中位数**（至少 5 个样本、3 个不同的人），
  没有足够样本时才用管理员设定的基准，报告里明确标注来源；
- 不做个人排名或监控——报告只有汇总和匿名评价，没有按人展开的数据；
- 样本太少时报告自己会写明“结论仅供参考”，不替人把话说满。
"""
from datetime import timedelta
from statistics import median
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from models.init_db import AttendanceAnomaly, AutomationWork, PilotTimeSample, SystemIssue, Team, TeamMember, WorkFeedback, WorkflowBaseline
from service.automation_work_service import authorize, get_work
from service.exceptions import InvalidInput, NotFound
from service.workflows import all_workflows, get_workflow
from utils.timeutil import utcnow

MIN_SAMPLES = 5
MIN_USERS = 3
MAX_SAMPLES_PER_USER_PER_DAY = 20
SMALL_SAMPLE = 30


# ---------------------------------------------------------------- 员工侧

async def add_time_sample(db, user_id: int, team_id: int, kind: str, minutes: float, note: Optional[str] = None) -> Dict[str, Any]:
    await authorize(db, user_id, team_id)
    get_workflow(kind)
    if not 0.5 <= float(minutes) <= 600:
        raise InvalidInput("手工办理时间应在 0.5 到 600 分钟之间")
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    done = (await db.execute(select(func.count()).where(PilotTimeSample.user_id == user_id, PilotTimeSample.created_at >= today))).scalar() or 0
    if done >= MAX_SAMPLES_PER_USER_PER_DAY:
        raise InvalidInput("今天记录的次数已达上限")
    db.add(PilotTimeSample(user_id=user_id, team_id=team_id, kind=kind, minutes=round(float(minutes), 1), note=(note or "").strip()[:200] or None))
    await db.commit()
    return {"ok": True}


async def my_time_samples(db, user_id: int, team_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    await authorize(db, user_id, team_id)
    rows = (await db.execute(select(PilotTimeSample).where(PilotTimeSample.user_id == user_id, PilotTimeSample.team_id == team_id)
                             .order_by(PilotTimeSample.id.desc()).limit(limit))).scalars().all()
    return [{"id": r.id, "kind": r.kind, "minutes": r.minutes, "note": r.note, "created_at": r.created_at.isoformat() + "Z"} for r in rows]


async def give_feedback(db, user_id: int, work_id: str, rating: int, comment: Optional[str], manual_minutes: Optional[float]) -> Dict[str, Any]:
    work = await get_work(db, user_id, work_id)          # 只能评价自己的成果
    if work.status != "applied":
        raise InvalidInput("保存为业务草稿之后才能评价")
    if not 1 <= int(rating) <= 5:
        raise InvalidInput("评分应在 1 到 5 之间")
    row = (await db.execute(select(WorkFeedback).where(WorkFeedback.work_id == work.id, WorkFeedback.user_id == user_id))).scalar_one_or_none()
    text = (comment or "").strip()[:300] or None
    if row is None:
        db.add(WorkFeedback(work_id=work.id, user_id=user_id, team_id=work.team_id, kind=work.kind, rating=int(rating), comment=text))
    else:
        row.rating, row.comment = int(rating), text
    await db.commit()
    if manual_minutes is not None and not (await db.execute(select(PilotTimeSample.id).where(
            PilotTimeSample.user_id == user_id, PilotTimeSample.note == f"来自成果 {work.id}"))).first():
        await add_time_sample(db, user_id, work.team_id, work.kind, manual_minutes, f"来自成果 {work.id}")
    return {"rating": int(rating), "comment": text}


async def feedback_for(db, user_id: int, work_id: str) -> Optional[Dict[str, Any]]:
    row = (await db.execute(select(WorkFeedback).where(WorkFeedback.work_id == work_id, WorkFeedback.user_id == user_id))).scalar_one_or_none()
    return None if row is None else {"rating": row.rating, "comment": row.comment}


# ---------------------------------------------------------------- 实测基准

async def measured_baselines(db) -> Dict[str, Dict[str, Any]]:
    """样本足够（≥5 个、≥3 个不同的人）的类型：手工办理时间取中位数（比平均数更不受个别极端值影响）。"""
    rows = (await db.execute(select(PilotTimeSample.kind, PilotTimeSample.user_id, PilotTimeSample.minutes))).all()
    by_kind: Dict[str, List[Any]] = {}
    for kind, user_id, minutes in rows:
        by_kind.setdefault(kind, []).append((user_id, minutes))
    result = {}
    for kind, items in by_kind.items():
        users = {u for u, _ in items}
        if len(items) >= MIN_SAMPLES and len(users) >= MIN_USERS:
            result[kind] = {"minutes": round(float(median(m for _, m in items)), 1), "samples": len(items), "users": len(users)}
    return result


async def baseline_details(db) -> Dict[str, Dict[str, Any]]:
    """每类工作最终使用的基准时间和来源：measured 实测 / admin 管理员设定 / default 默认值。"""
    saved = {row.kind: row.minutes for row in (await db.execute(select(WorkflowBaseline))).scalars().all()}
    measured = await measured_baselines(db)
    result = {}
    for w in all_workflows():
        if w.id in measured:
            result[w.id] = {**measured[w.id], "source": "measured"}
        elif w.id in saved:
            result[w.id] = {"minutes": saved[w.id], "source": "admin", "samples": 0, "users": 0}
        else:
            result[w.id] = {"minutes": w.baseline_minutes, "source": "default", "samples": 0, "users": 0}
    return result


# ---------------------------------------------------------------- 试点报告

SOURCE_LABELS = {"measured": "员工实测中位数", "admin": "管理员设定", "default": "系统默认值（未经实测）"}


async def report(db, days: int = 30, team_id: Optional[int] = None) -> Dict[str, Any]:
    from service import automation_metrics
    from service.observability import agent_runs, issues
    from starlette.concurrency import run_in_threadpool
    from models.init_db import SessionLocal
    if not 1 <= days <= 365:
        raise InvalidInput("统计天数应在 1 到 365 之间")
    if team_id is not None and (await db.execute(select(Team.id).where(Team.id == team_id))).scalar() is None:
        raise NotFound("部门不存在")
    since = utcnow() - timedelta(days=days)
    metrics = await automation_metrics.metrics(db, days, team_id)
    scope = [AutomationWork.created_at >= since] + ([AutomationWork.team_id == team_id] if team_id is not None else [])
    users_any = (await db.execute(select(func.count(func.distinct(AutomationWork.user_id))).where(*scope))).scalar() or 0
    per_user = (await db.execute(select(AutomationWork.user_id, func.count()).where(*scope, AutomationWork.status == "applied")
                                 .group_by(AutomationWork.user_id))).all()
    members_q = select(func.count(func.distinct(TeamMember.user_id))).where(TeamMember.status == "active")
    if team_id is not None:
        members_q = members_q.where(TeamMember.team_id == team_id)
    members = (await db.execute(members_q)).scalar() or 0
    participation = {"members": int(members), "users_tried": int(users_any), "users_applied": len(per_user),
                     "repeat_users": sum(1 for _, n in per_user if n >= 2),
                     "adoption_rate": round(len(per_user) / members, 3) if members else None}

    fb_scope = [WorkFeedback.created_at >= since] + ([WorkFeedback.team_id == team_id] if team_id is not None else [])
    ratings = [r for (r,) in (await db.execute(select(WorkFeedback.rating).where(*fb_scope))).all()]
    comments = [{"rating": r, "kind": k, "comment": c} for r, k, c in (await db.execute(
        select(WorkFeedback.rating, WorkFeedback.kind, WorkFeedback.comment).where(*fb_scope, WorkFeedback.comment.is_not(None))
        .order_by(WorkFeedback.id.desc()).limit(10))).all()]   # 只有评分、类型和话，没有人名
    feedback = {"count": len(ratings), "average": round(sum(ratings) / len(ratings), 2) if ratings else None,
                "distribution": {str(i): ratings.count(i) for i in range(1, 6)}, "recent_comments": comments}

    details = await baseline_details(db)
    for row in metrics["workflows"]:
        info = details.get(row["kind"], {})
        row["baseline_source"], row["baseline_samples"], row["baseline_users"] = info.get("source"), info.get("samples", 0), info.get("users", 0)

    def sync_parts(session):
        return agent_runs.agent_health(session, days), issues.summary(session)
    health, problems = await run_in_threadpool(lambda: _with_session(sync_parts))
    agents = health["agents"]
    total_runs = sum(a["runs"] for a in agents)
    reliability = {"runs": total_runs, "failed": sum(a["failed"] for a in agents),
                   "failure_rate": round(sum(a["failed"] for a in agents) * 100 / total_runs, 1) if total_runs else None,
                   "open_problems": problems["active"], "mtta_minutes": problems["mtta_minutes"], "mttr_minutes": problems["mttr_minutes"]}

    attendance = await attendance_process(db, since, team_id)
    frontend_errors = (await db.execute(select(func.count()).select_from(SystemIssue).where(
        SystemIssue.service == "web-frontend", SystemIssue.last_seen_at >= since))).scalar() or 0
    reliability["frontend_error_issues"] = int(frontend_errors)

    overall = metrics["overall"]
    caveats = []
    if overall["total"] < SMALL_SAMPLE:
        caveats.append(f"本期只有 {overall['total']} 份 AI 工作成果，样本量小于 {SMALL_SAMPLE}，所有比例仅供参考，不能当作结论。")
    if participation["users_applied"] < 5:
        caveats.append(f"本期只有 {participation['users_applied']} 人把成果保存为业务草稿，参与人数偏少。")
    estimated = [r for r in metrics["workflows"] if r["applied"] and r.get("baseline_source") != "measured"]
    if estimated:
        caveats.append("以下工作的“节省工时”用的不是实测基准（" + "、".join(f"{r['name']}：{SOURCE_LABELS.get(r['baseline_source'])}" for r in estimated)
                       + "），是估算值；请让试点员工用“记录手工办理时间”补足样本。")
    if feedback["count"] < 5:
        caveats.append(f"员工评价只有 {feedback['count']} 条，满意度不具代表性。")
    if attendance["needs_handling"] and attendance["needs_handling"] < 10:
        caveats.append(f"考勤异常只有 {attendance['needs_handling']} 条需要处理，说明率、认定时长不具代表性。")
    caveats.append("责任协同的数据在企业业务服务（Java）里，本报告暂未汇总；在部门工作台的“部门看板”里看。")
    caveats.append("节省工时 = 基准时间 − AI 办理实际耗时，只统计已保存为业务草稿的成果；不包含员工核对、修改花费的时间之外的其他收益，也不能说明业务结果的质量。")
    return {"days": days, "team_id": team_id, "team_name": metrics["team_name"], "generated_at": utcnow().isoformat() + "Z",
            "participation": participation, "ai_work": metrics, "feedback": feedback, "reliability": reliability, "attendance": attendance,
            "baselines": {k: {**v, "source_label": SOURCE_LABELS[v["source"]]} for k, v in details.items()}, "caveats": caveats}


async def attendance_process(db, since, team_id: Optional[int] = None) -> Dict[str, Any]:
    """考勤异常的处理流程指标（只有汇总，没有人名）：员工说明得快不快、认定得及时不及时、有多少是补录后系统自己消除的。
    说明“流程跑通了没有”，不评价考勤本身——考勤异常多少取决于导入的文件，不是系统效果。"""
    scope = [AttendanceAnomaly.created_at >= since] + ([AttendanceAnomaly.team_id == team_id] if team_id is not None else [])
    rows = (await db.execute(select(AttendanceAnomaly.status, AttendanceAnomaly.created_at, AttendanceAnomaly.explained_at, AttendanceAnomaly.decided_at)
                             .where(*scope))).all()
    counted = [r for r in rows if r.status != "cleared"]                      # 补录/请假批准后系统自己消除的不算“需要人处理”
    explained = [r for r in counted if r.explained_at is not None]
    decided = [r for r in counted if r.status in ("confirmed", "dismissed")]
    hours = lambda a, b: (b - a).total_seconds() / 3600                       # noqa: E731
    explain_hours = [hours(r.created_at, r.explained_at) for r in explained]
    decide_hours = [hours(r.explained_at, r.decided_at) for r in decided if r.explained_at and r.decided_at]
    return {"total": len(rows), "cleared_by_system": len(rows) - len(counted), "needs_handling": len(counted), "explained": len(explained),
            "explained_rate": round(len(explained) / len(counted), 3) if counted else None, "decided": len(decided),
            "decided_rate": round(len(decided) / len(counted), 3) if counted else None,
            "confirmed": sum(1 for r in decided if r.status == "confirmed"), "dismissed": sum(1 for r in decided if r.status == "dismissed"),
            "median_explain_hours": round(median(explain_hours), 1) if explain_hours else None,
            "median_decide_hours": round(median(decide_hours), 1) if decide_hours else None}


def _with_session(fn):
    from models.init_db import SessionLocal
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def to_markdown(data: Dict[str, Any]) -> str:
    o, p, f, r = data["ai_work"]["overall"], data["participation"], data["feedback"], data["reliability"]
    pct = lambda v: "—" if v is None else f"{v * 100:.0f}%"   # noqa: E731
    scope = data["team_name"] or "全企业"
    lines = [f"# 试点效果报告：{scope}（近 {data['days']} 天）", "", f"生成时间：{data['generated_at']}", "",
             "## 先看这几条说明", ""] + [f"- {c}" for c in data["caveats"]] + ["",
             "## 参与情况", "", f"- 部门成员 {p['members']} 人；试用过 {p['users_tried']} 人；把成果保存为业务草稿 {p['users_applied']} 人（采纳率 {pct(p['adoption_rate'])}）；重复使用（≥2 次）{p['repeat_users']} 人。", "",
             "## AI 工作成果", "", f"- 处理材料 {o['total']} 份，保存为业务草稿 {o['applied']} 份（保存率 {pct(o['apply_rate'])}），失败 {o['failed']} 份。",
             f"- 一次通过率（没改直接保存）{pct(o['first_pass_rate'])}；平均修改比例 {pct(o['avg_edit_ratio'])}。",
             f"- 估算节省工时合计 {o['estimated_saved_minutes']} 分钟（见下表各类工作的基准来源）。", "",
             "| 工作类型 | 份数 | 已保存 | 保存率 | 中位办理耗时 | 基准（分钟） | 基准来源 | 节省（分钟） |", "|---|---|---|---|---|---|---|---|"]
    for row in data["ai_work"]["workflows"]:
        if not row["total"]:
            continue
        secs = row["median_apply_seconds"]
        lines.append(f"| {row['name']} | {row['total']} | {row['applied']} | {pct(row['apply_rate'])} | {'—' if secs is None else f'{secs / 60:.1f} 分钟'} | "
                     f"{row['baseline_minutes']:g} | {SOURCE_LABELS.get(row['baseline_source'], '—')}（样本 {row['baseline_samples']}） | {row['estimated_saved_minutes'] if row['estimated_saved_minutes'] is not None else '—'} |")
    lines += ["", "## 员工评价（匿名）", "",
              f"- 共 {f['count']} 条，平均 {f['average'] if f['average'] is not None else '—'} 分；分布 " + "、".join(f"{k} 分 {v}" for k, v in f["distribution"].items()), ""]
    lines += [f"  - （{c['rating']} 分）{c['comment']}" for c in f["recent_comments"]]
    lines += ["", "## 可靠性", "",
              f"- Agent 运行 {r['runs']} 次，失败 {r['failed']} 次（失败率 {'—' if r['failure_rate'] is None else str(r['failure_rate']) + '%'}）；"
              f"未关闭的系统问题 {r['open_problems']} 个；平均确认 {r['mtta_minutes'] if r['mtta_minutes'] is not None else '—'} 分钟，平均恢复 {r['mttr_minutes'] if r['mttr_minutes'] is not None else '—'} 分钟；页面脚本错误涉及 {r.get('frontend_error_issues', 0)} 个问题。", ""]
    a = data.get("attendance")
    if a and a["total"]:
        lines += ["## 考勤异常处理流程", "",
                  f"- 系统发现 {a['total']} 条异常，其中 {a['cleared_by_system']} 条补录打卡或请假批准后系统自己消除；需要人处理 {a['needs_handling']} 条。",
                  f"- 员工已说明 {a['explained']} 条（{pct(a['explained_rate'])}），中位 {a['median_explain_hours'] if a['median_explain_hours'] is not None else '—'} 小时；"
                  f"已认定 {a['decided']} 条（{pct(a['decided_rate'])}，认定为异常 {a['confirmed']}、认定为正常 {a['dismissed']}），说明后中位 {a['median_decide_hours'] if a['median_decide_hours'] is not None else '—'} 小时认定。",
                  "- 这些数字说明流程有没有跑通，不评价考勤本身；异常多少取决于导入的文件和规则设置。", ""]
    return "\n".join(lines)
