"""试点预检：真实模型、死信、成员模型连接、部门与负责人、考勤日历；error 阻止开始，warn 只提醒（真实 MySQL）。"""
import unittest
import uuid
from datetime import datetime
from unittest.mock import patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import AttendanceCalendar, DeadLetter, LLMConfig, SessionLocal
from service import pilot_preflight as pf
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team
from utils.timeutil import utcnow

_AVAILABLE, _WHY = rc.route_tests_available()


def by_key(checks):
    return {c["key"]: c for c in checks}


class EnvironmentTest(unittest.TestCase):
    def test_offline_demo_model_blocks_a_real_pilot(self):
        with patch("service.llm.offline_demo.enabled", return_value=True):
            checks = by_key(pf.check_environment())
        self.assertEqual(checks["real_model"]["level"], "error")
        self.assertFalse(checks["real_model"]["ok"])

    def test_disabled_outbox_runner_blocks_and_disabled_reminders_warn(self):
        with patch.dict("os.environ", {"OUTBOX_RUNNER": "0", "REMINDERS_ENABLED": "0"}):
            checks = by_key(pf.check_environment())
        self.assertEqual((checks["outbox_runner"]["level"], checks["reminders"]["level"]), ("error", "warn"))

    def test_non_production_is_only_a_warning(self):
        with patch.dict("os.environ", {"APP_ENV": "test"}):
            self.assertEqual(by_key(pf.check_environment())["app_env"]["level"], "warn")
        with patch.dict("os.environ", {"APP_ENV": "production"}):
            self.assertEqual(by_key(pf.check_environment())["app_env"]["level"], "ok")


class MarkdownTest(unittest.TestCase):
    def test_verdict_follows_errors(self):
        ok = {"ok": True, "errors": 0, "warnings": 1, "checks": [{"level": "warn", "label": "备份", "message": "旧", "fix": "再备份"}]}
        bad = {"ok": False, "errors": 1, "warnings": 0, "checks": [{"level": "error", "label": "模型", "message": "离线", "fix": ""}]}
        self.assertIn("可以开始试点", pf.to_markdown(ok))
        self.assertIn("不能开始：1 项必须先处理", pf.to_markdown(bad))


@unittest.skipUnless(_AVAILABLE, _WHY)
class RosterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.users = [rc.create_user(f"pfl-{i}") for i in range(6)]
        cls.org = _create_org(cls.db, "pfl-org-" + uuid.uuid4().hex[:6], cls.users[0]["id"])
        cls.hr = _create_team(cls.db, cls.org, "pfl-hr", cls.users[0]["id"])
        cls.sales = _create_team(cls.db, cls.org, "pfl-sales", cls.users[0]["id"])
        cls.db.execute(text("UPDATE teams SET department_code='hr' WHERE id=:t"), {"t": cls.hr})
        cls.db.commit()
        for user in cls.users:
            _add_org_member(cls.db, cls.org, user["id"], "member")
        _add_team_member(cls.db, cls.hr, cls.users[0]["id"], "admin")
        _add_team_member(cls.db, cls.hr, cls.users[1]["id"], "member")
        for user in cls.users[2:]:
            _add_team_member(cls.db, cls.sales, user["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.commit()
        cls.db.execute(text("DELETE FROM attendance_calendar WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM team_members WHERE team_id IN (:a,:b)"), {"a": cls.hr, "b": cls.sales})
        cls.db.execute(text("DELETE FROM teams WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    def setUp(self):
        self.db.commit()
        self.db.execute(text("DELETE FROM attendance_calendar WHERE organization_id=:o"), {"o": self.org})
        self.db.execute(text("DELETE FROM llm_config WHERE user_id IN (" + ",".join(str(u["id"]) for u in self.users) + ")"))
        self.db.commit()

    def roster(self):
        self.db.commit()
        return by_key(pf.check_roster(self.db, self.org))

    def test_unknown_org_is_an_error(self):
        self.assertEqual(pf.check_roster(self.db, 99999999)[0]["level"], "error")

    def test_enough_people_and_departments_but_models_missing(self):
        checks = self.roster()
        self.assertEqual((checks["members"]["level"], checks["departments"]["level"]), ("ok", "ok"))
        self.assertEqual(checks["member_models"]["level"], "warn")
        self.assertIn("6 位成员还没连接模型", checks["member_models"]["message"])
        self.db.add_all([LLMConfig(user_id=u["id"], model_name="glm-4", api_key="x", is_active=1) for u in self.users])
        self.db.commit()
        self.assertEqual(self.roster()["member_models"]["level"], "ok")

    def test_department_without_business_type_and_without_head_are_flagged(self):
        checks = self.roster()
        self.assertIn("pfl-sales", checks["department_codes"]["message"])          # 销售部没设业务类型
        self.assertEqual(checks["heads"]["level"], "warn")                          # 只有人事部有负责人
        self.assertIn("1/2", checks["heads"]["message"])

    def test_attendance_calendar_is_flagged_until_entered(self):
        self.assertEqual(self.roster()["attendance_calendar"]["level"], "warn")
        self.db.add(AttendanceCalendar(organization_id=self.org, day=datetime(utcnow().year, 10, 1), kind="holiday", note="国庆"))
        self.db.commit()
        self.assertEqual(self.roster()["attendance_calendar"]["level"], "ok")

    def test_attendance_needs_an_hr_member(self):
        self.assertEqual(self.roster()["attendance_hr"]["level"], "ok")
        self.db.execute(text("UPDATE teams SET department_code=NULL WHERE id=:t"), {"t": self.hr})
        self.db.commit()
        try:
            self.assertEqual(self.roster()["attendance_hr"]["level"], "warn")
        finally:
            self.db.execute(text("UPDATE teams SET department_code='hr' WHERE id=:t"), {"t": self.hr})
            self.db.commit()

    def test_pending_dead_letter_blocks(self):
        row = DeadLetter(event_id=str(uuid.uuid4()), consumer="pf-test", topic="t", event_type="x", payload_json="{}", status="pending")
        self.db.add(row)
        self.db.commit()
        letter_id = row.id
        try:
            checks = by_key(pf.check_operations(self.db))
            self.assertEqual(checks["dead_letters"]["level"], "error")
        finally:
            self.db.execute(text("DELETE FROM dead_letter WHERE id=:i"), {"i": letter_id})
            self.db.commit()

    def test_run_without_org_skips_roster_with_a_warning(self):
        result = pf.run(self.db, None)
        self.assertEqual(by_key(result["checks"])["roster"]["level"], "warn")


if __name__ == "__main__":
    unittest.main()
