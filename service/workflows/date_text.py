"""把原文里的期限说法（"下周五""10月15日前""月底""三天后"）确定性地换算成具体日期。

责任事项的截止日期不能让模型凭空给：模型只负责摘出原文里的期限说法（必须逐字出现在原文中），
具体日期由这里按"今天"换算；换算不了的（"尽快""近期""下周内"）返回 None，由用户补充。
"""
import calendar
import re
from datetime import date, timedelta
from typing import Optional

_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "末": 5}
_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _number(raw: str) -> Optional[int]:
    """阿拉伯数字或 1–99 的中文数字（十五、二十、二十五、三十一）。"""
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if "十" in raw:
        tens, _, ones = raw.partition("十")
        t = 1 if tens == "" else _DIGITS.get(tens)
        o = 0 if ones == "" else _DIGITS.get(ones)
        return None if t is None or o is None else t * 10 + o
    return _DIGITS.get(raw) if len(raw) == 1 else None


_NUM = r"(\d{1,2}|[一二两三四五六七八九十]{1,3})"


def _safe(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_day(today: date, month: int, day: int) -> Optional[date]:
    result = _safe(today.year, month, day)
    if result is not None and (today - result).days > 60:
        result = _safe(today.year + 1, month, day)   # 已经过去很久的月日，说的是明年
    return result


def _add_months(today: date, delta: int) -> tuple:
    index = today.year * 12 + today.month - 1 + delta
    return index // 12, index % 12 + 1


def resolve_due(text: str, today: date) -> Optional[date]:
    """text 是原文里的期限说法；today 是"今天"（北京时间）。无法确定具体日期时返回 None。"""
    raw = (text or "").strip()
    if not raw:
        return None
    iso = re.search(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", raw)
    if iso:
        return _safe(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    # 相对天/周
    if "大后天" in raw:
        return today + timedelta(days=3)
    if "后天" in raw:
        return today + timedelta(days=2)
    if "明天" in raw or "明日" in raw:
        return today + timedelta(days=1)
    if "今天" in raw or "今日" in raw:
        return today
    ahead = re.search(_NUM + r"\s*(?:个)?(天|日|周|星期|礼拜)\s*(?:后|内)", raw)
    if ahead:   # "三天后""三天内"都按今天往后数（内 = 最迟到那天）
        n = _number(ahead.group(1))
        if n is not None:
            return today + timedelta(days=n if ahead.group(2) in ("天", "日") else n * 7)
    # 月底 / 年底
    month_end = re.search(r"(?:(下个?月|本月|这个月|" + _NUM + r"\s*月)\s*)?(?:底|末)", raw)
    if "年底" in raw:
        return date(today.year, 12, 31)
    if month_end and ("月" in raw):
        label = month_end.group(1) or ""
        if label.startswith("下"):
            year, month = _add_months(today, 1)
        elif label in ("", "本月", "这个月"):
            year, month = today.year, today.month
        else:
            n = _number(month_end.group(2) or "")
            if n is None or not 1 <= n <= 12:
                return None
            year, month = today.year, n
            if date(year, month, calendar.monthrange(year, month)[1]) < today - timedelta(days=60):
                year += 1
        return date(year, month, calendar.monthrange(year, month)[1])
    # 下月X日 / 本月X日
    rel_month = re.search(r"(下个?月|本月|这个月)\s*" + _NUM + r"\s*(?:日|号)", raw)
    if rel_month:
        year, month = _add_months(today, 1 if rel_month.group(1).startswith("下") else 0)
        day = _number(rel_month.group(2))
        return _safe(year, month, day) if day else None
    # X月X日/号
    md = re.search(_NUM + r"\s*月\s*" + _NUM + r"\s*(?:日|号)", raw)
    if md:
        month, day = _number(md.group(1)), _number(md.group(2))
        return _month_day(today, month, day) if month and day else None
    slash = re.search(r"(?<!\d)(\d{1,2})\s*[/.]\s*(\d{1,2})(?!\d)", raw)
    if slash:
        return _month_day(today, int(slash.group(1)), int(slash.group(2)))
    # 周几
    weekday = re.search(r"(下下|下|本|这|上)?\s*(?:个)?(?:周|星期|礼拜)\s*([一二三四五六日天])", raw)
    if weekday:
        prefix, target = weekday.group(1) or "", _WEEKDAYS[weekday.group(2)]
        monday = today - timedelta(days=today.weekday())
        offset = {"下下": 2, "下": 1, "上": -1}.get(prefix, 0)
        result = monday + timedelta(weeks=offset, days=target)
        if prefix in ("", ) and result < today:
            result += timedelta(weeks=1)   # 只说"周五"且本周五已过，指下周五
        return result
    day_only = re.search(_NUM + r"\s*(?:日|号)(?:前|之前|底前)?", raw)
    if day_only:
        day = _number(day_only.group(1))
        if day and 1 <= day <= 31:
            result = _safe(today.year, today.month, day)
            if result is not None and result < today:
                year, month = _add_months(today, 1)
                result = _safe(year, month, day)
            return result
    return None
