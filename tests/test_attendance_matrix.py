"""考勤“月度汇总”格式（一人一行、一天一列）：service/attendance_matrix.py，经 attendance_import.parse 进入。"""
import io
import unittest
from datetime import date, datetime, time

from openpyxl import Workbook

from service import attendance_import as imp
from service import attendance_matrix as mx
from service.exceptions import InvalidInput


def xlsx(rows):
    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def by_day(result, name):
    return {r["day"]: r["punches"] for r in result["records"] if r["name"] == name}


DAYS = [str(d) for d in range(1, 11)]


class MonthlyMatrixTest(unittest.TestCase):
    def dingtalk_like(self):
        return xlsx([
            ["2026年10月 考勤月度汇总"],
            ["统计周期：2026-10-01 至 2026-10-31"],
            ["姓名", "工号", *DAYS],
            ["张三", "E001", "08:55 18:02", "09:01\n18:10", "休息", "正常", "08:50-18:00",
             "08:57,12:01,18:05", "", "次日 02:10", "事假", "--"],
            ["合计", "", *[""] * 10],
        ])

    def test_dingtalk_monthly_summary_is_read(self):
        result = imp.parse("考勤月报.xlsx", self.dingtalk_like())
        self.assertEqual(result["format"], "monthly_matrix")
        self.assertEqual(imp.FORMAT_LABELS[result["format"]], "月度汇总")
        days = by_day(result, "张三")
        self.assertEqual(days[date(2026, 10, 1)], [datetime(2026, 10, 1, 8, 55), datetime(2026, 10, 1, 18, 2)])
        self.assertEqual(days[date(2026, 10, 2)], [datetime(2026, 10, 2, 9, 1), datetime(2026, 10, 2, 18, 10)])
        self.assertEqual(days[date(2026, 10, 5)], [datetime(2026, 10, 5, 8, 50), datetime(2026, 10, 5, 18, 0)])
        self.assertEqual(len(days[date(2026, 10, 6)]), 3, "一格里多次打卡都要")
        self.assertEqual(days[date(2026, 10, 8)], [datetime(2026, 10, 9, 2, 10)], "次日 = 跨夜班，算到第二天")
        self.assertEqual(days[date(2026, 10, 3)], [], "休息：没有打卡是正常的")
        self.assertEqual(result["records"][0]["alt"], "E001")
        self.assertEqual(result["period"], (date(2026, 10, 1), date(2026, 10, 10)))

    def test_status_without_time_is_reported_not_treated_as_no_punch(self):
        result = imp.parse("考勤月报.xlsx", self.dingtalk_like())
        self.assertNotIn(date(2026, 10, 4), by_day(result, "张三"), "“正常”没有时间：这一天不导入")
        reasons = [s["reason"] for s in result["skipped"]]
        self.assertTrue(any("10月4日" in r and "正常" in r and "请核对" in r for r in reasons), reasons)
        self.assertFalse(any("事假" in r or "休息" in r or "--" in r for r in reasons), "休息、请假这类不该提示")

    def test_status_only_report_is_refused_with_instructions(self):
        content = xlsx([["2026年10月"], ["姓名", *DAYS], ["张三", *["正常"] * 9, "迟到"]])
        with self.assertRaises(InvalidInput) as ctx:
            imp.parse("月报.xlsx", content)
        self.assertIn("没有打卡时间", str(ctx.exception))

    def test_month_comes_from_title_file_name_or_header(self):
        rows = [["姓名", *DAYS], ["李四", *["09:00 18:00"] * 10]]
        with self.assertRaises(InvalidInput) as ctx:
            imp.parse("月报.xlsx", xlsx(rows))
        self.assertIn("哪一年哪个月", str(ctx.exception))
        from_name = imp.parse("考勤_2026-09.xlsx", xlsx(rows))
        self.assertEqual(from_name["period"][0], date(2026, 9, 1))
        headed = xlsx([["2026年度"], ["姓名", *[f"10-{d:02d} 星期{'一二三四五六日'[(d + 2) % 7]}" for d in range(1, 11)]],
                       ["李四", *["09:00 18:00"] * 10]])
        self.assertEqual(imp.parse("x.xlsx", headed)["period"], (date(2026, 10, 1), date(2026, 10, 10)))

    def test_excel_date_headers_and_time_cells(self):
        rows = [["姓名", *[datetime(2026, 10, d) for d in range(1, 9)]],
                ["王五", *[time(8, 58)] * 8]]
        result = imp.parse("x.xlsx", xlsx(rows))
        self.assertEqual(by_day(result, "王五")[date(2026, 10, 3)], [datetime(2026, 10, 3, 8, 58)])

    def test_days_that_do_not_exist_in_the_month_are_ignored(self):
        rows = [["2026年2月"], ["姓名", *[str(d) for d in range(1, 32)]], ["赵六", *["09:00 18:00"] * 31]]
        result = imp.parse("x.xlsx", xlsx(rows))
        self.assertEqual(result["period"], (date(2026, 2, 1), date(2026, 2, 28)))

    def test_existing_formats_are_unaffected(self):
        daily = xlsx([["姓名", "日期", "上班打卡时间", "下班打卡时间"], ["张三", "2026-10-06", "08:55", "18:02"]])
        self.assertEqual(imp.parse("x.xlsx", daily)["format"], "daily_summary")
        with self.assertRaises(InvalidInput):
            imp.parse("x.xlsx", xlsx([["随便", "什么"], ["1", "2"]]))

    def test_header_cell_forms(self):
        for cell, expected in (("1", (None, None, 1)), ("01日", (None, None, 1)), ("5 二", (None, None, 5)),
                               ("5\n星期二", (None, None, 5)), ("10-01", (None, 10, 1)), ("10月1日", (None, 10, 1)),
                               ("2026-10-01", (2026, 10, 1)), (12, (None, None, 12)), ("姓名", None), ("32", None), ("", None)):
            with self.subTest(cell=cell):
                self.assertEqual(mx.day_header(cell), expected)


if __name__ == "__main__":
    unittest.main()
