"""考勤文件解析与异常规则（纯逻辑）：按现实里常见的导出格式构造文件——钉钉每日汇总、打卡机逐条明细（GBK CSV）、日期+时间两列。"""
import io
import unittest
from datetime import date, datetime, time

from openpyxl import Workbook

from service import attendance_import as imp
from service import attendance_rules as rules
from service.exceptions import InvalidInput


def xlsx(rows, extra_sheet=True):
    book = Workbook()
    sheet = book.active
    sheet.title = "考勤明细"
    for row in rows:
        sheet.append(row)
    if extra_sheet:
        other = book.create_sheet("说明")
        other.append(["本表由系统导出"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


class DatetimeParsingTest(unittest.TestCase):
    def test_common_formats(self):
        cases = {"2026-10-06 08:55:12": datetime(2026, 10, 6, 8, 55, 12), "2026/10/6 8:55": datetime(2026, 10, 6, 8, 55),
                 "2026年10月6日 08:55": datetime(2026, 10, 6, 8, 55), "2026-10-06 星期二": datetime(2026, 10, 6),
                 "2026-10-06 08:55（补卡）": datetime(2026, 10, 6, 8, 55), "2026.10.06 18:02:30": datetime(2026, 10, 6, 18, 2, 30),
                 datetime(2026, 10, 6, 9, 0, 0, 500): datetime(2026, 10, 6, 9, 0), date(2026, 10, 6): datetime(2026, 10, 6)}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(imp.parse_datetime(raw), expected)
        self.assertEqual(imp.parse_datetime("10-06 08:55", 2026), datetime(2026, 10, 6, 8, 55))
        self.assertEqual(imp.parse_datetime(46301.5), datetime(2026, 10, 6, 12, 0))                  # Excel 序列数
        for bad in ("", None, "abc", "2026-13-40 08:00", "缺卡", True):
            with self.subTest(bad=bad):
                self.assertIsNone(imp.parse_datetime(bad))

    def test_clock_values(self):
        self.assertEqual(imp.parse_clock("08:55"), time(8, 55))
        self.assertEqual(imp.parse_clock("8:55:03"), time(8, 55, 3))
        self.assertEqual(imp.parse_clock("08:55（补卡）"), time(8, 55))
        self.assertEqual(imp.parse_clock(0.375), time(9, 0))                                         # Excel 时间格
        self.assertEqual(imp.parse_clock(datetime(2026, 10, 6, 18, 5)), time(18, 5))
        for none in ("缺卡", "未打卡", "--", "", None, "25:99"):
            self.assertIsNone(imp.parse_clock(none), none)


class DingTalkDailySummaryTest(unittest.TestCase):
    """钉钉导出的“打卡时间”每日汇总：表头上面有标题和统计说明，日期带星期，班次带序号，缺卡写“缺卡”。"""

    def build(self):
        return xlsx([
            ["考勤报表"], ["统计时间：2026-10-01 至 2026-10-07"], [],
            ["姓名", "部门", "工号", "日期", "上班1打卡时间", "上班1打卡结果", "下班1打卡时间", "下班1打卡结果"],
            ["张三", "销售部", "A001", "2026-10-05 星期一", "08:55", "正常", "18:05", "正常"],
            ["张三", "销售部", "A001", "2026-10-06 星期二", "09:20", "迟到", "18:00", "正常"],
            ["李四", "销售部", "A002", "2026-10-05 星期一", "缺卡", "缺卡", "18:30", "正常"],
            ["李四", "销售部", "A002", "2026-10-06 星期二", None, None, None, None],
            ["合计", None, None, None, None, None, None, None],
        ])

    def test_header_found_below_title_rows_and_columns_mapped(self):
        parsed = imp.parse("钉钉考勤.xlsx", self.build())
        self.assertEqual(parsed["format"], "daily_summary")
        self.assertEqual(len(parsed["records"]), 4)
        self.assertEqual(parsed["period"], (date(2026, 10, 5), date(2026, 10, 6)))
        by = {(r["name"], r["day"]): r["punches"] for r in parsed["records"]}
        self.assertEqual(by[("张三", date(2026, 10, 5))], [datetime(2026, 10, 5, 8, 55), datetime(2026, 10, 5, 18, 5)])
        self.assertEqual(by[("李四", date(2026, 10, 5))], [datetime(2026, 10, 5, 18, 30)])          # “缺卡”不是解析失败
        self.assertEqual(by[("李四", date(2026, 10, 6))], [])
        self.assertEqual(parsed["skipped"], [])                                                       # “合计”行被忽略，不报错

    def test_row_numbers_point_at_the_file(self):
        parsed = imp.parse("a.xlsx", self.build())
        self.assertEqual(parsed["records"][0]["row"], 5)


class PunchRowsTest(unittest.TestCase):
    def test_gbk_csv_from_a_punch_machine(self):
        csv_text = "工号,姓名,打卡时间\r\n1001,张三,2026-10-06 08:58:11\r\n1001,张三,2026-10-06 18:03:40\r\n1002,李四,2026/10/6 9:31\r\n"
        parsed = imp.parse("门禁记录.csv", csv_text.encode("gb18030"))
        self.assertEqual(parsed["format"], "punch_rows")
        self.assertEqual([(r["name"], r["alt"]) for r in parsed["records"]], [("张三", "1001"), ("张三", "1001"), ("李四", "1002")])   # 姓名优先，工号作备选
        self.assertEqual(parsed["records"][2]["punches"], [datetime(2026, 10, 6, 9, 31)])

    def test_employee_number_alone_is_used_as_the_identity(self):
        parsed = imp.parse("a.csv", "工号,打卡时间\nA001,2026-10-06 08:58\n".encode("utf-8"))
        self.assertEqual((parsed["records"][0]["name"], parsed["records"][0]["alt"]), ("A001", ""))

    def test_name_column_with_utf8_bom(self):
        csv_text = "﻿姓名,打卡时间\n张三,2026-10-06 08:58\n"
        parsed = imp.parse("a.csv", csv_text.encode("utf-8"))
        self.assertEqual(parsed["records"][0]["name"], "张三")

    def test_separate_date_and_time_columns(self):
        parsed = imp.parse("a.xlsx", xlsx([["姓名", "日期", "打卡时刻"], ["张三", datetime(2026, 10, 6), "08:58"], ["张三", datetime(2026, 10, 6), 0.75]]))
        self.assertEqual(parsed["format"], "punch_rows")
        self.assertEqual([r["punches"][0] for r in parsed["records"]], [datetime(2026, 10, 6, 8, 58), datetime(2026, 10, 6, 18, 0)])

    def test_unparseable_rows_are_reported_not_dropped(self):
        parsed = imp.parse("a.csv", "姓名,打卡时间\n张三,2026-10-06 08:58\n李四,昨天早上\n,2026-10-06 09:00\n".encode("utf-8"))
        self.assertEqual(len(parsed["records"]), 1)
        self.assertEqual([(s["row"], s["reason"][:6]) for s in parsed["skipped"]], [(3, "打卡时间无法"), (4, "没有姓名/工")])

    def test_semicolon_and_tab_separated(self):
        for sep in (";", "\t"):
            parsed = imp.parse("a.csv", f"姓名{sep}打卡时间\n张三{sep}2026-10-06 08:58\n".encode("utf-8"))
            self.assertEqual(len(parsed["records"]), 1, repr(sep))


class RejectionTest(unittest.TestCase):
    def test_bad_files(self):
        cases = (("a.pdf", b"x", "支持 .xlsx"), ("a.csv", b"", "文件是空的"), ("a.xlsx", b"not a zip", "无法打开"),
                 ("a.csv", "月份,出勤天数\n10,22\n".encode(), "没有找到表头"), ("a.csv", "姓名,打卡时间\n".encode(), "没有数据行"))
        for name, content, word in cases:
            with self.subTest(name=name, word=word), self.assertRaises(InvalidInput) as ctx:
                imp.parse(name, content)
            self.assertIn(word, ctx.exception.message)

    def test_row_limit(self):
        from unittest.mock import patch
        with patch.object(imp, "MAX_ROWS", 2), self.assertRaises(InvalidInput):
            imp.parse("a.csv", ("姓名,打卡时间\n" + "张三,2026-10-06 08:58\n" * 20).encode())


class PunchMachineFileKindsTest(unittest.TestCase):
    """打卡机常见的“.xls”其实不是二进制 Excel：HTML 表格、Excel 2003 XML 表格、改了扩展名的 .xlsx。按内容识别，不看扩展名。"""

    HTML = ("<html><head><meta charset=\"gb2312\"></head><body><table><tr><td colspan=3>考勤记录表</td></tr>"
            "<tr><th>工号</th><th>姓名</th><th>打卡时间</th></tr>"
            "<tr><td>1001</td><td>张三</td><td>2026-10-06 08:58:11</td></tr>"
            "<tr><td>1001</td><td>张三</td><td>2026-10-06 18:03:40</td></tr>"
            "<tr><td>1002</td><td>李&nbsp;四</td><td>2026/10/6 9:31</td></tr></table></body></html>")
    XML = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><Workbook xmlns=\"urn:schemas-microsoft-com:office:spreadsheet\" "
           "xmlns:ss=\"urn:schemas-microsoft-com:office:spreadsheet\"><Worksheet ss:Name=\"明细\"><Table>"
           "<Row><Cell><Data ss:Type=\"String\">姓名</Data></Cell><Cell><Data ss:Type=\"String\">打卡时间</Data></Cell></Row>"
           "<Row><Cell><Data ss:Type=\"String\">张三</Data></Cell><Cell><Data ss:Type=\"String\">2026-10-06 08:58:11</Data></Cell></Row>"
           "<Row><Cell ss:Index=\"2\"><Data ss:Type=\"String\">2026-10-06 18:03:40</Data></Cell></Row>"
           "</Table></Worksheet></Workbook>")

    def test_html_table_saved_as_xls_in_gbk(self):
        parsed = imp.parse("打卡记录.xls", self.HTML.encode("gb18030"))
        self.assertEqual(parsed["format"], "punch_rows")
        self.assertEqual([(r["name"], r["alt"]) for r in parsed["records"]], [("张三", "1001"), ("张三", "1001"), ("李 四", "1002")])
        self.assertEqual(parsed["records"][0]["punches"], [datetime(2026, 10, 6, 8, 58, 11)])

    def test_html_table_in_utf8_with_bom(self):
        parsed = imp.parse("a.xls", b"\xef\xbb\xbf" + self.HTML.replace("gb2312", "utf-8").encode("utf-8"))
        self.assertEqual(len(parsed["records"]), 3)

    def test_excel_2003_xml_with_skipped_cells(self):
        parsed = imp.parse("导出.xls", self.XML.encode("utf-8"))
        self.assertEqual([r["name"] for r in parsed["records"]], ["张三"])           # 第二行省略了姓名单元格：没有姓名，被报告而不是悄悄丢掉
        self.assertEqual(len(parsed["skipped"]), 1)
        self.assertIn("没有姓名", parsed["skipped"][0]["reason"])

    def test_xlsx_renamed_to_xls_is_still_read(self):
        content = xlsx([["姓名", "打卡时间"], ["张三", "2026-10-06 08:58"]], extra_sheet=False)
        self.assertEqual(len(imp.parse("a.xls", content)["records"]), 1)

    def test_tab_separated_text(self):
        parsed = imp.parse("a.tsv", "姓名\t打卡时间\n张三\t2026-10-06 08:58\n".encode("utf-8"))
        self.assertEqual(len(parsed["records"]), 1)

    def test_binary_xls_gets_a_clear_instruction(self):
        with self.assertRaises(InvalidInput) as ctx:
            imp.parse("老设备.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
        self.assertIn("另存为 .xlsx", ctx.exception.message)

    def test_xml_with_entity_declaration_is_refused(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table/></Worksheet></Workbook>'
        with self.assertRaises(InvalidInput) as ctx:
            imp.parse("a.xls", bomb.encode("utf-8"))
        self.assertIn("不安全", ctx.exception.message)

    def test_html_without_a_table_and_unknown_extension(self):
        with self.assertRaises(InvalidInput):
            imp.parse("a.html", b"<html><body>hello</body></html>")
        with self.assertRaises(InvalidInput):
            imp.parse("a.doc", b"x")


RULE = {"work_start": "09:00", "work_end": "18:00", "grace_minutes": 5}
DAY = date(2026, 10, 6)


def at(h, m=0, day=DAY):
    return datetime.combine(day, time(h, m))


def types(found):
    return sorted(f["type"] for f in found)


class RulesTest(unittest.TestCase):
    def check(self, punches, kind="workday", leave=False):
        return rules.evaluate_day(DAY, punches, RULE, kind, leave)

    def test_normal_day_has_no_anomaly(self):
        self.assertEqual(self.check([at(8, 50), at(18, 10)]), [])
        self.assertEqual(self.check([at(9, 5), at(18, 0)]), [])                      # 宽限 5 分钟内

    def test_late_and_early_with_minutes_and_severity(self):
        late = self.check([at(9, 6), at(18, 0)])
        self.assertEqual((types(late), late[0]["detail"]["minutes"], late[0]["severity"]), (["late"], 6, "low"))
        both = self.check([at(10, 30), at(17, 0)])
        self.assertEqual(types(both), ["early_leave", "late"])
        self.assertEqual({f["type"]: f["severity"] for f in both}, {"late": "high", "early_leave": "medium"})   # 90 分钟 / 60 分钟

    def test_missing_cards(self):
        self.assertEqual(types(self.check([at(8, 55)])), ["missing_out"])
        self.assertEqual(types(self.check([at(18, 20)])), ["missing_in"])
        self.assertEqual(types(self.check([at(13, 30)])), ["missing_in"])             # 正好过中点按缺上班卡算

    def test_absent_only_without_leave(self):
        self.assertEqual(types(self.check([])), ["absent"])
        self.assertEqual(self.check([], leave=True), [])

    def test_leave_with_punches_is_a_conflict_not_an_absence(self):
        self.assertEqual(types(self.check([at(9, 0), at(18, 0)], leave=True)), ["leave_conflict"])

    def test_rest_days_and_holidays(self):
        self.assertEqual(self.check([], kind="rest"), [])
        self.assertEqual(self.check([], kind="holiday", leave=True), [])
        found = self.check([at(10, 0), at(15, 0)], kind="holiday")
        self.assertEqual((types(found), found[0]["severity"]), (["rest_day_work"], "low"))

    def test_double_tap_counts_once_and_other_days_are_ignored(self):
        self.assertEqual(types(self.check([at(8, 55), datetime(2026, 10, 6, 8, 55, 40)])), ["missing_out"])
        self.assertEqual(types(self.check([at(9, 0, day=date(2026, 10, 5))])), ["absent"])        # 前一天的打卡不算今天的

    def test_overlong(self):
        found = self.check([at(6, 0), at(21, 0)])
        self.assertEqual(types(found), ["overlong"])
        self.assertEqual(found[0]["detail"]["hours"], 15.0)

    def test_per_team_rule(self):
        early_shift = {"work_start": "08:00", "work_end": "17:00", "grace_minutes": 0}
        self.assertEqual(rules.evaluate_day(DAY, [at(8, 1), at(17, 0)], early_shift, "workday", False)[0]["detail"]["minutes"], 1)

    def test_rule_validation(self):
        self.assertEqual(rules.validate_rule("9:00", "18:30", 10), ("09:00", "18:30", 10, 0))
        self.assertEqual(rules.validate_rule("9:00", "18:30", 10, 60), ("09:00", "18:30", 10, 60))
        for bad in (("18:00", "09:00", 5), ("09:00", "18:00", 90), ("abc", "18:00", 5), ("09:00", "18:00", -1),
                    ("09:00", "18:00", 5, 181), ("09:00", "18:00", 5, -1)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                rules.validate_rule(*bad)

    def test_flexible_start(self):
        """弹性 60 分钟：9:00–10:00 之间到岗不算迟到，晚到多少晚走多少；超过 10:00 + 宽限才算迟到，从 10:00 算起。"""
        flex = {"work_start": "09:00", "work_end": "18:00", "grace_minutes": 5, "flex_minutes": 60}
        types = lambda punches: {i["type"]: i["detail"] for i in rules.evaluate_day(DAY, punches, flex, "workday", False)}   # noqa: E731
        self.assertEqual(types([at(9, 40), at(18, 40)]), {}, "晚到 40 分钟、晚走 40 分钟：正常")
        self.assertEqual(types([at(8, 30), at(18, 0)]), {}, "早到不用提前走，也不用多留")
        early = types([at(9, 40), at(18, 10)])["early_leave"]
        self.assertEqual((early["minutes"], early["required_end"]), (30, "18:40"))
        late = types([at(10, 20), at(19, 0)])
        self.assertEqual(late["late"]["minutes"], 20, "从弹性截止 10:00 算起")
        self.assertNotIn("early_leave", late, "晚走最多补满弹性时长：19:00 就够了")
        self.assertEqual(types([at(9, 3), at(18, 3)]), {}, "没有弹性的旧规则不受影响的写法也成立")
        no_flex = {"work_start": "09:00", "work_end": "18:00", "grace_minutes": 5}
        self.assertEqual(rules.evaluate_day(DAY, [at(9, 40), at(18, 40)], no_flex, "workday", False)[0]["type"], "late")

    def test_default_calendar_is_monday_to_friday(self):
        self.assertEqual([rules.default_day_kind(date(2026, 10, d)) for d in (5, 9, 10, 11)], ["workday", "workday", "rest", "rest"])


if __name__ == "__main__":
    unittest.main()
