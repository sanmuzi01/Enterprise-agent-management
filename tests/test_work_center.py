"""统一待办、站内通知、提醒规则引擎与五条提醒规则（Java 读取用替身，数据库为真实测试库）。"""
import json
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, text, update

from models.init_db import Notification, ReminderRun, SessionLocal, WorkItem
from service import notification_center, reminders, work_item_service
from service.exceptions import NotFound
from service.reminders import rules
from service.reminders.base import Reminder, ReminderRule, sync_reminders
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


class QuietHoursTest(unittest.TestCase):
    def test_same_day_and_cross_midnight_windows(self):
        at = lambda hhmm: datetime(2026, 10, 2, int(hhmm[:2]), int(hhmm[3:])) - timedelta(hours=8)  # noqa: E731
        self.assertTrue(notification_center.in_quiet_hours("12:00", "14:00", at("13:00")))
        self.assertFalse(notification_center.in_quiet_hours("12:00", "14:00", at("14:00")))
        self.assertTrue(notification_center.in_quiet_hours("22:00", "08:00", at("23:30")))
        self.assertTrue(notification_center.in_quiet_hours("22:00", "08:00", at("07:59")))
        self.assertFalse(notification_center.in_quiet_hours("22:00", "08:00", at("12:00")))
        self.assertFalse(notification_center.in_quiet_hours(None, None, at("23:00")))

    def test_date_only_due_is_beijing_evening(self):
        self.assertEqual(work_item_service.parse_due("2026-10-08"), datetime(2026, 10, 8, 10, 0))
        self.assertEqual(work_item_service.parse_due("2026-10-08T09:00:00+08:00"), datetime(2026, 10, 8, 1, 0))


