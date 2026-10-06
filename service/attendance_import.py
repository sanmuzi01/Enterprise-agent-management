"""考勤文件解析：读懂打卡机、钉钉、企业微信等导出的 Excel / CSV。

现实里的考勤导出格式五花八门，所以这里不要求固定模板，而是按表头的常见叫法自动识别两种最常见的形态：
1. **逐条打卡**：每行一次打卡——姓名（或工号）+ 打卡时间（一列完整的日期时间，或“日期”“时间”两列）；
2. **每日汇总**：每人每天一行——姓名 + 日期 + 上班打卡时间 + 下班打卡时间（钉钉“打卡时间”报表、企业微信“每日统计”常见这种；
   “上班1打卡时间 / 下班1打卡时间”这类带序号的表头也认，多个班次取最早和最晚）。
钉钉/企业微信的导出常在表头上方有标题行、说明行，所以会在前 15 行里找表头；日期时间支持 Excel 日期格、
“2026-10-06 08:55:12”“2026/10/6 8:55”“2026年10月6日 08:55”“10-06 08:55（按文件里的年份推断）”和 Excel 序列数。
解析不了的行不会悄悄丢掉：记入 skipped（行号 + 原因），导入结果里原样展示。只读文件，不保存文件。
"""
import csv
import io
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from service.exceptions import InvalidInput

MAX_ROWS = 50000
HEADER_SEARCH_ROWS = 15

NAME_HEADERS = ("姓名", "员工姓名", "名字", "人员", "员工", "用户名", "账号", "name")
EMPNO_HEADERS = ("工号", "员工编号", "员工工号", "人员编号", "考勤号", "编号", "emp_no")
PUNCH_HEADERS = ("打卡时间", "刷卡时间", "考勤时间", "签到时间", "打卡日期时间", "时间", "punch_time", "datetime")
DATE_HEADERS = ("日期", "考勤日期", "工作日期", "打卡日期", "date")
CLOCK_HEADERS = ("打卡时刻", "具体时间", "刷卡时刻")
IN_HEADERS = ("上班打卡时间", "上班时间", "上班打卡", "签到", "首次打卡", "最早打卡", "第一次打卡", "上班", "最早签到")
OUT_HEADERS = ("下班打卡时间", "下班时间", "下班打卡", "签退", "末次打卡", "最晚打卡", "最后一次打卡", "下班", "最晚签退")
# 带班次序号：上班1打卡时间、下班2打卡时间、上班1签到…
_SHIFT_IN = re.compile(r"^上班\s*\d*\s*(打卡时间|打卡|签到|时间)?$")
_SHIFT_OUT = re.compile(r"^下班\s*\d*\s*(打卡时间|打卡|签退|时间)?$")


def _norm(value: Any) -> str:
    return re.sub(r"[\s　（）()：:*]+", "", str(value or "")).lower()


def _match(header: str, names) -> bool:
    h = _norm(header)
    return any(h == _norm(n) for n in names)


def _read_csv(content: bytes) -> List[List[Any]]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise InvalidInput("无法识别文件编码，请另存为 UTF-8 或 GBK 的 CSV")
    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ("," if sample.count(",") >= sample.count(";") else ";")
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _read_xlsx(content: bytes) -> List[List[Any]]:
    from openpyxl import load_workbook
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001
        raise InvalidInput("Excel 文件无法打开，请确认没有加密、没有损坏，格式是 .xlsx") from None
    try:
        best: List[List[Any]] = []
        for sheet in workbook.worksheets:        # 多个工作表时取行数最多的那个（汇总页、说明页通常很短）
            rows = []
            for row in sheet.iter_rows(values_only=True):
                rows.append(list(row))
                if len(rows) > MAX_ROWS + HEADER_SEARCH_ROWS:
                    raise InvalidInput(f"一次最多导入 {MAX_ROWS:,} 行，请按月或按部门拆分文件")
            if len(rows) > len(best):
                best = rows
        return best
    finally:
        workbook.close()


