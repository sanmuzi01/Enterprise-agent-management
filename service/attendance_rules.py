"""考勤异常的判断规则：纯函数，不涉及模型，同样的输入永远得到同样的结论。

规则（都可以在“考勤规则”里按部门调整上下班时间和迟到宽限）：
- 上班日没有任何打卡，也没有已批准的请假 → 旷工（高）；
- 只有一次打卡：落在上下班时间的前半段 → 缺下班卡，后半段 → 缺上班卡（中）；
- 最早一次打卡晚于“上班时间 + 宽限” → 迟到（分钟数从上班时间算起）；最晚一次打卡早于下班时间 → 早退；
- 休息日、法定节假日有打卡 → 休息日出勤（低，提醒核对是否有加班审批）；
- 上班日已批准请假但有打卡 → 请假与打卡冲突（低）；
- 首末打卡间隔超过 14 小时 → 时长异常（中，通常是漏打下班卡或打卡机时间不对）；
- 1 分钟内的重复刷卡当作一次（打卡机连刷）。
迟到/早退 15 分钟以内为低、1 小时以内为中、超过为高。当天还没结束的日期不判断。
"""
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_RULE = {"work_start": "09:00", "work_end": "18:00", "grace_minutes": 5}
OVERLONG_HOURS = 14
DEDUP_SECONDS = 60

TYPE_LABELS = {"late": "迟到", "early_leave": "早退", "missing_in": "缺上班卡", "missing_out": "缺下班卡", "absent": "旷工",
               "rest_day_work": "休息日出勤", "overlong": "时长异常", "leave_conflict": "请假与打卡冲突"}
SEVERITY_LABELS = {"low": "低", "medium": "中", "high": "高"}


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def validate_rule(work_start: str, work_end: str, grace_minutes: int) -> Tuple[str, str, int]:
    try:
        start, end = parse_hhmm(work_start), parse_hhmm(work_end)
    except (ValueError, AttributeError):
        raise ValueError("上下班时间格式应为 HH:MM") from None
    if end <= start:
        raise ValueError("下班时间必须晚于上班时间")
    if not 0 <= int(grace_minutes) <= 60:
        raise ValueError("迟到宽限应在 0 到 60 分钟之间")
    return start.strftime("%H:%M"), end.strftime("%H:%M"), int(grace_minutes)


def default_day_kind(day: date) -> str:
    return "workday" if day.weekday() < 5 else "rest"


def dedupe(punches: List[datetime]) -> List[datetime]:
    result: List[datetime] = []
    for p in sorted(punches):
        if not result or (p - result[-1]).total_seconds() > DEDUP_SECONDS:
            result.append(p)
    return result


def _severity_by_minutes(minutes: int) -> str:
    return "low" if minutes <= 15 else "medium" if minutes <= 60 else "high"


def _hm(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def evaluate_day(day: date, punches: List[datetime], rule: Dict[str, Any], kind: str, on_leave: bool,
                 leave_info: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """返回这一天的异常列表 [{type, severity, detail}]；没有异常返回空列表。"""
    start_t, end_t = parse_hhmm(rule["work_start"]), parse_hhmm(rule["work_end"])
    start, end = datetime.combine(day, start_t), datetime.combine(day, end_t)
    grace = timedelta(minutes=int(rule.get("grace_minutes", 5)))
    points = dedupe([p for p in punches if p.date() == day])
    base = {"punches": [_hm(p) for p in points], "work_start": rule["work_start"], "work_end": rule["work_end"]}
    found: List[Dict[str, Any]] = []

    def add(kind_: str, severity: str, **extra):
        found.append({"type": kind_, "severity": severity, "detail": {**base, **extra}})

    if kind in ("rest", "holiday"):
        if points:
            add("rest_day_work", "low", day_kind=kind, first=_hm(points[0]), last=_hm(points[-1]))
        return found
    if on_leave:
        if points:
            add("leave_conflict", "low", leave=leave_info or {})
        return found
    if not points:
        add("absent", "high")
        return found
    if len(points) == 1:
        midpoint = start + (end - start) / 2
        if points[0] < midpoint:
            add("missing_out", "medium", first=_hm(points[0]))
        else:
            add("missing_in", "medium", last=_hm(points[0]))
        return found
    first, last = points[0], points[-1]
    if first > start + grace:
        minutes = int((first - start).total_seconds() // 60)
        add("late", _severity_by_minutes(minutes), minutes=minutes, first=_hm(first))
    if last < end:
        minutes = int((end - last).total_seconds() // 60)
        add("early_leave", _severity_by_minutes(minutes), minutes=minutes, last=_hm(last))
    span_hours = (last - first).total_seconds() / 3600
    if span_hours > OVERLONG_HOURS:
        add("overlong", "medium", hours=round(span_hours, 1), first=_hm(first), last=_hm(last))
    return found
