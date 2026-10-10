"""考勤“月度汇总”格式：一人一行、一天一列（钉钉“月度汇总 / 打卡时间”、企业微信“月报”、很多打卡机的“考勤月报表”）。

    姓名 | 工号 | 1 | 2 | 3 | … | 31
    张三 | E001 | 08:55 18:02 | 09:01\n18:10 | 休息 | …

和“逐条打卡”“每日汇总”不同，日期在表头上：表头格子可能是 “1” “01” “1日” “1 二” “1\n星期二” “10-01” “10月1日”，
也可能是 Excel 日期格。单元格里可能有一次或多次打卡（空格、换行、逗号、“-”隔开），也可能有“次日 02:10”（跨夜班）。

只写了状态（“正常”“迟到”）却没有时间的格子没法判断迟到早退，不能当成“没打卡”（那会被判成旷工）：
  - 整张表一个时间都没有：是只有统计数字的月报，直接拒绝并说明要导出哪种表；
  - 个别格子只有文字：记进“跳过”，人事在导入结果里能看到；“休息”“请假”这类本来就不该有打卡的词不算。
年月：表头是完整日期就用表头；只有“几号”时，从表格上方的标题（“2026年10月考勤汇总”“统计周期 2026-10-01 至 …”）或文件名里找。
"""
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from service.exceptions import InvalidInput

HEADER_SEARCH_ROWS = 15
MIN_DAY_COLUMNS = 7
NAME_HEADERS = ("姓名", "员工姓名", "名字", "人员", "员工", "用户名", "账号", "name")
EMPNO_HEADERS = ("工号", "员工编号", "员工工号", "人员编号", "考勤号", "编号", "emp_no")
# 不该有打卡的状态：出现在格子里不算“缺时间”
REST_WORDS = ("休息", "休", "公休", "节假日", "法定假", "调休", "请假", "年假", "事假", "病假", "婚假", "产假", "丧假", "-", "--", "—", "/")

_WEEKDAY_SUFFIX = r"\s*[(（]?\s*(?:周|星期)?\s*[一二三四五六日天]?\s*[)）]?"
_DAY_ONLY = re.compile(r"^0?(\d{1,2})\s*(?:日|号)?" + _WEEKDAY_SUFFIX + r"$")
_MONTH_DAY = re.compile(r"^(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?" + _WEEKDAY_SUFFIX + r"$")
_FULL_DATE = re.compile(r"^(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?" + _WEEKDAY_SUFFIX + r"$")
_YEAR_MONTH = re.compile(r"(\d{4})\s*(?:年|[-/.])\s*(\d{1,2})(?:\s*月|(?=[-/.]\d)|\b)")
_TIME = re.compile(r"(次日)?\s*(?<!\d)([01]?\d|2[0-3])\s*[:：]\s*([0-5]\d)(?:\s*[:：]\s*[0-5]\d)?(?!\d)")


def _norm(value: Any) -> str:
    return re.sub(r"[\s　（）()：:*]+", "", str(value or "")).lower()


def _is(cell: Any, names) -> bool:
    return _norm(cell) in {_norm(n) for n in names}


def day_header(cell: Any) -> Optional[Tuple[Optional[int], Optional[int], int]]:
    """表头格子 → (年 或 None, 月 或 None, 日)；不是日期列返回 None。"""
    if isinstance(cell, datetime):
        return cell.year, cell.month, cell.day
    if isinstance(cell, date):
        return cell.year, cell.month, cell.day
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        return (None, None, int(cell)) if float(cell).is_integer() and 1 <= cell <= 31 else None
    text = re.sub(r"\s+", " ", str(cell or "")).strip()
    if not text:
        return None
    m = _FULL_DATE.match(text)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = _MONTH_DAY.match(text)
    if m and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31:
        return None, int(m.group(1)), int(m.group(2))
    m = _DAY_ONLY.match(text)
    if m and 1 <= int(m.group(1)) <= 31:
        return None, None, int(m.group(1))
    return None


def find_matrix_header(rows: List[List[Any]]):
    """在前 15 行里找“姓名（或工号）+ 至少 7 个日期列”的表头。找不到返回 None。"""
    for index, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        name_col = next((c for c, cell in enumerate(row) if _is(cell, NAME_HEADERS)), None)
        empno_col = next((c for c, cell in enumerate(row) if _is(cell, EMPNO_HEADERS)), None)
        days = {c: d for c, cell in enumerate(row) if c not in (name_col, empno_col) and (d := day_header(cell)) is not None}
        if (name_col is not None or empno_col is not None) and len(days) >= MIN_DAY_COLUMNS:
            return index, (name_col if name_col is not None else empno_col), (empno_col if name_col is not None else None), days
    return None


