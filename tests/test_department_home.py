"""部门首页概览：卡片按部门业务类型和身份变化；某一项读不到不影响其它；非成员拿不到。Java 调用换成桩。"""
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import SessionLocal
from service.exceptions import PermissionDenied, UpstreamError
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, _WHY)
class DepartmentHomeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.owner = rc.create_user("dh-owner")
        cls.u = {n: rc.create_user(f"dh-{n}") for n in ("acc", "fhead", "itp", "sales", "hrp", "out")}
        cls.org = _create_org(cls.db, "dh-org-" + uuid.uuid4().hex[:6], cls.owner["id"])
        cls.t = {}
        for code in ("finance", "it", "sales", "hr"):
            cls.t[code] = _create_team(cls.db, cls.org, f"dh-{code}", cls.owner["id"])
            cls.db.execute(text("UPDATE teams SET department_code=:c WHERE id=:t"), {"c": code, "t": cls.t[code]})
        cls.db.commit()
        for name, team, role in (("acc", "finance", "member"), ("fhead", "finance", "admin"), ("itp", "it", "member"),
                                 ("sales", "sales", "member"), ("hrp", "hr", "member")):
            _add_team_member(cls.db, cls.t[team], cls.u[name]["id"], role)
            _add_org_member(cls.db, cls.org, cls.u[name]["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        ids = ",".join(str(t) for t in cls.t.values())
        cls.db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({ids})"))
        cls.db.execute(text(f"DELETE FROM teams WHERE id IN ({ids})"))
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    def setUp(self):
        self.patches = {
            "service.it_service.list_my_tickets_async": AsyncMock(return_value=[
                {"status": "WAITING_USER"}, {"status": "IN_PROGRESS"}, {"status": "RESOLVED"}, {"status": "CLOSED"}]),
            "service.hr_service.my_tasks_async": AsyncMock(return_value=[]),
            "service.hr_service.team_pending_approval_async": AsyncMock(return_value=[{"id": 1}]),
            "service.department_workspace_service.list_team_pending_leave_requests_async": AsyncMock(return_value=[{"id": 2}, {"id": 3}]),
            "service.finance_workspace_service.list_team_pending_expense_claims_async": AsyncMock(return_value=[]),
            "service.it_service.list_team_pending_async": AsyncMock(return_value=[]),
            "service.finance_voucher_service.list_vouchers_async": AsyncMock(return_value=[{"riskLevel": "WARN"}, {"riskLevel": "NONE"}]),
            "service.finance_voucher_service.list_unbooked_async": AsyncMock(return_value=[{"id": 9}]),
            "service.it_service.desk_summary_async": AsyncMock(return_value={"unassigned": 2, "overdue": 1,
                                                                               "effect": {"slaMetRate": 95.0, "resolved": 20}}),
            "service.crm_workspace_service.list_team_customers_async": AsyncMock(return_value=[
                {"ownerUserId": self.u["sales"]["id"]}, {"ownerUserId": 0}]),
            "service.hr_service.summary_async": AsyncMock(return_value={"byType": {"ONBOARDING": {"IN_PROGRESS": 2}},
                                                                         "overdueTasks": 1, "effectPending": 1}),
        }
        for target, mock in self.patches.items():
            p = patch(target, mock)
            p.start()
            self.addCleanup(p.stop)

    def home(self, name, team):
        response = self.client.get(f"/enterprise/home?team_id={self.t[team]}", headers=self.u[name]["headers"])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        return body, {c["key"]: c for c in body["cards"]}

    def test_finance_member_sees_voucher_cards_but_not_approvals(self):
        body, cards = self.home("acc", "finance")
        self.assertEqual(body["business_label"], "财务记账")
        self.assertEqual((cards["vouchers"]["value"], cards["vouchers"]["tone"]), (2, "warn"))
        self.assertIn("1 张有风险", cards["vouchers"]["hint"])
        self.assertEqual(cards["unbooked"]["value"], 1)
        self.assertNotIn("approvals", cards)
        self.assertEqual(cards["tickets"]["value"], 2)
        self.assertIn("1 张等你补充", cards["tickets"]["hint"])

    def test_department_head_sees_pending_approvals_summed(self):
        body, cards = self.home("fhead", "finance")
        self.assertTrue(body["identity"]["is_head"])
        self.assertEqual(cards["approvals"]["value"], 3)   # 请假 2 + 人事 1

    def test_it_and_sales_cards(self):
        _, cards = self.home("itp", "it")
        self.assertEqual((cards["it_unassigned"]["value"], cards["it_overdue"]["tone"], cards["it_sla"]["value"]), (2, "danger", "95%"))
        body, cards = self.home("sales", "sales")
        self.assertEqual(cards["customers"]["hint"], "其中由我负责 1 个")
        self.assertEqual(body["business_label"], "客户与商机")

    def test_hr_member_sees_hr_cards(self):
        body, cards = self.home("hrp", "hr")
        self.assertEqual(body["identity"]["roles"], ["HR"])
        self.assertEqual((cards["hr_active"]["value"], cards["hr_overdue"]["tone"], cards["hr_effect"]["value"]), (2, "danger", 1))

    def test_one_failing_source_does_not_break_the_page(self):
        self.patches["service.finance_voucher_service.list_vouchers_async"].side_effect = UpstreamError("业务服务不可用")
        self.patches["service.it_service.list_my_tickets_async"].side_effect = PermissionDenied("x")
        _, cards = self.home("acc", "finance")
        self.assertNotIn("vouchers", cards)
        self.assertNotIn("tickets", cards)
        self.assertIn("unbooked", cards)
        self.assertIn("todos", cards)

    def test_non_member_is_rejected(self):
        response = self.client.get(f"/enterprise/home?team_id={self.t['finance']}", headers=self.u["sales"]["headers"])
        self.assertEqual(response.status_code, 403)
        response = self.client.get(f"/enterprise/home?team_id={self.t['finance']}", headers=self.u["out"]["headers"])
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