@unittest.skipUnless(_AVAILABLE, _WHY)
class WorkCenterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.admin = rc.create_user("wc-adm")
        cls.member = rc.create_user("wc-mem")
        cls.other = rc.create_user("wc-oth")
        cls.org = _create_org(cls.db, "wc-org-" + uuid.uuid4().hex[:6], cls.admin["id"])
        cls.team = _create_team(cls.db, cls.org, "wc-team", cls.admin["id"])
        cls.sales = _create_team(cls.db, cls.org, "wc-sales", cls.admin["id"])
        cls.db.execute(text("UPDATE teams SET department_code='sales' WHERE id=:t"), {"t": cls.sales})
        cls.db.commit()
        _add_org_member(cls.db, cls.org, cls.admin["id"], "admin")
        for team in (cls.team, cls.sales):
            _add_team_member(cls.db, team, cls.admin["id"], "admin")
            _add_team_member(cls.db, team, cls.member["id"], "member")
        cls.teams = run_db(lambda db: rules.active_teams(db))
        cls.teams = [t for t in cls.teams if t.id in (cls.team, cls.sales)]

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.execute(text("DELETE FROM teams WHERE id IN (:a, :b)"), {"a": cls.team, "b": cls.sales})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    def setUp(self):
        self.db.commit()
        for table in ("work_item", "notification", "notification_preference"):
            self.db.execute(text(f"DELETE FROM {table} WHERE user_id IN (:a, :b, :c)"),
                            {"a": self.admin["id"], "b": self.member["id"], "c": self.other["id"]})
        self.db.commit()

    def items(self, user):
        return run_db(lambda db: work_item_service.list_items(db, user["id"], "all"))["items"]

    def notifications(self, user):
        return run_db(lambda db: notification_center.list_notifications(db, user["id"]))["items"]

    def scoped(self, code=None):
        teams = [t for t in self.teams if code is None or t.department_code == code]
        return patch.object(rules, "active_teams", AsyncMock(return_value=teams))

    # ---- 待办 ----

    def test_manual_items_counts_and_ownership(self):
        now = utcnow()
        run_db(lambda db: work_item_service.create_manual(db, self.member["id"], "写周报", None, "low"))
        overdue = run_db(lambda db: work_item_service.create_manual(
            db, self.member["id"], "交报销", iso(now - timedelta(hours=1)), "high"))
        counts = run_db(lambda db: work_item_service.counts(db, self.member["id"]))
        self.assertEqual((counts["open"], counts["overdue"], counts["high"]), (2, 1, 1))
        self.assertEqual(self.items(self.member)[0]["title"], "交报销")  # 有截止时间的排在前面
        with self.assertRaises(NotFound):
            run_db(lambda db: work_item_service.set_status(db, self.other["id"], overdue["id"], "done"))
        done = run_db(lambda db: work_item_service.set_status(db, self.member["id"], overdue["id"], "done"))
        self.assertEqual((done["status"], done["resolved_by"]), ("done", "user"))

    # ---- 通知 ----

    def test_notify_dedupes_respects_mute_and_marks_read(self):
        uid = self.member["id"]
        self.assertTrue(run_db(lambda db: notification_center.notify(db, uid, "approval", "A", dedupe_key="k1")))
        self.assertFalse(run_db(lambda db: notification_center.notify(db, uid, "approval", "A", dedupe_key="k1")))
        run_db(lambda db: notification_center.update_preference(db, uid, ["approval"], None, None, False))
        self.assertFalse(run_db(lambda db: notification_center.notify(db, uid, "approval", "B", dedupe_key="k2")))
        self.assertTrue(run_db(lambda db: notification_center.notify(db, uid, "system", "S", dedupe_key="k3")))
        self.assertEqual(run_db(lambda db: notification_center.mark_read(db, uid))["unread"], 0)

    def test_external_push_only_when_enabled_and_outside_quiet_hours(self):
        uid = self.member["id"]
        with patch("service.notification_service.dispatch_alert_async", AsyncMock()) as push:
            run_db(lambda db: notification_center.notify(db, uid, "digest", "A", dedupe_key="p1"))
            push.assert_not_called()
            run_db(lambda db: notification_center.update_preference(db, uid, [], "00:00", "23:59", True))
            run_db(lambda db: notification_center.notify(db, uid, "digest", "B", dedupe_key="p2"))
            push.assert_not_called()
            run_db(lambda db: notification_center.update_preference(db, uid, [], None, None, True))
            run_db(lambda db: notification_center.notify(db, uid, "digest", "C", dedupe_key="p3"))
            push.assert_called_once()

    # ---- 提醒同步 ----

    def test_sync_creates_refreshes_resolves_and_respects_dismissal(self):
        uid = self.member["id"]
        sync = lambda rs: run_db(lambda db: sync_reminders(db, "t_rule", "approval", rs))  # noqa: E731
        first = [Reminder(uid, "x:1", "单据 1 等待"), Reminder(uid, "x:2", "单据 2 等待")]
        self.assertEqual(sync(first), (2, 0))
        self.assertEqual(sync([Reminder(uid, "x:1", "单据 1 等待（更新）"), first[1]]), (0, 0))
        self.assertEqual(len(self.notifications(self.member)), 2)
        dismissed = next(i for i in self.items(self.member) if i["title"].startswith("单据 2"))
        run_db(lambda db: work_item_service.set_status(db, uid, dismissed["id"], "dismissed"))
        self.assertEqual(sync([first[0]]), (0, 0))   # x:2 已被用户忽略，不算自动关闭
        self.assertEqual(sync([]), (0, 1))            # x:1 条件不再成立 → 自动关闭
        resolved = next(i for i in self.items(self.member) if i["title"].startswith("单据 1"))
        self.assertEqual((resolved["status"], resolved["resolved_by"]), ("done", "rule"))
        sync(first)                                   # 条件再次成立：规则关闭的重新打开，用户忽略的保持忽略
        states = {i["title"][:4]: i["status"] for i in self.items(self.member)}
        self.assertEqual(states, {"单据 1": "open", "单据 2": "dismissed"})

    # ---- 规则引擎 ----

    def test_engine_lease_failure_alert_and_force_run(self):
        calls = []

        async def ok(db):
            calls.append(1)
            return 1, 0

        async def boom(db):
            raise RuntimeError("Java 不可用")
        good, bad = ReminderRule("t_ok", "测试", "system", 60, ok), ReminderRule("t_bad", "坏规则", "system", 60, boom)
        try:
            self.assertEqual(run_db(lambda db: reminders.run_rule(db, good))["status"], "ok")
            self.assertIsNone(run_db(lambda db: reminders.run_rule(db, good)))       # 未到下次运行时间
            self.assertEqual(run_db(lambda db: reminders.run_rule(db, good, force=True))["created"], 1)
            run_db(lambda db: self._hold_lease(db, "t_ok"))
            self.assertIsNone(run_db(lambda db: reminders.run_rule(db, good, force=True)))  # 别的 Worker 持有租约
            self.assertEqual(len(calls), 2)
            with patch.object(reminders, "org_admins", AsyncMock(return_value=[self.admin["id"]])):
                for _ in range(3):
                    result = run_db(lambda db: reminders.run_rule(db, bad, force=True))
            self.assertEqual(result["status"], "failed")
            row = run_db(lambda db: db.get(ReminderRun, "t_bad"))
            self.assertEqual((row.consecutive_failures, row.last_status), (3, "failed"))
            alerts = [n for n in self.notifications(self.admin) if n["category"] == "system"]
            self.assertEqual(len(alerts), 1)
            self.assertIn("坏规则", alerts[0]["title"])
        finally:
            self.db.execute(text("DELETE FROM reminder_run WHERE rule IN ('t_ok', 't_bad')"))
            self.db.commit()

    async def _hold_lease(self, db, rule):
        await db.execute(update(ReminderRun).where(ReminderRun.rule == rule)
                         .values(lease_until=utcnow() + timedelta(minutes=5)))
        await db.commit()

    # ---- 具体规则 ----

    def test_approval_waiting(self):
        now = utcnow()
        soon = (now + timedelta(hours=8) + timedelta(days=1)).date().isoformat()
        leave = [{"id": 501, "applicantUserId": self.member["id"], "leaveTypeCode": "annual", "startDate": soon,
                  "endDate": soon, "days": 1, "submittedAt": iso(now - timedelta(hours=30))}]
        purchases = [{"id": 601, "requesterUserId": self.member["id"], "totalAmount": 300,
                      "submittedAt": iso(now - timedelta(hours=2))}]
        expenses = [{"id": 701, "applicantUserId": self.admin["id"], "totalAmount": 50,
                     "submittedAt": iso(now - timedelta(hours=40))}]

        def loader(data):
            return AsyncMock(side_effect=lambda db, uid, team_id: data if team_id == self.team else [])
        with self.scoped(), \
                patch("service.department_workspace_service.list_team_pending_leave_requests_async", loader(leave)), \
                patch("service.procurement_workspace_service.list_team_pending_purchase_requests_async", loader(purchases)), \
                patch("service.finance_workspace_service.list_team_pending_expense_claims_async", loader(expenses)):
            created, _ = run_db(rules.run_approval_waiting)
        items = self.items(self.admin)
        self.assertEqual(created, 1)   # 采购未超时；报销是负责人自己提交的，不能自己审批
        self.assertEqual(len(items), 1)
        self.assertIn("请假单 #501 已等待审批 30 小时", items[0]["title"])
        self.assertEqual(items[0]["priority"], "high")   # 明天就开始
        self.assertIn("即将开始", items[0]["detail"])
        self.assertIn(f"申请人 {self.member['name']}", items[0]["detail"])
        with self.scoped(), patch("service.department_workspace_service.list_team_pending_leave_requests_async",
                                  AsyncMock(return_value=[])), \
                patch("service.procurement_workspace_service.list_team_pending_purchase_requests_async",
                      AsyncMock(return_value=[])), \
                patch("service.finance_workspace_service.list_team_pending_expense_claims_async",
                      AsyncMock(return_value=[])):
            self.assertEqual(run_db(rules.run_approval_waiting), (0, 1))   # 已审批 → 自动关闭

    def test_stale_customers_go_to_owner(self):
        now = utcnow()
        customers = [{"id": 1, "name": "老客户", "ownerUserId": self.member["id"], "createdAt": iso(now - timedelta(days=90))},
                     {"id": 2, "name": "活跃客户", "ownerUserId": self.member["id"], "createdAt": iso(now - timedelta(days=90))}]
        summaries = {1: {"recentFollowUps": [{"createdAt": iso(now - timedelta(days=20))}]},
                     2: {"recentFollowUps": [{"createdAt": iso(now - timedelta(days=2))}]}}
        with self.scoped("sales"), \
                patch("service.crm_workspace_service.list_team_customers_async", AsyncMock(return_value=customers)), \
                patch("service.crm_workspace_service.get_customer_summary_async",
                      AsyncMock(side_effect=lambda db, uid, team, cid: summaries[cid])):
            self.assertEqual(run_db(rules.run_stale_customers)[0], 1)
        items = self.items(self.member)
        self.assertEqual([i["title"] for i in items], ["客户「老客户」已 20 天没有跟进"])

    def test_missing_invoices(self):
        claims = {self.member["id"]: [
            {"id": 9, "status": "SUBMITTED", "teamId": self.team, "lines": [
                {"description": "出租车", "amount": 48, "invoiceNo": None},
                {"description": "高铁", "amount": 260, "invoiceNo": "G1"}]},
            {"id": 10, "status": "APPROVED", "teamId": self.team, "lines": [{"description": "x", "amount": 1, "invoiceNo": None}]}]}
        with self.scoped(), patch("service.finance_workspace_service.list_my_expense_claims_async",
                                  AsyncMock(side_effect=lambda uid: claims.get(uid, []))):
            run_db(rules.run_missing_invoices)
        titles = [i["title"] for i in self.items(self.member)]
        self.assertEqual(titles, ["报销单 #9 有 1 条费用缺发票"])
        self.assertEqual(self.items(self.admin), [])

    def test_task_due_notifies_once(self):
        uid = self.member["id"]
        run_db(lambda db: work_item_service.create_manual(db, uid, "回电话", iso(utcnow() + timedelta(hours=3))))
        run_db(lambda db: work_item_service.create_manual(db, uid, "下周的事", iso(utcnow() + timedelta(days=5))))
        run_db(rules.run_task_due)
        run_db(rules.run_task_due)
        titles = [n["title"] for n in self.notifications(self.member)]
        self.assertEqual(titles, ["待办即将到期：回电话"])

    def test_weekly_digest_once_per_week_for_team_admins(self):
        with self.scoped():
            run_db(rules.run_weekly_digest)
            run_db(rules.run_weekly_digest)
        digests = [n for n in self.notifications(self.admin) if n["category"] == "digest"]
        self.assertEqual(len(digests), 2)   # 两个部门各一份
        self.assertEqual([n for n in self.notifications(self.member) if n["category"] == "digest"], [])


@unittest.skipUnless(_AVAILABLE, _WHY)
class WorkCenterRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.user = rc.create_user("wc-route")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()

    def test_todo_and_notification_routes(self):
        h = self.user["headers"]
        created = self.client.post("/work-items", json={"title": "整理合同", "due_at": "2026-10-08", "priority": "high"},
                                   headers=h)
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(self.client.get("/work-items/counts", headers=h).json()["open"], 1)
        done = self.client.patch(f"/work-items/{created.json()['id']}", json={"status": "done"}, headers=h)
        self.assertEqual(done.json()["status"], "done")
        self.assertEqual(self.client.post("/work-items", json={"title": ""}, headers=h).status_code, 422)
        self.assertEqual(self.client.get("/notifications/unread-count", headers=h).json(), {"unread": 0})
        prefs = self.client.put("/notification-preferences", json={"muted_categories": ["digest"]}, headers=h)
        self.assertEqual(prefs.json()["muted_categories"], ["digest"])
        bad = self.client.put("/notification-preferences", json={"muted_categories": ["system"]}, headers=h)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.get("/admin/reminders", headers=h).status_code, 403)


if __name__ == "__main__":
    unittest.main()
