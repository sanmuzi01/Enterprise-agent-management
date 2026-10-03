"""提醒规则调度：每条规则独立租约（多个 Worker 同时运行也只会执行一次）、按间隔运行、
记录健康状态；连续失败达到阈值时通知企业所有者/管理员。由 service/background_worker.py 定时调用。"""
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from models.init_db import Organization, ReminderRun
from service import notification_center
from service.exceptions import NotFound
from service.reminders.base import org_admins
from service.reminders.rules import RULES, RULES_BY_NAME
from utils.logger_handler import get_logger
from utils.timeutil import utcnow

logger = get_logger("reminders")
LEASE_MINUTES = 10
ALERT_AFTER_FAILURES = 3


async def _ensure_row(db, rule_name: str) -> None:
    if await db.get(ReminderRun, rule_name) is None:
        db.add(ReminderRun(rule=rule_name))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()


async def _claim(db, rule_name: str, force: bool) -> bool:
    await _ensure_row(db, rule_name)
    now = utcnow()
    conditions = [ReminderRun.rule == rule_name,
                  or_(ReminderRun.lease_until.is_(None), ReminderRun.lease_until < now)]
    if not force:
        conditions.append(or_(ReminderRun.next_run_at.is_(None), ReminderRun.next_run_at <= now))
    result = await db.execute(update(ReminderRun).where(*conditions).values(
        lease_until=now + timedelta(minutes=LEASE_MINUTES), last_started_at=now))
    await db.commit()
    return result.rowcount == 1


async def _alert_admins(db, rule, error: str) -> None:
    day = utcnow().strftime("%Y%m%d")
    for org_id in (await db.execute(select(Organization.id).where(Organization.status == "active"))).scalars().all():
        for admin_id in await org_admins(db, org_id):
            await notification_center.notify(
                db, admin_id, "system", f"提醒规则「{rule.label}」连续运行失败", body=error[:300],
                link="/admin/diagnose", dedupe_key=f"system:reminder:{rule.name}:{day}")


async def run_rule(db, rule, *, force: bool = False) -> Optional[Dict[str, Any]]:
    if not await _claim(db, rule.name, force):
        return None
    try:
        created, resolved = await rule.run(db)
    except Exception as exc:  # noqa: BLE001 —— 单条规则失败不能影响其它规则；下次按间隔自动重试
        await db.rollback()
        run = await db.get(ReminderRun, rule.name)
        await db.refresh(run)
        failures = run.consecutive_failures + 1
        message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
        await db.execute(update(ReminderRun).where(ReminderRun.rule == rule.name).values(
            lease_until=None, last_finished_at=utcnow(), last_status="failed", last_error=message[:500],
            consecutive_failures=failures,
            next_run_at=utcnow() + timedelta(minutes=min(rule.interval_minutes, 10))))
        await db.commit()
        logger.warning(f"提醒规则 {rule.name} 运行失败（连续 {failures} 次）: {message}")
        if failures >= ALERT_AFTER_FAILURES:
            await _alert_admins(db, rule, message)
        return {"rule": rule.name, "status": "failed", "error": message}
    await db.execute(update(ReminderRun).where(ReminderRun.rule == rule.name).values(
        lease_until=None, last_finished_at=utcnow(), last_status="ok", last_error=None, consecutive_failures=0,
        last_created=created, last_resolved=resolved, next_run_at=utcnow() + timedelta(minutes=rule.interval_minutes)))
    await db.commit()
    return {"rule": rule.name, "status": "ok", "created": created, "resolved": resolved}


async def run_due_rules(db) -> List[Dict[str, Any]]:
    results = []
    for rule in RULES:
        result = await run_rule(db, rule)
        if result:
            results.append(result)
    return results


async def run_now(db, rule_name: str) -> Dict[str, Any]:
    rule = RULES_BY_NAME.get(rule_name)
    if rule is None:
        raise NotFound("提醒规则不存在")
    result = await run_rule(db, rule, force=True)
    return result or {"rule": rule_name, "status": "busy", "error": "该规则正在其它 Worker 上运行"}


async def status(db) -> List[Dict[str, Any]]:
    rows = {r.rule: r for r in (await db.execute(select(ReminderRun))).scalars().all()}
    fmt = lambda v: v.isoformat() + "Z" if v else None  # noqa: E731
    out = []
    for rule in RULES:
        run = rows.get(rule.name)
        out.append({
            "rule": rule.name, "label": rule.label, "description": rule.description,
            "interval_minutes": rule.interval_minutes,
            "last_status": run.last_status if run else None, "last_error": run.last_error if run else None,
            "last_finished_at": fmt(run.last_finished_at) if run else None,
            "next_run_at": fmt(run.next_run_at) if run else None,
            "last_created": run.last_created if run else 0, "last_resolved": run.last_resolved if run else 0,
            "consecutive_failures": run.consecutive_failures if run else 0,
        })
    return out
