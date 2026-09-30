"""service/crm_workspace_service.py 的单测（里程碑3：部门工作台 CRM 客户跟进/商机闭环）。

结构照抄 tests/test_procurement_workspace_service.py，但断言点不同：CRM 没有
审批/负责人概念，只判断"是不是本部门成员"——所以这里测的是"非本部门成员调
任何函数，PermissionDenied 必须在 hub.call 被调用之前抛出"，不是"非负责人"。
"""
import unittest
from unittest.mock import patch

from sqlalchemy import text

from models.init_db import SessionLocal
from service import crm_workspace_service as crm_workspace
from service import enterprise_hub_client as hub
from service.exceptions import NotFound, PermissionDenied
from tests import _route_client as rc
from tests._async_helpers import run_async as _run
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


def _run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def _wrapper():
        async with AsyncSessionLocal() as db:
            return await fn(db)

    return _run(_wrapper())


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class CrmWorkspaceServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.org_admin = rc.create_user("cws-org-admin")
        cls.member = rc.create_user("cws-member")
        cls.outsider = rc.create_user("cws-outsider")

        cls.org_id = _create_org(cls.db, "cws-test-org", cls.org_admin["id"])
        _add_org_member(cls.db, cls.org_id, cls.org_admin["id"], "admin")
        _add_org_member(cls.db, cls.org_id, cls.member["id"], "member")

        cls.team_id = _create_team(cls.db, cls.org_id, "cws-test-team", cls.org_admin["id"])
        _add_team_member(cls.db, cls.team_id, cls.member["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.execute(text("DELETE FROM teams WHERE id=:i"), {"i": cls.team_id})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": cls.org_id})
        cls.db.commit()
        cls.db.close()

    # ---------------- list_team_customers_async ----------------

    def test_list_customers_denied_for_outsider_before_hub_call(self):
        with patch.object(hub, "call") as mock_call:
            with self.assertRaises(PermissionDenied):
                _run_db(lambda db: crm_workspace.list_team_customers_async(db, self.outsider["id"], self.team_id))
        mock_call.assert_not_called()

    def test_list_customers_calls_hub_when_member(self):
        with patch.object(hub, "call", return_value=[{"id": 1, "name": "客户A"}]) as mock_call:
            result = _run_db(lambda db: crm_workspace.list_team_customers_async(db, self.member["id"], self.team_id))
        self.assertEqual(result, [{"id": 1, "name": "客户A"}])
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:4], ("GET", "/crm/customers", self.member["id"], self.team_id))

    # ---------------- get_customer_summary_async ----------------

    def test_get_summary_denied_for_outsider_before_hub_call(self):
        with patch.object(hub, "call") as mock_call:
            with self.assertRaises(PermissionDenied):
                _run_db(lambda db: crm_workspace.get_customer_summary_async(
                    db, self.outsider["id"], self.team_id, 1,
                ))
        mock_call.assert_not_called()

    def test_get_summary_calls_hub_when_member(self):
        with patch.object(hub, "call", return_value={"id": 1, "name": "客户A"}) as mock_call:
            result = _run_db(lambda db: crm_workspace.get_customer_summary_async(
                db, self.member["id"], self.team_id, 1,
            ))
        self.assertEqual(result["id"], 1)
        args, _ = mock_call.call_args
        self.assertEqual(args[:2], ("GET", "/crm/customers/1"))

    # ---------------- create_followup_draft_async ----------------

    def test_create_followup_denied_for_outsider_before_hub_call(self):
        with patch.object(hub, "call") as mock_call:
            with self.assertRaises(PermissionDenied):
                _run_db(lambda db: crm_workspace.create_followup_draft_async(
                    db, self.outsider["id"], self.team_id, 1, "拜访了客户",
                ))
        mock_call.assert_not_called()

    def test_create_followup_calls_hub_when_member(self):
        with patch.object(hub, "call", return_value={"id": 9, "status": "DRAFT"}) as mock_call:
            result = _run_db(lambda db: crm_workspace.create_followup_draft_async(
                db, self.member["id"], self.team_id, 1, "拜访了客户",
            ))
        self.assertEqual(result["status"], "DRAFT")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:2], ("POST", "/crm/customers/1/followups"))
        self.assertEqual(kwargs["json_body"], {"content": "拜访了客户"})

    # ---------------- confirm_followup_async ----------------

    def test_confirm_followup_calls_hub_with_no_team_id(self):
        with patch.object(hub, "call", return_value={"status": "CONFIRMED"}) as mock_call:
            result = _run(crm_workspace.confirm_followup_async(self.member["id"], 9))
        self.assertEqual(result["status"], "CONFIRMED")
        args, _ = mock_call.call_args
        self.assertEqual(args[:4], ("POST", "/crm/followups/9/confirm", self.member["id"], None))

    # ---------------- upsert_opportunity_async ----------------

    def test_upsert_opportunity_denied_for_outsider_before_hub_call(self):
        with patch.object(hub, "call") as mock_call:
            with self.assertRaises(PermissionDenied):
                _run_db(lambda db: crm_workspace.upsert_opportunity_async(
                    db, self.outsider["id"], self.team_id, 1, None, "QUALIFIED", 50000,
                ))
        mock_call.assert_not_called()

    def test_upsert_opportunity_new_omits_opportunity_id(self):
        with patch.object(hub, "call", return_value={"id": 3, "stage": "QUALIFIED"}) as mock_call:
            result = _run_db(lambda db: crm_workspace.upsert_opportunity_async(
                db, self.member["id"], self.team_id, 1, None, "QUALIFIED", 50000,
            ))
        self.assertEqual(result["stage"], "QUALIFIED")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:2], ("POST", "/crm/customers/1/opportunities"))
        self.assertEqual(kwargs["json_body"], {"stage": "QUALIFIED", "amount": 50000})

    def test_upsert_opportunity_update_includes_opportunity_id(self):
        with patch.object(hub, "call", return_value={"id": 3, "stage": "NEGOTIATION"}) as mock_call:
            _run_db(lambda db: crm_workspace.upsert_opportunity_async(
                db, self.member["id"], self.team_id, 1, 3, "NEGOTIATION", 60000,
            ))
        args, kwargs = mock_call.call_args
        self.assertEqual(kwargs["json_body"], {"stage": "NEGOTIATION", "amount": 60000, "opportunityId": 3})

    # ---------------- EnterpriseHubError 翻译 ----------------

    def test_hub_error_translated_to_not_found(self):
        with patch.object(hub, "call", side_effect=hub.EnterpriseHubError(404, "客户不存在")):
            with self.assertRaises(NotFound):
                _run(crm_workspace.confirm_followup_async(self.member["id"], 999))


if __name__ == "__main__":
    unittest.main()
