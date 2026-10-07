"""试点数据：手工计时样本取代拍脑袋基准、员工评价、试点报告（汇总、匿名、会说明样本不足）。"""
import json
import unittest
import uuid
from datetime import timedelta

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import AttendanceAnomaly, AutomationWork, SessionLocal
from service import pilot_service
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team
from utils.timeutil import utcnow

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, _WHY)
class PilotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.admin = rc.create_user("plt-adm", admin=True)
        cls.users = [rc.create_user(f"plt-u{i}") for i in range(4)]
        cls.out = rc.create_user("plt-out")
        cls.org = _create_org(cls.db, "plt-org-" + uuid.uuid4().hex[:6], cls.admin["id"])
        cls.team = _create_team(cls.db, cls.org, "plt-team", cls.admin["id"])
        cls.db.commit()
        for user in cls.users:
            _add_org_member(cls.db, cls.org, user["id"], "member")
            _add_team_member(cls.db, cls.team, user["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        for table in ("pilot_time_sample", "work_feedback", "automation_work", "attendance_anomaly"):
            cls.db.execute(text(f"DELETE FROM {table} WHERE team_id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    def setUp(self):
        self.db.commit()
        for table in ("pilot_time_sample", "work_feedback", "automation_work", "attendance_anomaly"):
            self.db.execute(text(f"DELETE FROM {table} WHERE team_id=:t"), {"t": self.team})
        self.db.commit()

    def h(self, i=0):
        return self.users[i]["headers"]

    def sample(self, i, minutes, kind="expense", status=200):
        response = self.client.post("/enterprise/automation/time-samples", headers=self.h(i),
                                    json={"team_id": self.team, "kind": kind, "minutes": minutes})
        self.assertEqual(response.status_code, status, response.text)

    def work(self, i, kind="expense", status="applied", seconds=60, edited=0):
        created = utcnow() - timedelta(minutes=5)
        work = AutomationWork(id=str(uuid.uuid4()), user_id=self.users[i]["id"], team_id=self.team, request_key=str(uuid.uuid4()), kind=kind,
                              model_name="m", sensitivity="internal", source_text="材料材料材料材料材料", status=status, created_at=created,
                              applied_at=created + timedelta(seconds=seconds) if status == "applied" else None, edited=edited, apply_attempts=1,
                              proposal_json=json.dumps({"a": 1}), accepted_json=json.dumps({"a": 1}), elapsed_ms=2000)
        work_id = work.id    # 提交后再读属性会重新开一个事务快照，后面的查询会看不到别的会话新提交的数据
        self.db.add(work)
        self.db.commit()
        return work_id

    def metrics(self):
        with rc.admin_env(self.admin["name"]):
            return self.client.get(f"/admin/automation-metrics?days=7&team_id={self.team}", headers=self.admin["headers"]).json()

    # ---- 计时样本 → 实测基准 ----

    def test_baseline_stays_assumed_until_enough_people_have_measured(self):
        for i, minutes in enumerate((20, 30, 25, 40)):        # 4 个样本、4 个人：样本数不够 5
            self.sample(i, minutes)
        row = next(r for r in self.metrics()["workflows"] if r["kind"] == "expense")
        self.assertEqual((row["baseline_source"], row["baseline_minutes"]), ("default", 15))
        self.sample(0, 35)                                    # 第 5 个样本
        row = next(r for r in self.metrics()["workflows"] if r["kind"] == "expense")
        self.assertEqual((row["baseline_source"], row["baseline_minutes"], row["baseline_samples"]), ("measured", 30, 5))   # 中位数 30

    def test_one_person_cannot_set_the_baseline_alone(self):
        for minutes in (10, 12, 14, 16, 18, 20):
            self.sample(0, minutes)
        row = next(r for r in self.metrics()["workflows"] if r["kind"] == "expense")
        self.assertEqual(row["baseline_source"], "default")   # 6 个样本但只有 1 个人

    def test_measured_baseline_drives_the_saved_minutes(self):
        for i, minutes in enumerate((30, 30, 30, 30)):
            self.sample(i, minutes)
        self.sample(0, 30)
        self.work(0, seconds=120)
        row = next(r for r in self.metrics()["workflows"] if r["kind"] == "expense")
        self.assertEqual(row["estimated_saved_minutes"], 28.0)    # 30 分钟手工 − 2 分钟 AI

    def test_sample_validation_and_membership(self):
        self.sample(0, 0.1, status=422)
        self.sample(0, 9999, status=422)
        self.sample(0, 10, kind="no-such-kind", status=400)
        denied = self.client.post("/enterprise/automation/time-samples", headers=self.out["headers"],
                                  json={"team_id": self.team, "kind": "expense", "minutes": 10})
        self.assertEqual(denied.status_code, 403)
        mine = self.client.get(f"/enterprise/automation/time-samples/mine?team_id={self.team}", headers=self.h(0)).json()
        self.assertEqual(mine, [])
        self.sample(0, 12)
        mine = self.client.get(f"/enterprise/automation/time-samples/mine?team_id={self.team}", headers=self.h(0)).json()
        self.assertEqual([m["minutes"] for m in mine], [12.0])
        self.assertEqual(self.client.get(f"/enterprise/automation/time-samples/mine?team_id={self.team}", headers=self.h(1)).json(), [])   # 只能看自己的

    def test_daily_limit(self):
        from unittest.mock import patch
        with patch.object(pilot_service, "MAX_SAMPLES_PER_USER_PER_DAY", 2):
            self.sample(0, 10)
            self.sample(0, 11)
            self.sample(0, 12, status=400)

    # ---- 评价 ----

    def test_feedback_only_for_my_saved_work_and_can_be_changed(self):
        saved, ready, others = self.work(0), self.work(0, status="ready"), self.work(1)
        put = lambda wid, who, **body: self.client.put(f"/enterprise/automation/{wid}/feedback", headers=self.h(who), json=body)   # noqa: E731
        self.assertEqual(put(saved, 0, rating=4, comment="省事").status_code, 200)
        self.assertEqual(self.client.get(f"/enterprise/automation/{saved}/feedback", headers=self.h(0)).json(), {"rating": 4, "comment": "省事"})
        self.assertEqual(put(saved, 0, rating=2, comment="日期老要改").json()["rating"], 2)            # 可以改，不会出现两条
        count = self.db.execute(text("SELECT COUNT(*) FROM work_feedback WHERE work_id=:w"), {"w": saved}).scalar()
        self.db.commit()
        self.assertEqual(count, 1)
        self.assertEqual(put(ready, 0, rating=5).status_code, 400)                                      # 还没保存，不能评价
        self.assertEqual(put(others, 0, rating=5).status_code, 404)                                     # 别人的成果
        self.assertEqual(put(saved, 0, rating=6).status_code, 422)
        self.assertEqual(put(saved, 0, rating=3, extra=1).status_code, 422)

    def test_feedback_can_carry_a_manual_time_sample_once(self):
        saved = self.work(0)
        for _ in range(2):
            self.client.put(f"/enterprise/automation/{saved}/feedback", headers=self.h(0), json={"rating": 5, "manual_minutes": 25})
        count = self.db.execute(text("SELECT COUNT(*) FROM pilot_time_sample WHERE user_id=:u"), {"u": self.users[0]["id"]}).scalar()
        self.db.commit()
        self.assertEqual(count, 1)

    # ---- 报告 ----

    def report(self, **query):
        with rc.admin_env(self.admin["name"]):
            return self.client.get(f"/admin/pilot-report?team_id={self.team}&days=7" + "".join(f"&{k}={v}" for k, v in query.items()), headers=self.admin["headers"])

    def test_report_is_admin_only_and_flags_a_small_sample(self):
        self.assertEqual(self.client.get("/admin/pilot-report", headers=self.h(0)).status_code, 403)
        for i in range(3):
            wid = self.work(i)
            self.client.put(f"/enterprise/automation/{wid}/feedback", headers=self.h(i), json={"rating": 4 + (i == 0), "comment": f"第{i}条"})
        data = self.report().json()
        self.assertEqual(data["team_name"], "plt-team")
        self.assertEqual(data["participation"]["users_applied"], 3)
        self.assertEqual((data["participation"]["members"], data["participation"]["adoption_rate"]), (4, 0.75))
        self.assertEqual((data["feedback"]["count"], data["feedback"]["average"]), (3, 4.33))
        self.assertTrue(any("样本量小于" in c for c in data["caveats"]))
        self.assertTrue(any("不是实测基准" in c for c in data["caveats"]))
        self.assertTrue(any("评价只有 3 条" in c for c in data["caveats"]))
        self.assertEqual(data["baselines"]["expense"]["source"], "default")

    def test_report_has_no_per_person_data(self):
        wid = self.work(0)
        self.client.put(f"/enterprise/automation/{wid}/feedback", headers=self.h(0), json={"rating": 3, "comment": "一般"})
        raw = self.report().text
        for user in self.users:
            self.assertNotIn(user["name"], raw)
        md = self.report(format="md").text
        self.assertIn("# 试点效果报告：plt-team", md)
        self.assertIn("一般", md)
        for user in self.users:
            self.assertNotIn(user["name"], md)

    def test_measured_baselines_are_labelled_in_the_report(self):
        for i, minutes in enumerate((30, 30, 30, 30)):
            self.sample(i, minutes)
        self.sample(1, 30)
        self.work(0, seconds=90)
        data = self.report().json()
        self.assertEqual(data["baselines"]["expense"]["source"], "measured")
        self.assertFalse(any("不是实测基准" in c for c in data["caveats"] if "费用材料整理" in c))
        self.assertIn("员工实测中位数", self.report(format="md").text)

    def test_report_for_an_empty_period_does_not_crash(self):
        data = self.report().json()
        self.assertEqual((data["ai_work"]["overall"]["total"], data["feedback"]["count"]), (0, 0))
        self.assertIn("试点效果报告", self.report(format="md").text)
        with rc.admin_env(self.admin["name"]):
            self.assertEqual(self.client.get("/admin/pilot-report?team_id=99999999", headers=self.admin["headers"]).status_code, 404)
            self.assertEqual(self.client.get("/admin/pilot-report?days=0", headers=self.admin["headers"]).status_code, 422)

    # ---- 考勤异常处理流程 ----

    def anomaly(self, kind, status, explained_after_h=None, decided_after_h=None, day=1):
        created = utcnow() - timedelta(days=2)
        row = AttendanceAnomaly(organization_id=self.org, team_id=self.team, user_id=self.users[0]["id"], work_date=created.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=day),
                                type=kind, severity="medium", detail_json="{}", status=status, created_at=created)
        if explained_after_h is not None:
            row.explanation, row.explained_at = "堵车", created + timedelta(hours=explained_after_h)
        if decided_after_h is not None:
            row.decided_by, row.decision_note, row.decided_at = self.users[1]["id"], "核实", row.explained_at + timedelta(hours=decided_after_h)
        self.db.add(row)
        self.db.commit()

    def test_report_summarises_the_attendance_process_without_names(self):
        self.anomaly("late", "open", day=1)
        self.anomaly("absent", "explained", explained_after_h=2, day=2)
        self.anomaly("early_leave", "confirmed", explained_after_h=4, decided_after_h=6, day=3)
        self.anomaly("missing_out", "cleared", day=4)
        with rc.admin_env(self.admin["name"]):
            data = self.client.get(f"/admin/pilot-report?days=7&team_id={self.team}", headers=self.admin["headers"]).json()
        a = data["attendance"]
        self.assertEqual((a["total"], a["cleared_by_system"], a["needs_handling"]), (4, 1, 3))
        self.assertEqual((a["explained"], a["decided"], a["confirmed"], a["dismissed"]), (2, 1, 1, 0))
        self.assertEqual((a["explained_rate"], a["decided_rate"]), (0.667, 0.333))
        self.assertEqual((a["median_explain_hours"], a["median_decide_hours"]), (3.0, 6.0))
        self.assertTrue(any("考勤异常只有 3 条" in c for c in data["caveats"]))
        with rc.admin_env(self.admin["name"]):
            md = self.client.get(f"/admin/pilot-report?days=7&team_id={self.team}&format=md", headers=self.admin["headers"]).text
        self.assertIn("考勤异常处理流程", md)
        for user in self.users:
            self.assertNotIn(user["name"], md)

    def test_report_without_attendance_data_has_no_attendance_section(self):
        with rc.admin_env(self.admin["name"]):
            md = self.client.get(f"/admin/pilot-report?days=7&team_id={self.team}&format=md", headers=self.admin["headers"]).text
        self.assertNotIn("考勤异常处理流程", md)


if __name__ == "__main__":
    unittest.main()
