"""考勤异常发现（真实数据库 + 真实路由，业务系统的请假接口用桩）：导入对齐、规则判断、请假/日历、补录后消除、说明与认定的权限、汇总。"""
import io
import json
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import SessionLocal
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()

MON, TUE, WED = "2026-09-28", "2026-09-29", "2026-09-30"      # 周一到周三
SAT = "2026-09-26"
TABLES = ("attendance_anomaly", "attendance_punch", "attendance_import", "attendance_alias", "attendance_calendar", "attendance_rule")


def punch_csv(rows):
    """打卡机逐条导出：姓名,打卡时间。"""
    return ("姓名,打卡时间\n" + "".join(f"{name},{stamp}\n" for name, stamp in rows)).encode("utf-8")


@unittest.skipUnless(_AVAILABLE, _WHY)
class AttendanceServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.admin = rc.create_user("att-adm", admin=True)
        cls.hr = rc.create_user("att-hr")
        cls.head = rc.create_user("att-head")
        cls.emp1 = rc.create_user("att-e1")
        cls.emp2 = rc.create_user("att-e2")
        cls.other_head = rc.create_user("att-oh")
        cls.other_emp = rc.create_user("att-oe")
        cls.org = _create_org(cls.db, "att-org-" + uuid.uuid4().hex[:6], cls.admin["id"])
        cls.hr_team = _create_team(cls.db, cls.org, "att-hr", cls.admin["id"])
        cls.sales = _create_team(cls.db, cls.org, "att-sales", cls.admin["id"])
        cls.ops = _create_team(cls.db, cls.org, "att-ops", cls.admin["id"])
        cls.db.execute(text("UPDATE teams SET department_code='hr' WHERE id=:t"), {"t": cls.hr_team})
        cls.db.commit()
        for user in (cls.hr, cls.head, cls.emp1, cls.emp2, cls.other_head, cls.other_emp):
            _add_org_member(cls.db, cls.org, user["id"], "member")
        _add_team_member(cls.db, cls.hr_team, cls.hr["id"], "member")
        _add_team_member(cls.db, cls.sales, cls.head["id"], "admin")
        _add_team_member(cls.db, cls.sales, cls.emp1["id"], "member")
        _add_team_member(cls.db, cls.sales, cls.emp2["id"], "member")
        _add_team_member(cls.db, cls.ops, cls.other_head["id"], "admin")
        _add_team_member(cls.db, cls.ops, cls.other_emp["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.clean()
        cls.db.execute(text("DELETE FROM team_members WHERE team_id IN (:a,:b,:c)"), {"a": cls.hr_team, "b": cls.sales, "c": cls.ops})
        cls.db.execute(text("DELETE FROM teams WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    @classmethod
    def clean(cls):
        cls.db.commit()
        for table in TABLES:
            cls.db.execute(text(f"DELETE FROM {table} WHERE organization_id=:o"), {"o": cls.org})
        cls.db.commit()

    def setUp(self):
        self.clean()
        self.leaves = []
        patcher = patch("service.attendance_service.call_hub", new=AsyncMock(side_effect=lambda *a, **k: self.leaves))
        self.hub = patcher.start()
        self.addCleanup(patcher.stop)

    # ---- 辅助 ----

    def post(self, who, path, **body):
        return self.client.post(f"/enterprise/attendance{path}", headers=who["headers"], json=body)

    def get(self, who, path, team=None, **params):
        query = "&".join([f"team_id={team or self.hr_team}"] + [f"{k}={v}" for k, v in params.items()])
        return self.client.get(f"/enterprise/attendance{path}?{query}", headers=who["headers"])

    def upload(self, who, rows, aliases=None, name="打卡明细.csv", team=None):
        return self.client.post("/enterprise/attendance/import", headers=who["headers"], data={"team_id": team or self.hr_team, "aliases": json.dumps(aliases or {})},
                                files={"file": (name, io.BytesIO(punch_csv(rows)), "text/csv")})

    def day(self, who, day, first, last):
        return [(who["name"], f"{day} {first}"), (who["name"], f"{day} {last}")]

    def baseline_rows(self):
        """emp1：周一正常、周二迟到 40 分钟、周三整天没打卡；emp2：周一周二请假、周三正常；周六 emp2 来加班。
        head/hr 每天正常出勤，免得它们的“旷工”干扰断言。"""
        rows = []
        rows += self.day(self.emp1, MON, "08:55", "18:05") + self.day(self.emp1, TUE, "09:40", "18:00")
        rows += self.day(self.emp2, WED, "08:50", "18:00") + self.day(self.emp2, SAT, "10:00", "15:00")
        for who in (self.head, self.hr):
            for day in (MON, TUE, WED):
                rows += self.day(who, day, "08:50", "18:10")
        return rows

    def analyze(self, who=None, start=SAT, end=WED):
        return self.post(who or self.hr, "/analyze", team_id=self.hr_team, start=start, end=end)

    def anomalies_of(self, who, **params):
        response = self.get(who, "/anomalies", team=self.sales if who is not self.hr else self.hr_team, view="mine", **params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def prepare(self):
        self.leaves = [{"applicantUserId": self.emp2["id"], "leaveTypeCode": "annual", "startDate": MON, "endDate": TUE}]
        self.assertEqual(self.upload(self.hr, self.baseline_rows()).status_code, 200)
        result = self.analyze()
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    # ---- 导入 ----

    def test_import_matches_by_name_and_reports_the_rest(self):
        rows = self.baseline_rows() + [("小王", f"{MON} 09:00"), ("小王", f"{MON} 18:00"), ("陌生人", f"{MON} 09:00")]
        result = self.upload(self.hr, rows)
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body["format"], "punch_rows")
        self.assertEqual(sorted(u["name"] for u in body["unmatched"]), ["小王", "陌生人"])
        self.assertEqual(body["matched_people"], 4)
        self.assertEqual(body["no_records_total"], 2)                                 # other_head / other_emp 在这份文件里没有任何记录
        self.assertEqual(sorted(body["no_records"]), sorted([self.other_head["name"], self.other_emp["name"]]))
        self.assertEqual(body["new_punches"], body["punches"])

    def test_names_with_inserted_whitespace_still_match(self):
        """两个字的名字常被导出成“李 四”“李\u3000四”：对比时忽略空白，不该落到“对不上”里。"""
        spaced = self.emp1["name"][:4] + "\u3000" + self.emp1["name"][4:]
        result = self.upload(self.hr, [(spaced, f"{MON} 09:00"), (spaced, f"{MON} 18:00")]).json()
        self.assertEqual((result["unmatched"], result["matched_people"], result["new_punches"]), ([], 1, 2))

    def test_alias_is_saved_and_reused_next_time(self):
        rows = [("小王", f"{MON} 09:00"), ("小王", f"{MON} 18:00")]
        first = self.upload(self.hr, rows, aliases={"小王": self.emp1["id"]}).json()
        self.assertEqual((first["unmatched"], first["new_punches"]), ([], 2))
        again = self.upload(self.hr, rows).json()                       # 这次不再传对应，也认得
        self.assertEqual((again["unmatched"], again["new_punches"], again["duplicate_punches"]), ([], 0, 2))

    def test_alias_to_someone_outside_the_company_is_rejected(self):
        response = self.upload(self.hr, [("小王", f"{MON} 09:00")], aliases={"小王": 99999999})
        self.assertEqual(response.status_code, 400, response.text)

    def test_reimporting_the_same_file_adds_nothing(self):
        self.upload(self.hr, self.baseline_rows())
        again = self.upload(self.hr, self.baseline_rows()).json()
        self.assertEqual(again["new_punches"], 0)

    def test_bad_file_is_a_clear_400(self):
        response = self.client.post("/enterprise/attendance/import", headers=self.hr["headers"], data={"team_id": self.hr_team},
                                    files={"file": ("a.csv", io.BytesIO("月份,天数\n10,22\n".encode()), "text/csv")})
        self.assertEqual(response.status_code, 400)
        self.assertIn("表头", response.text)

    # ---- 权限 ----

    def test_only_hr_can_import_analyze_and_configure(self):
        for who in (self.emp1, self.head):
            team = self.sales
            self.assertEqual(self.upload(who, [("x", f"{MON} 09:00")], team=team).status_code, 403)
            self.assertEqual(self.client.post("/enterprise/attendance/analyze", headers=who["headers"], json={"team_id": team, "start": MON, "end": WED}).status_code, 403)
            self.assertEqual(self.client.post("/enterprise/attendance/rules", headers=who["headers"], json={
                "team_id": team, "work_start": "09:00", "work_end": "18:00", "grace_minutes": 5}).status_code, 403)
            self.assertEqual(self.client.post("/enterprise/attendance/calendar", headers=who["headers"], json={"team_id": team, "text": f"{MON},休息"}).status_code, 403)
            self.assertEqual(self.client.get(f"/enterprise/attendance/imports?team_id={team}", headers=who["headers"]).status_code, 403)

    def test_outsider_cannot_use_a_team_they_do_not_belong_to(self):
        self.assertEqual(self.get(self.other_emp, "/anomalies", team=self.sales, view="mine").status_code, 403)

    # ---- 分析 ----

    def test_analysis_finds_the_expected_anomalies_and_respects_approved_leave(self):
        result = self.prepare()
        self.assertEqual(result["created"], len([1 for _ in self.anomalies_all()]))
        by = {(a["user_id"], a["work_date"], a["type"]): a for a in self.anomalies_all()}
        self.assertIn((self.emp1["id"], TUE, "late"), by)
        self.assertEqual(by[(self.emp1["id"], TUE, "late")]["detail"]["minutes"], 40)
        self.assertEqual(by[(self.emp1["id"], TUE, "late")]["severity"], "medium")
        self.assertIn((self.emp1["id"], WED, "absent"), by)
        self.assertIn((self.emp2["id"], SAT, "rest_day_work"), by)
        self.assertEqual([k for k in by if k[0] == self.emp2["id"] and k[2] == "absent"], [])      # 请假那两天不算旷工
        self.assertEqual([k for k in by if k[0] in (self.head["id"], self.hr["id"])], [])
        self.assertEqual({a["status"] for a in by.values()}, {"open"})

    def anomalies_all(self):
        response = self.get(self.hr, "/anomalies", view="team")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_people_without_any_punch_are_listed_not_judged_absent(self):
        result = self.prepare()
        self.assertEqual(sorted(result["uncovered"]), sorted([self.other_head["name"], self.other_emp["name"]]))
        self.assertEqual(result["uncovered_total"], 2)
        self.assertEqual([a for a in self.anomalies_all() if a["user_id"] in (self.other_head["id"], self.other_emp["id"])], [])

    def test_leave_is_requested_from_the_business_system_for_the_whole_company(self):
        self.prepare()
        args = self.hub.call_args.args
        self.assertEqual(args[0], "GET")
        self.assertIn("/oa/leave/requests/approved", args[1])
        self.assertIn(f"scopeTeamIds={self.hr_team}", args[1])
        self.assertIn("from=" + SAT, args[1])
        self.assertEqual(args[4], ["oa.leave.read"])

    def test_business_system_failure_stops_the_analysis_instead_of_guessing(self):
        self.upload(self.hr, self.baseline_rows())
        self.hub.side_effect = RuntimeError("down")
        try:
            response = self.client.post("/enterprise/attendance/analyze", headers=self.hr["headers"], json={"team_id": self.hr_team, "start": MON, "end": WED})
            self.assertGreaterEqual(response.status_code, 500)
        except RuntimeError:
            pass                                                                      # 测试客户端把未处理异常直接抛出来
        self.assertEqual(self.anomalies_all(), [])

    def test_rerunning_is_idempotent_and_keeps_explanations(self):
        self.prepare()
        mine = self.anomalies_of(self.emp1)
        late = next(a for a in mine if a["type"] == "late")
        self.assertEqual(self.post(self.emp1, f"/anomalies/{late['id']}/explain", team_id=self.sales, text="早上堵车，到公司 9:40").status_code, 200)
        again = self.analyze().json()
        self.assertEqual((again["created"], again["cleared"]), (0, 0))
        after = next(a for a in self.anomalies_of(self.emp1) if a["id"] == late["id"])
        self.assertEqual((after["status"], after["explanation"]), ("explained", "早上堵车，到公司 9:40"))

    def test_late_punch_added_afterwards_clears_the_absence(self):
        self.prepare()
        absent = next(a for a in self.anomalies_of(self.emp1) if a["type"] == "absent")
        self.upload(self.hr, self.day(self.emp1, WED, "08:58", "18:02"))              # 补录周三的卡
        result = self.analyze().json()
        self.assertEqual(result["cleared"], 1)
        row = next(a for a in self.anomalies_of(self.emp1) if a["id"] == absent["id"])
        self.assertEqual(row["status"], "cleared")
        self.assertIn("补录", row["decision_note"])

    def test_decided_anomalies_are_not_overwritten_by_reanalysis(self):
        self.prepare()
        absent = next(a for a in self.anomalies_of(self.emp1) if a["type"] == "absent")
        self.assertEqual(self.post(self.head, f"/anomalies/{absent['id']}/decide", team_id=self.sales, action="confirm", note="核实确为旷工").status_code, 200)
        self.upload(self.hr, self.day(self.emp1, WED, "08:58", "18:02"))
        self.analyze()
        row = next(a for a in self.anomalies_of(self.emp1) if a["id"] == absent["id"])
        self.assertEqual(row["status"], "confirmed")

    def test_today_and_future_are_not_judged_and_range_is_limited(self):
        self.upload(self.hr, self.baseline_rows())
        self.assertEqual(self.analyze(start="2999-01-01", end="2999-01-05").status_code, 400)
        self.assertEqual(self.analyze(start="2026-01-01", end="2026-09-30").status_code, 400)

    def test_analysis_without_any_punch_asks_for_an_import_first(self):
        response = self.analyze()
        self.assertEqual(response.status_code, 400)
        self.assertIn("导入", response.text)

    # ---- 规则与日历 ----

    def test_team_rule_changes_the_verdict(self):
        self.upload(self.hr, self.day(self.emp1, MON, "09:20", "18:00") + self.day(self.emp2, MON, "09:20", "18:00"))
        self.leaves = []
        self.assertEqual(self.post(self.hr, "/rules", team_id=self.hr_team, for_team_id=self.sales, work_start="09:30", work_end="18:00", grace_minutes=0).status_code, 200)
        self.analyze(start=MON, end=MON)
        types = {a["type"] for a in self.anomalies_all() if a["user_id"] == self.emp1["id"]}
        self.assertNotIn("late", types)                                              # 销售部 9:30 上班，9:20 不算迟到

    def test_rules_list_every_department_in_the_company(self):
        rules = self.get(self.hr, "/rules").json()
        self.assertEqual({t["id"] for t in rules["org_teams"]}, {self.hr_team, self.sales, self.ops})
        self.assertEqual(rules["default"]["work_start"], "09:00")

    def test_rule_validation(self):
        bad = self.post(self.hr, "/rules", team_id=self.hr_team, work_start="18:00", work_end="09:00", grace_minutes=5)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.post(self.hr, "/rules", team_id=self.hr_team, for_team_id=99999999, work_start="09:00", work_end="18:00", grace_minutes=5).status_code, 404)

    def test_calendar_marks_a_weekday_as_holiday(self):
        self.upload(self.hr, self.day(self.emp1, TUE, "09:40", "18:00"))
        imported = self.post(self.hr, "/calendar", team_id=self.hr_team, text=f"日期,类型,备注\n{TUE},节假日,示例假期\n")
        self.assertEqual(imported.json(), {"imported": 1})
        self.analyze(start=TUE, end=TUE)
        types = [a["type"] for a in self.anomalies_all() if a["user_id"] == self.emp1["id"]]
        self.assertEqual(types, ["rest_day_work"])
        listed = self.get(self.hr, "/calendar", start=TUE, end=TUE).json()
        self.assertEqual([(r["day"], r["kind"], r["note"]) for r in listed], [(TUE, "holiday", "示例假期")])

    def test_calendar_makes_a_saturday_a_working_day(self):
        self.upload(self.hr, self.day(self.emp1, SAT, "09:00", "18:00"))
        self.post(self.hr, "/calendar", team_id=self.hr_team, text=f"{SAT},上班,调休补班")
        self.analyze(start=SAT, end=SAT)
        self.assertEqual([a for a in self.anomalies_all() if a["user_id"] == self.emp1["id"]], [])

    def test_calendar_input_errors_are_listed(self):
        response = self.post(self.hr, "/calendar", team_id=self.hr_team, text=f"{MON},放长假\n不是日期,休息")
        self.assertEqual(response.status_code, 400)
        self.assertIn("第 1 行", response.text)

    # ---- 说明与认定 ----

    def test_employee_sees_and_explains_only_their_own(self):
        self.prepare()
        mine = self.anomalies_of(self.emp1)
        self.assertEqual({a["user_id"] for a in mine}, {self.emp1["id"]})
        theirs = next(a for a in self.anomalies_of(self.emp2))
        stolen = self.post(self.emp1, f"/anomalies/{theirs['id']}/explain", team_id=self.sales, text="替他说明")
        self.assertEqual(stolen.status_code, 404)
        short = self.post(self.emp1, f"/anomalies/{mine[0]['id']}/explain", team_id=self.sales, text="嗯")
        self.assertEqual(short.status_code, 400)

    def test_team_view_is_limited_to_the_heads_own_teams(self):
        self.prepare()
        seen = self.get(self.head, "/anomalies", team=self.sales, view="team").json()
        self.assertTrue(seen)
        self.assertEqual({a["team_id"] for a in seen}, {self.sales})
        self.assertEqual(self.get(self.emp1, "/anomalies", team=self.sales, view="team").status_code, 403)
        self.assertEqual(self.get(self.other_head, "/anomalies", team=self.ops, view="team").json(), [])      # 别的部门负责人看不到销售部的

    def test_other_departments_head_cannot_decide(self):
        self.prepare()
        absent = next(a for a in self.anomalies_of(self.emp1) if a["type"] == "absent")
        response = self.post(self.other_head, f"/anomalies/{absent['id']}/decide", team_id=self.ops, action="dismiss", note="不关我事")
        self.assertEqual(response.status_code, 404)

    def test_head_and_hr_cannot_decide_their_own_anomalies(self):
        self.upload(self.hr, self.day(self.head, MON, "10:30", "18:00") + self.day(self.hr, MON, "10:30", "18:00"))
        self.leaves = []
        self.analyze(start=MON, end=MON)
        head_late = next(a for a in self.anomalies_of(self.head) if a["type"] == "late")
        self.assertEqual(self.post(self.head, f"/anomalies/{head_late['id']}/decide", team_id=self.sales, action="dismiss", note="自己批自己").status_code, 403)
        hr_late = next(a for a in self.anomalies_of(self.hr) if a["type"] == "late")
        self.assertEqual(self.post(self.hr, f"/anomalies/{hr_late['id']}/decide", team_id=self.hr_team, action="dismiss", note="自己批自己").status_code, 403)
        self.assertEqual(self.post(self.hr, f"/anomalies/{head_late['id']}/decide", team_id=self.hr_team, action="confirm", note="核实迟到").status_code, 200)   # 人事可以认定别人的

    def test_decision_closes_the_anomaly_and_blocks_further_changes(self):
        self.prepare()
        late = next(a for a in self.anomalies_of(self.emp1) if a["type"] == "late")
        self.post(self.emp1, f"/anomalies/{late['id']}/explain", team_id=self.sales, text="早上堵车")
        decided = self.post(self.head, f"/anomalies/{late['id']}/decide", team_id=self.sales, action="dismiss", note="情况属实，视为正常")
        self.assertEqual(decided.status_code, 200)
        self.assertEqual((decided.json()["status"], decided.json()["decided_by"]), ("dismissed", self.head["id"]))
        self.assertEqual(self.post(self.head, f"/anomalies/{late['id']}/decide", team_id=self.sales, action="confirm", note="改主意").status_code, 409)
        self.assertEqual(self.post(self.emp1, f"/anomalies/{late['id']}/explain", team_id=self.sales, text="再补充一句").status_code, 409)
        self.assertEqual(self.post(self.head, f"/anomalies/{late['id']}/decide", team_id=self.sales, action="confirm", note="x").status_code, 400)   # 理由太短

    def test_decision_needs_a_reason_and_valid_action(self):
        self.prepare()
        late = next(a for a in self.anomalies_of(self.emp1) if a["type"] == "late")
        self.assertEqual(self.post(self.head, f"/anomalies/{late['id']}/decide", team_id=self.sales, action="maybe", note="随便").status_code, 422)

    # ---- 汇总 ----

    def test_summary_counts_and_narrative_state_facts_only(self):
        self.prepare()
        hr_view = self.get(self.hr, "/summary", start=SAT, end=WED).json()
        self.assertEqual(hr_view["by_type"], {"late": 1, "absent": 1, "rest_day_work": 1})
        self.assertEqual(hr_view["people_affected"], 2)
        self.assertIn("共 3 条考勤异常", hr_view["narrative"])
        head_view = self.get(self.head, "/summary", team=self.sales, start=SAT, end=WED).json()
        self.assertEqual(head_view["by_type"], hr_view["by_type"])                    # 三条都是销售部的人
        self.assertEqual(self.get(self.other_head, "/summary", team=self.ops, start=SAT, end=WED).json()["by_type"], {})
        self.assertEqual(self.get(self.emp1, "/summary", team=self.sales, start=SAT, end=WED).status_code, 403)

    def test_summary_without_anomalies_says_so(self):
        empty = self.get(self.hr, "/summary", start=SAT, end=WED).json()
        self.assertIn("没有考勤异常", empty["narrative"])

    def test_import_history_lists_files(self):
        self.upload(self.hr, self.baseline_rows(), name="九月下旬.csv")
        listed = self.get(self.hr, "/imports").json()
        self.assertEqual([r["file_name"] for r in listed], ["九月下旬.csv"])
        self.assertEqual(listed[0]["period"], [SAT, WED])

    def test_me_reports_role_and_pending_counts(self):
        self.prepare()
        hr_me = self.get(self.hr, "/me").json()
        self.assertEqual((hr_me["is_hr"], hr_me["is_head"], hr_me["open_mine"], hr_me["to_decide"]), (True, False, 0, 0))
        emp = self.get(self.emp1, "/me", team=self.sales).json()
        self.assertEqual((emp["is_hr"], emp["is_head"], emp["open_mine"], emp["to_decide"]), (False, False, 2, 0))
        late = next(a for a in self.anomalies_of(self.emp1) if a["type"] == "late")
        self.post(self.emp1, f"/anomalies/{late['id']}/explain", team_id=self.sales, text="早上堵车")
        self.assertEqual(self.get(self.emp1, "/me", team=self.sales).json()["open_mine"], 1)
        head = self.get(self.head, "/me", team=self.sales).json()
        self.assertEqual((head["is_head"], head["to_decide"]), (True, 1))
        self.assertEqual(self.get(self.other_head, "/me", team=self.ops).json()["to_decide"], 0)

    def test_member_list_for_mapping(self):
        names = {m["name"] for m in self.get(self.hr, "/members").json()}
        self.assertIn(self.emp1["name"], names)
        self.assertNotIn(self.admin["name"], names)


if __name__ == "__main__":
    unittest.main()