def read_rows(file_name: str, content: bytes) -> List[List[Any]]:
    lower = (file_name or "").lower()
    if lower.endswith(".xlsx"):
        return _read_xlsx(content)
    if lower.endswith((".csv", ".txt")):
        rows = _read_csv(content)
        if len(rows) > MAX_ROWS + HEADER_SEARCH_ROWS:
            raise InvalidInput(f"一次最多导入 {MAX_ROWS:,} 行，请按月或按部门拆分文件")
        return rows
    raise InvalidInput("考勤文件支持 .xlsx 和 .csv（旧版 .xls 请先另存为 .xlsx）")


# ---------------------------------------------------------------- 日期时间

_DT_PATTERNS = (
    r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?[ T]*(\d{1,2})[:：时](\d{2})(?:[:：分](\d{2}))?秒?$",
    r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$",
)
_MD_TIME = re.compile(r"^(\d{1,2})[-/月](\d{1,2})日?[ T]*(\d{1,2})[:：](\d{2})(?::(\d{2}))?$")
_CLOCK = re.compile(r"^(\d{1,2})[:：](\d{2})(?:[:：](\d{2}))?$")
_EXCEL_EPOCH = datetime(1899, 12, 30)


_WEEKDAY = re.compile(r"\s*(星期|周)[一二三四五六日天]")
_NOTE = re.compile(r"[（(][^）)]*[）)]")


def _clean_text(text: str) -> str:
    """去掉导出里常带的星期（“2026-10-06 星期二”）和备注（“08:55（补卡）”）。"""
    return _NOTE.sub("", _WEEKDAY.sub("", text)).strip()