def _year_month(rows: List[List[Any]], header_index: int, file_name: str) -> Optional[Tuple[int, int]]:
    texts = [str(c) for row in rows[:header_index + 1] for c in row if c not in (None, "")] + [file_name or ""]
    for text in texts:
        m = _YEAR_MONTH.search(text)
        if m and 2000 <= int(m.group(1)) <= 2100 and 1 <= int(m.group(2)) <= 12:
            return int(m.group(1)), int(m.group(2))
    return None


def cell_times(value: Any, day: date) -> List[datetime]:
    """格子里所有打卡时间（“08:55 18:02”“08:55-18:02”“次日 02:10”）。Excel 时间 / 日期时间格同样认。"""
    if isinstance(value, datetime):
        return [datetime.combine(day, value.time().replace(microsecond=0))] if value.time() != datetime.min.time() else []
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < float(value) < 1:
        return [datetime.combine(day, datetime.min.time()) + timedelta(seconds=round(float(value) * 86400))]
    result = []
    for m in _TIME.finditer(str(value or "")):
        moment = datetime(day.year, day.month, day.day, int(m.group(2)), int(m.group(3)))
        result.append(moment + timedelta(days=1) if m.group(1) else moment)
    return result


def parse_matrix(rows: List[List[Any]], file_name: str = "") -> Optional[Dict[str, Any]]:
    """识别成功返回和 attendance_import.parse 一样结构的结果；不是月度汇总格式返回 None。"""
    found = find_matrix_header(rows)
    if found is None:
        return None
    header_index, name_col, empno_col, day_cols = found
    title = _year_month(rows, header_index, file_name)
    columns: Dict[int, date] = {}
    for col, (year, month, day) in day_cols.items():
        if month is None:
            if title is None:
                raise InvalidInput("这是月度汇总表（一天一列），但没找到是哪一年哪个月：请保留表格上方的标题"
                                   "（如“2026年10月考勤汇总”），或在文件名里写上年月（如“考勤_2026-10.xlsx”）")
            year, month = title
        elif year is None:
            year = title[0] if title else datetime.now().year
        try:
            columns[col] = date(year, month, day)
        except ValueError:
            continue                                     # 2 月 30 日这类不存在的列（模板固定 31 列）
    records: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    body = rows[header_index + 1:]
    any_time = False
    for offset, row in enumerate(body):
        number = header_index + 2 + offset
        cells = list(row) + [None] * 40
        name = str(cells[name_col] if cells[name_col] is not None else "").strip()
        if not any(str(c).strip() for c in row if c is not None):
            continue
        if not name or name in ("合计", "总计", "小计"):
            if not name:
                skipped.append({"row": number, "reason": "没有姓名/工号"})
            continue
        alt = str(cells[empno_col]).strip() if empno_col is not None and cells[empno_col] is not None else ""
        for col, day in sorted(columns.items(), key=lambda kv: kv[1]):
            value = cells[col] if col < len(cells) else None
            punches = cell_times(value, day)
            text = str(value).strip() if value is not None else ""
            if punches:
                any_time = True
            elif text and text not in REST_WORDS and not any(w in text for w in ("休息", "请假", "假")):
                skipped.append({"row": number, "reason": f"{name} {day.month}月{day.day}日：「{text[:20]}」没有打卡时间，"
                                                          "这一天没有导入，分析时可能被当成缺卡，请核对"})
                continue
            records.append({"name": name, "alt": alt, "punches": punches, "row": number, "day": day})
    if not records and not skipped:
        raise InvalidInput("表头下面没有数据行")
    if not any_time:
        raise InvalidInput("这份月度汇总表里只有“正常 / 迟到”这类状态，没有打卡时间，无法判断迟到早退："
                           "请导出带打卡时间的月度汇总（钉钉“打卡时间”报表、企业微信“每日统计”）或逐条打卡明细")
    days = [r["day"] for r in records]
    return {"format": "monthly_matrix", "records": records, "skipped": skipped[:200], "skipped_total": len(skipped),
            "row_count": len(body), "period": (min(days), max(days)) if days else (None, None),
            "columns": {"name": str(rows[header_index][name_col]).strip(), "format": "monthly_matrix"}}
