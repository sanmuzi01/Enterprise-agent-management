"""责任协同的提醒：待接受/异议、到期与逾期、长期受阻、待验收；条件消失自动关闭（Java 读取用替身，数据库为真实测试库）。"""
import unittest
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from models.init_db import SessionLocal
from service import work_item_service
from service.exceptions import PermissionDenied
from service.reminders import responsibility_rules as rr
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


def iso(dt):
    return dt.isoformat() + "Z"


class RegistrationTest(unittest.TestCase):
    def test_rules_are_registered_with_a_notification_category(self):
        from service.notification_center import CATEGORIES
        for name in ("resp_accept", "resp_due", "resp_review"):
            rule = rules.RULES_BY_NAME[name]
            self.assertEqual(rule.category, "responsibility")
        self.assertIn("responsibility", CATEGORIES)


@unittest.skipUnless(_AVAILABLE, _WHY)
class ResponsibilityRemindersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.head = rc.create_user("rrm-head")
        cls.emp = rc.create_user("rrm-emp")
        cls.reviewer = rc.create_user("rrm-rev")
        cls.org = _create_org(cls.db, "rrm-org-" + uuid.uuid4().hex[:6], cls.head["id"])
        cls.team = _create_team(cls.db, cls.org, "rrm-team", cls.head["id"])
        cls.db.commit()
        _add_org_member(cls.db, cls.org, cls.head["id"], "admin")
        _add_team_member(cls.db, cls.team, cls.head["id"], "admin")
        for user in (cls.emp, cls.reviewer):
            _add_team_member(cls.db, cls.team, user["id"], "member")
        cls.teams = [t for t in run_db(lambda db: rules.active_teams(db)) if t.id == cls.team]

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    def setUp(self):
        self.db.commit()
        for table in ("work_item", "notification"):
            self.db.execute(text(f"DELETE FROM {table} WHERE user_id IN (:a, :b, :c)"),
                            {"a": self.head["id"], "b": self.emp["id"], "c": self.reviewer["id"]})
        self.db.commit()
        self.tasks = []
        scoped = patch.object(rr, "active_teams", AsyncMock(return_value=self.teams))
        feed = patch("service.responsibility_service.team_tasks_for_reminders", AsyncMock(side_effect=lambda *a, **k: list(self.tasks)))
        for p in (scoped, feed):
            p.start()
            self.addCleanup(p.stop)

    def task(self, **over):
        base = {"id": 1, "title": "联调首页", "status": "PENDING_ACCEPT", "responsibleUserId": self.emp["id"],
                "reviewerUserId": self.reviewer["id"], "assignedByUserId": self.head["id"], "dueDate": None,
                "assignedAt": iso(utcnow() - timedelta(hours=2)), "teamId": self.team}
        base.update(over)
        return base

    def items(self, user):
        return run_db(lambda db: work_item_service.list_items(db, user["id"], "all"))["items"]

    def titles(self, user):
        return sorted(i["title"] for i in self.items(user))

    def today(self, days=0):
        return ((utcnow() + timedelta(hours=8)).date() + timedelta(days=days)).isoformat()

    # ---- 待接受 ----

    def test_pending_acceptance_reminds_the_employee_and_only_later_the_assigner(self):
        self.tasks = [self.task()]
        run_db(rr.run_resp_accept)
        self.assertEqual(len(self.titles(self.emp)), 1)
        self.assertIn("「联调首页」等你接受", self.titles(self.emp)[0])
        self.assertEqual(self.items(self.head), [])                      # 刚指派两小时，还没到催办时限
        self.tasks = [self.task(assignedAt=iso(utcnow() - timedelta(hours=30)))]
        run_db(rr.run_resp_accept)
        self.assertEqual(self.items(self.emp)[0]["priority"], "high")
        self.assertIn("还没有接受「联调首页」", self.titles(self.head)[0])
        self.assertIn("已等待 30 小时", self.items(self.head)[0]["detail"])

    def test_accepting_closes_the_reminders(self):
        self.tasks = [self.task(assignedAt=iso(utcnow() - timedelta(hours=30)))]
        run_db(rr.run_resp_accept)
        self.tasks = [self.task(status="IN_PROGRESS")]
        created, resolved = run_db(rr.run_resp_accept)
        self.assertEqual((created, resolved), (0, 2))
        self.assertEqual([i for i in self.items(self.emp) if i["status"] == "open"], [])

    def test_objection_goes_to_the_assigner_not_the_employee(self):
        self.tasks = [self.task(status="NEGOTIATING")]
        run_db(rr.run_resp_accept)
        self.assertEqual(self.items(self.emp), [])
        self.assertIn("提出了异议", self.titles(self.head)[0])
        self.assertEqual(self.items(self.head)[0]["priority"], "high")

    # ---- 到期 / 逾期 / 受阻 ----

    def test_due_soon_and_overdue(self):
        self.tasks = [self.task(id=1, status="IN_PROGRESS", dueDate=self.today(1)),
                      self.task(id=2, title="更晚的事", status="IN_PROGRESS", dueDate=self.today(10)),
                      self.task(id=3, title="已逾期的事", status="IN_PROGRESS", dueDate=self.today(-2))]
        run_db(rr.run_resp_due)
        mine = {i["title"]: i for i in self.items(self.emp)}
        self.assertEqual(len(mine), 2)                                    # 10 天后到期的不提醒
        self.assertEqual(mine[f"{self.teams[0].name}：「联调首页」即将到期"]["priority"], "normal")
        overdue = next(i for t, i in mine.items() if "已逾期的事" in t)
        self.assertEqual(overdue["priority"], "high")
        self.assertIn("已逾期 2 天", overdue["detail"])
        boss = self.titles(self.head)
        self.assertEqual(len(boss), 1)
        self.assertIn("已逾期的事」已逾期 2 天", boss[0])

    def test_long_blocked_reminds_the_assigner_with_the_employees_reason(self):
        since = iso(utcnow() - timedelta(hours=60))
        self.tasks = [self.task(status="BLOCKED", blockedSince=since, blockedReason="缺少测试环境账号", dueDate=self.today(10)),
                      self.task(id=2, title="刚受阻", status="BLOCKED", blockedSince=iso(utcnow() - timedelta(hours=3)), dueDate=self.today(10))]
        run_db(rr.run_resp_due)
        titles = self.titles(self.head)
        self.assertEqual(len(titles), 1)
        self.assertIn("受阻已 60 小时", titles[0])
        self.assertIn("缺少测试环境账号", self.items(self.head)[0]["detail"])

    def test_done_or_blocked_cleared_closes(self):
        self.tasks = [self.task(status="IN_PROGRESS", dueDate=self.today(-1))]
        run_db(rr.run_resp_due)
        self.tasks = []
        self.assertEqual(run_db(rr.run_resp_due), (0, 2))

    # ---- 待验收 ----

    def test_review_reminder_goes_to_the_reviewer_and_escalates(self):
        self.tasks = [self.task(status="PENDING_REVIEW", submittedAt=iso(utcnow() - timedelta(hours=2)))]
        run_db(rr.run_resp_review)
        self.assertIn("等你验收", self.titles(self.reviewer)[0])
        self.assertEqual(self.items(self.reviewer)[0]["priority"], "normal")
        self.assertEqual(self.items(self.emp), [])
        self.tasks = [self.task(status="PENDING_REVIEW", submittedAt=iso(utcnow() - timedelta(hours=40)))]
        run_db(rr.run_resp_review)
        self.assertEqual(self.items(self.reviewer)[0]["priority"], "high")
        self.tasks = [self.task(status="DONE")]
        self.assertEqual(run_db(rr.run_resp_review)[1], 1)

    # ---- 身份失效 ----

    def test_invalid_head_identity_is_skipped_without_failing(self):
        with patch("service.responsibility_service.team_tasks_for_reminders", AsyncMock(side_effect=PermissionDenied("身份失效"))):
            self.assertEqual(run_db(rr.run_resp_accept), (0, 0))
        self.assertEqual(self.items(self.emp), [])


if __name__ == "__main__":
    unittest.main()
