"""考勤异常提醒：员工待说明、负责人/人事待认定；每人一条汇总，处理完自动关闭，太久以前的历史异常不催。"""
import json
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from models.init_db import AttendanceAnomaly, SessionLocal
from service import work_item_service
from service.reminders import attendance_reminders as ar
from service.reminders import rules
from tests import _route_client as rc
from tests._async_helpers import run_async
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team
from utils.timeutil import utcnow

_AVAILABLE, _WHY = rc.route_tests_available()


def run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as db:
            return await fn(db)
    return run_async(go())


class RegistrationTest(unittest.TestCase):
    def test_rules_are_registered_with_a_notification_category(self):
        from service.notification_center import CATEGORIES
        for name in ("attendance_explain", "attendance_decide"):
            self.assertEqual(rules.RULES_BY_NAME[name].category, "attendance")
        self.assertIn("attendance", CATEGORIES)


@unittest.skipUnless(_AVAILABLE, _WHY)
class AttendanceRemindersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.head = rc.create_user("arm-head")
        cls.emp = rc.create_user("arm-emp")
        cls.hr = rc.create_user("arm-hr")
        cls.org = _create_org(cls.db, "arm-org-" + uuid.uuid4().hex[:6], cls.head["id"])
        cls.sales = _create_team(cls.db, cls.org, "arm-sales", cls.head["id"])
        cls.hr_team = _create_team(cls.db, cls.org, "arm-hr", cls.head["id"])
        cls.db.execute(text("UPDATE teams SET department_code='hr' WHERE id=:t"), {"t": cls.hr_team})
        cls.db.commit()
        for user in (cls.head, cls.emp, cls.hr):
            _add_org_member(cls.db, cls.org, user["id"], "member")
        _add_team_member(cls.db, cls.sales, cls.head["id"], "admin")
        _add_team_member(cls.db, cls.sales, cls.emp["id"], "member")
        _add_team_member(cls.db, cls.hr_team, cls.hr["id"], "member")
        cls.teams = [t for t in run_db(lambda db: rules.active_teams(db)) if t.id in (cls.sales, cls.hr_team)]

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.clean()
        cls.db.execute(text("DELETE FROM team_members WHERE team_id IN (:a,:b)"), {"a": cls.sales, "b": cls.hr_team})
        cls.db.execute(text("DELETE FROM teams WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    @classmethod
    def clean(cls):
        cls.db.commit()
        cls.db.execute(text("DELETE FROM attendance_anomaly WHERE organization_id=:o"), {"o": cls.org})
        for table in ("work_item", "notification"):
            cls.db.execute(text(f"DELETE FROM {table} WHERE user_id IN (:a, :b, :c)"), {"a": cls.head["id"], "b": cls.emp["id"], "c": cls.hr["id"]})
        cls.db.commit()

    def setUp(self):
        self.clean()
        patcher = patch.object(ar, "active_teams", AsyncMock(return_value=self.teams))
        patcher.start()
        self.addCleanup(patcher.stop)

    def anomaly(self, user, status="open", days_ago=1, created_days_ago=0, team=None, kind="late"):
        row = AttendanceAnomaly(organization_id=self.org, team_id=team or self.sales, user_id=user["id"], type=kind, severity="medium", status=status,
                                work_date=datetime.combine((utcnow() - timedelta(days=days_ago)).date(), datetime.min.time()),
                                detail_json=json.dumps({"minutes": 30}), created_at=utcnow() - timedelta(days=created_days_ago))
        if status == "explained":
            row.explanation, row.explained_at = "堵车", utcnow() - timedelta(days=created_days_ago)
        self.db.add(row)
        self.db.commit()

    def items(self, user):
        return run_db(lambda db: work_item_service.list_items(db, user["id"], "all"))["items"]

    # ---- 待说明 ----

    def test_employee_gets_one_summary_for_all_open_anomalies(self):
        self.anomaly(self.emp, days_ago=3)
        self.anomaly(self.emp, days_ago=2, kind="absent")
        run_db(ar.run_attendance_explain)
        items = self.items(self.emp)
        self.assertEqual([i["title"] for i in items], ["你有 2 条考勤异常待说明"])
        self.assertEqual(items[0]["priority"], "normal")
        self.assertEqual(self.items(self.head), [])

    def test_waiting_over_two_days_raises_priority(self):
        self.anomaly(self.emp, days_ago=5, created_days_ago=3)
        run_db(ar.run_attendance_explain)
        self.assertEqual(self.items(self.emp)[0]["priority"], "high")

    def test_explaining_closes_the_reminder(self):
        self.anomaly(self.emp)
        run_db(ar.run_attendance_explain)
        self.db.execute(text("UPDATE attendance_anomaly SET status='explained' WHERE user_id=:u"), {"u": self.emp["id"]})
        self.db.commit()
        created, resolved = run_db(ar.run_attendance_explain)
        self.assertEqual((created, resolved), (0, 1))
        self.assertEqual([i for i in self.items(self.emp) if i["status"] == "open"], [])

    def test_old_history_is_not_chased(self):
        self.anomaly(self.emp, days_ago=200, created_days_ago=200)
        self.assertEqual(run_db(ar.run_attendance_explain), (0, 0))

    # ---- 待认定 ----

    def test_head_and_hr_are_asked_to_decide_but_not_the_employee(self):
        self.anomaly(self.emp, status="explained")
        run_db(ar.run_attendance_decide)
        self.assertEqual([i["title"] for i in self.items(self.head)], ["有 1 条考勤异常等你认定"])
        self.assertEqual([i["title"] for i in self.items(self.hr)], ["有 1 条考勤异常等你认定"])
        self.assertEqual(self.items(self.emp), [])

    def test_own_anomaly_does_not_count_for_the_head(self):
        self.anomaly(self.head, status="explained")
        run_db(ar.run_attendance_decide)
        self.assertEqual(self.items(self.head), [])                                 # 负责人自己的由人事认定
        self.assertEqual(len(self.items(self.hr)), 1)

    def test_deciding_closes_the_reminder_for_everyone(self):
        self.anomaly(self.emp, status="explained")
        run_db(ar.run_attendance_decide)
        self.db.execute(text("UPDATE attendance_anomaly SET status='confirmed' WHERE user_id=:u"), {"u": self.emp["id"]})
        self.db.commit()
        self.assertEqual(run_db(ar.run_attendance_decide), (0, 2))

    def test_nothing_to_decide_is_a_noop(self):
        self.assertEqual(run_db(ar.run_attendance_decide), (0, 0))


if __name__ == "__main__":
    unittest.main()
