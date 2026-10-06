"""考勤异常的提醒：员工有异常待说明；人事/部门负责人有员工已说明、等待认定的异常。

每人一条汇总提醒（不是每条异常一条，避免月底导入一批后被刷屏）；异常处理完后条件不成立，提醒自动关闭。
只陈述事实：多少条、最早是哪天、已等多久，不评价员工。
"""
from collections import defaultdict
from datetime import timedelta
from typing import Dict, List, Tuple

from sqlalchemy import select

from models.init_db import AttendanceAnomaly
from service.reminders.base import Reminder, active_teams, approvers, env_int, sync_reminders, team_members
from utils.timeutil import utcnow

LINK = "/department"
RECENT_DAYS = 62     # 更早的历史异常不再催（导入一年前的文件不应该让所有人收到提醒）


async def _recent(db, status: str) -> List[AttendanceAnomaly]:
    since = utcnow() - timedelta(days=RECENT_DAYS)
    return list((await db.execute(select(AttendanceAnomaly).where(AttendanceAnomaly.status == status, AttendanceAnomaly.work_date >= since)
                                  .order_by(AttendanceAnomaly.work_date))).scalars().all())


def _days_since(moment, now) -> int:
    return max(0, int((now - moment).total_seconds() // 86400))


async def run_attendance_explain(db) -> Tuple[int, int]:
    """员工：有考勤异常等你说明；超过 2 天还没说明升为高优先级。"""
    wait_days, now = env_int("REMIND_ATTENDANCE_EXPLAIN_DAYS", 2), utcnow()
    by_user: Dict[int, List[AttendanceAnomaly]] = defaultdict(list)
    for row in await _recent(db, "open"):
        by_user[row.user_id].append(row)
    reminders = []
    for user_id, rows in by_user.items():
        oldest = rows[0]
        waited = _days_since(oldest.created_at, now)
        reminders.append(Reminder(
            user_id=user_id, key=f"att-explain:{user_id}", team_id=oldest.team_id, link=LINK, priority="high" if waited >= wait_days else "normal",
            title=f"你有 {len(rows)} 条考勤异常待说明",
            detail=f"最早一条是 {oldest.work_date.date().isoformat()}，系统发现后已等待 {waited} 天。请在「部门工作台 → 考勤」写明原因（比如忘打卡、外出办事、设备故障）"))
    return await sync_reminders(db, "attendance_explain", "attendance", reminders)


async def run_attendance_decide(db) -> Tuple[int, int]:
    """部门负责人（没有负责人时企业管理员）：本部门有员工已说明的异常等你认定；人事：全企业的。自己的异常不算。"""
    wait_days, now = env_int("REMIND_ATTENDANCE_DECIDE_DAYS", 3), utcnow()
    rows = await _recent(db, "explained")
    if not rows:
        return await sync_reminders(db, "attendance_decide", "attendance", [])
    reminders: List[Reminder] = []
    teams = await active_teams(db)
    pending: Dict[Tuple[int, int], List[AttendanceAnomaly]] = defaultdict(list)       # (负责人, 部门) → 异常
    for team in teams:
        mine = [r for r in rows if r.team_id == team.id]
        if not mine:
            continue
        for head in await approvers(db, team):
            pending[(head, team.id)] += [r for r in mine if r.user_id != head]
    hr_teams = [t for t in teams if t.department_code == "hr"]
    hr_pending: Dict[Tuple[int, int], List[AttendanceAnomaly]] = {}
    for team in hr_teams:
        for member in await team_members(db, team.id):
            hr_pending[(member, team.id)] = [r for r in rows if r.user_id != member and any(t.id == r.team_id and t.organization_id == team.organization_id for t in teams)]
    merged: Dict[int, Tuple[int, Dict[int, AttendanceAnomaly]]] = {}
    for (user_id, team_id), items in list(pending.items()) + list(hr_pending.items()):
        if not items:
            continue
        current = merged.setdefault(user_id, (team_id, {}))
        current[1].update({r.id: r for r in items})
    for user_id, (team_id, items) in merged.items():
        ordered = sorted(items.values(), key=lambda r: r.explained_at or r.created_at)
        waited = _days_since(ordered[0].explained_at or ordered[0].created_at, now)
        reminders.append(Reminder(
            user_id=user_id, key=f"att-decide:{user_id}", team_id=team_id, link=LINK, priority="high" if waited >= wait_days else "normal",
            title=f"有 {len(ordered)} 条考勤异常等你认定",
            detail=f"员工已写明原因，最早一条已等待 {waited} 天。请在「部门工作台 → 考勤」认定为异常或正常，并写明理由"))
    return await sync_reminders(db, "attendance_decide", "attendance", reminders)
