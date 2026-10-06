"""责任协同的提醒：待接受 / 异议、即将到期与逾期、长期受阻、待验收。

每条规则算"此刻应存在的提醒"，由 sync_reminders 同步成待办与通知；条件不再成立（员工接受了、验收了、阻塞解除）
自动关闭。只陈述系统里的事实（谁、哪项、期限、已等多久），不评价员工，也不推测绩效。
"""
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from service.exceptions import AppError
from service.name_lookup import user_names
from service.reminders.base import (Reminder, active_teams, approvers, beijing_day_start, env_int, parse_instant,
                                    sync_reminders)
from utils.timeutil import utcnow

LINK = "/department"


async def _team_tasks(db, team) -> List[Dict[str, Any]]:
    from service import responsibility_service as rs
    for head in await approvers(db, team):
        try:
            return await rs.team_tasks_for_reminders(db, head, team.id)
        except AppError:
            continue   # 该负责人的身份已失效 / 业务服务暂不可用，换下一个
    return []


async def _names(db, tasks: List[Dict[str, Any]]) -> Dict[int, str]:
    ids = [t.get(k) for t in tasks for k in ("responsibleUserId", "reviewerUserId", "assignedByUserId")]
    return await user_names(db, [i for i in ids if i])


def _hours(since: Optional[str], now) -> Optional[int]:
    moment = parse_instant(since)
    return None if moment is None else int((now - moment).total_seconds() // 3600)


def _label(names: Dict[int, str], user_id: Optional[int]) -> str:
    return names.get(user_id) or "—"


async def run_resp_accept(db) -> Tuple[int, int]:
    """主责员工：有责任等你接受；指派人：员工超过时限还没接受，或对责任提出了异议。"""
    wait_hours, now, reminders = env_int("REMIND_RESP_ACCEPT_HOURS", 24), utcnow(), []
    for team in await active_teams(db):
        tasks = [t for t in await _team_tasks(db, team) if t["status"] in ("PENDING_ACCEPT", "NEGOTIATING")]
        names = await _names(db, tasks)
        for t in tasks:
            waited = _hours(t.get("assignedAt"), now) or 0
            assigner = t.get("assignedByUserId")
            if t["status"] == "PENDING_ACCEPT" and t.get("responsibleUserId"):
                reminders.append(Reminder(
                    user_id=t["responsibleUserId"], key=f"resp-accept:{t['id']}", team_id=team.id,
                    title=f"{team.name}：「{t['title']}」等你接受", link=LINK,
                    detail=f"指派人 {_label(names, assigner)}，截止 {t.get('dueDate') or '—'}。请查看原文依据和验收标准后接受，或提出异议",
                    priority="high" if waited >= wait_hours else "normal"))
                if assigner and waited >= wait_hours:
                    reminders.append(Reminder(
                        user_id=assigner, key=f"resp-unaccepted:{t['id']}", team_id=team.id, link=LINK, priority="normal",
                        title=f"{team.name}：{_label(names, t['responsibleUserId'])} 还没有接受「{t['title']}」",
                        detail=f"已等待 {waited} 小时"))
            elif t["status"] == "NEGOTIATING" and assigner:
                reminders.append(Reminder(
                    user_id=assigner, key=f"resp-objection:{t['id']}", team_id=team.id, link=LINK, priority="high",
                    title=f"{team.name}：{_label(names, t.get('responsibleUserId'))} 对「{t['title']}」提出了异议",
                    detail="请在责任协同里查看异议并调整责任人、期限或验收标准"))
    return await sync_reminders(db, "resp_accept", "responsibility", reminders)


async def run_resp_due(db) -> Tuple[int, int]:
    """主责员工：责任 2 天内到期或已逾期；指派人：已逾期，或员工报告的阻塞超过 48 小时。"""
    soon_days = env_int("REMIND_RESP_DUE_DAYS", 2)
    blocked_hours, now, reminders = env_int("REMIND_RESP_BLOCKED_HOURS", 48), utcnow(), []
    today = (now + timedelta(hours=8)).date()
    for team in await active_teams(db):
        tasks = [t for t in await _team_tasks(db, team) if t["status"] in ("IN_PROGRESS", "BLOCKED")]
        names = await _names(db, tasks)
        for t in tasks:
            responsible, assigner, due = t.get("responsibleUserId"), t.get("assignedByUserId"), t.get("dueDate")
            if due and responsible:
                days = (parse_due(due) - today).days
                if days <= soon_days:
                    overdue = days < 0
                    reminders.append(Reminder(
                        user_id=responsible, key=f"resp-due:{t['id']}", team_id=team.id, link=LINK,
                        title=f"{team.name}：「{t['title']}」" + ("已逾期" if overdue else "即将到期"),
                        detail=f"截止 {due}" + (f"，已逾期 {-days} 天" if overdue else ""),
                        priority="high" if overdue else "normal", due_at=beijing_day_start(due)))
                    if overdue and assigner:
                        reminders.append(Reminder(
                            user_id=assigner, key=f"resp-overdue:{t['id']}", team_id=team.id, link=LINK, priority="high",
                            title=f"{team.name}：{_label(names, responsible)} 的「{t['title']}」已逾期 {-days} 天",
                            detail=f"截止 {due}，当前状态：{'受阻' if t['status'] == 'BLOCKED' else '执行中'}"))
            if t["status"] == "BLOCKED" and assigner:
                hours = _hours(t.get("blockedSince"), now) or 0
                if hours >= blocked_hours:
                    reminders.append(Reminder(
                        user_id=assigner, key=f"resp-blocked:{t['id']}", team_id=team.id, link=LINK, priority="high",
                        title=f"{team.name}：「{t['title']}」受阻已 {hours} 小时",
                        detail=f"{_label(names, responsible)} 报告的原因：{t.get('blockedReason') or '—'}，需要协调"))
    return await sync_reminders(db, "resp_due", "responsibility", reminders)


async def run_resp_review(db) -> Tuple[int, int]:
    """验收人：员工已提交成果，等你对照验收标准验收；超过 24 小时升为高优先级。"""
    wait_hours, now, reminders = env_int("REMIND_RESP_REVIEW_HOURS", 24), utcnow(), []
    for team in await active_teams(db):
        tasks = [t for t in await _team_tasks(db, team) if t["status"] == "PENDING_REVIEW" and t.get("reviewerUserId")]
        names = await _names(db, tasks)
        for t in tasks:
            waited = _hours(t.get("submittedAt"), now) or 0
            reminders.append(Reminder(
                user_id=t["reviewerUserId"], key=f"resp-review:{t['id']}", team_id=team.id, link=LINK,
                title=f"{team.name}：{_label(names, t.get('responsibleUserId'))} 提交了「{t['title']}」，等你验收",
                detail=f"已等待 {waited} 小时。请对照验收标准验收，不合格可以退回并写明原因",
                priority="high" if waited >= wait_hours else "normal"))
    return await sync_reminders(db, "resp_review", "responsibility", reminders)


def parse_due(day: str):
    from datetime import date
    return date.fromisoformat(day)