def parse_datetime(value: Any, default_year: Optional[int] = None) -> Optional[datetime]:
    """能解析出“日期+时间”返回 datetime；只有日期返回当天 00:00；解析不了返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0, tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 20000 < float(value) < 80000:      # Excel 序列数（2000–2100 年）
            return (_EXCEL_EPOCH + timedelta(days=float(value))).replace(microsecond=0)
        return None
    text = _clean_text(str(value))
    if not text:
        return None
    for pattern in _DT_PATTERNS:
        m = re.match(pattern, text)
        if m:
            parts = [int(x) if x else 0 for x in m.groups()]
            parts += [0] * (6 - len(parts))
            try:
                return datetime(*parts[:3], *parts[3:6])
            except ValueError:
                return None
    m = _MD_TIME.match(text)
    if m and default_year:
        month, day, hour, minute, second = (int(x) if x else 0 for x in m.groups())
        try:
            return datetime(default_year, month, day, hour, minute, second)
        except ValueError:
            return None
    return None


def parse_clock(value: Any) -> Optional[time]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.time().replace(microsecond=0)
    if isinstance(value, time):
        return value.replace(microsecond=0)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= float(value) < 1:   # Excel 时间格存成一天的小数
        seconds = round(float(value) * 86400)
        return (datetime(2000, 1, 1) + timedelta(seconds=seconds)).time()
    text = _clean_text(str(value))
    if text in ("", "-", "--", "—", "缺卡", "未打卡", "旷工"):
        return None
    m = _CLOCK.match(text)
    if m:
        hour, minute, second = (int(x) if x else 0 for x in m.groups())
        if hour < 24 and minute < 60 and second < 60:
            return time(hour, minute, second)
    full = parse_datetime(text)
    return full.time() if full and full.time() != time(0, 0) else None


# ---------------------------------------------------------------- 表头识别

def find_header(rows: List[List[Any]]) -> Tuple[int, Dict[str, Any]]:
    """在前若干行里找表头，返回 (表头行号, 列映射)。映射里只放识别到的列，调用方判断格式够不够。"""
    for index, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        mapping: Dict[str, Any] = {"in": [], "out": []}
        for col, cell in enumerate(row):
            h = _norm(cell)
            if not h:
                continue
            if "name" not in mapping and _match(cell, NAME_HEADERS):
                mapping["name"] = col
            elif "empno" not in mapping and _match(cell, EMPNO_HEADERS):
                mapping["empno"] = col
            elif "punch" not in mapping and _match(cell, PUNCH_HEADERS):
                mapping["punch"] = col
            elif "date" not in mapping and _match(cell, DATE_HEADERS):
                mapping["date"] = col
            elif "clock" not in mapping and _match(cell, CLOCK_HEADERS):
                mapping["clock"] = col
            elif _match(cell, IN_HEADERS) or _SHIFT_IN.match(h):
                mapping["in"].append(col)
            elif _match(cell, OUT_HEADERS) or _SHIFT_OUT.match(h):
                mapping["out"].append(col)
        if "name" not in mapping and "empno" in mapping:
            mapping["name"] = mapping.pop("empno")          # 只有工号没有姓名：用工号当名字去匹配
        if "name" in mapping and ("punch" in mapping or ("date" in mapping and "clock" in mapping) or (mapping["in"] and mapping["out"])
                                  or ("date" in mapping and (mapping["in"] or mapping["out"]))):
            return index, mapping
    raise InvalidInput("没有找到表头：文件里需要有“姓名（或工号）”，以及“打卡时间”或“日期 + 上班/下班打卡时间”。"
                       "请确认导出的是考勤明细或每日汇总，而不是只有统计数字的月报")


def parse(file_name: str, content: bytes) -> Dict[str, Any]:
    """返回 {format, records: [{name, punches: [datetime...], row}], skipped: [{row, reason}], row_count, period}。"""
    rows = read_rows(file_name, content)
    if not rows:
        raise InvalidInput("文件是空的")
    header_index, cols = find_header(rows)
    if "punch" in cols or ("date" in cols and "clock" in cols):
        fmt = "punch_rows"
    else:
        fmt = "daily_summary"
    years = [d.year for row in rows[header_index + 1:header_index + 50] for c in row if (d := parse_datetime(c)) is not None]
    default_year = max(set(years), key=years.count) if years else None
    records: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    body = rows[header_index + 1:]
    for offset, row in enumerate(body):
        number = header_index + 2 + offset            # 文件里的行号（从 1 开始）
        cells = list(row) + [None] * 40
        name = str(cells[cols["name"]] if cols["name"] < len(cells) else "").strip()
        if not any(str(c).strip() for c in row if c is not None):
            continue                                  # 空行
        if not name or name in ("合计", "总计", "小计"):
            if name:
                continue
            skipped.append({"row": number, "reason": "没有姓名/工号"})
            continue
        punches: List[datetime] = []
        if fmt == "punch_rows":
            if "punch" in cols:
                moment = parse_datetime(cells[cols["punch"]], default_year)
                if moment is None or moment.time() == time(0, 0) and not isinstance(cells[cols["punch"]], datetime):
                    skipped.append({"row": number, "reason": f"打卡时间无法识别：{cells[cols['punch']]!r}"})
                    continue
                punches.append(moment)
            else:
                day, clock = parse_datetime(cells[cols["date"]], default_year), parse_clock(cells[cols["clock"]])
                if day is None or clock is None:
                    skipped.append({"row": number, "reason": f"日期或时间无法识别：{cells[cols['date']]!r} {cells[cols['clock']]!r}"})
                    continue
                punches.append(datetime.combine(day.date(), clock))
        else:
            day = parse_datetime(cells[cols["date"]], default_year)
            if day is None:
                skipped.append({"row": number, "reason": f"日期无法识别：{cells[cols['date']]!r}"})
                continue
            for col in cols["in"] + cols["out"]:
                value = cells[col]
                full = parse_datetime(value, default_year)
                clock = full.time() if full and full.time() != time(0, 0) and (isinstance(value, datetime) or " " in str(value) or "T" in str(value)) else parse_clock(value)
                if clock is None:
                    continue                                   # 这一格没打卡（缺卡）是正常的数据，不算解析失败
                stamp = datetime.combine(full.date() if full and (isinstance(value, datetime) or " " in str(value)) else day.date(), clock)
                punches.append(stamp)
        alt = str(cells[cols["empno"]]).strip() if "empno" in cols and cols["empno"] < len(cells) and cells[cols["empno"]] is not None else ""
        records.append({"name": name, "alt": alt, "punches": punches, "row": number, "day": None if fmt == "punch_rows" else day.date()})
    if not records and not skipped:
        raise InvalidInput("表头下面没有数据行")
    stamps = [p for r in records for p in r["punches"]]
    days = [r["day"] for r in records if r["day"]] or [p.date() for p in stamps]
    return {"format": fmt, "records": records, "skipped": skipped[:200], "skipped_total": len(skipped),
            "row_count": len(body), "period": (min(days), max(days)) if days else (None, None),
            "columns": {"name": str(rows[header_index][cols["name"]]).strip(), "format": fmt}}
