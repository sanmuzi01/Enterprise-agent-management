"""service/finance_workspace_service.py 的单测（里程碑4：部门工作台财务报销闭环）。

结构照抄 tests/test_procurement_workspace_service.py（同款权限模型——审批类操作
要求"部门负责人或企业管理员"），mock 掉 `service.enterprise_hub_client.call`，
真实 DB 建组织/部门/成员关系。核心断言"纯部门成员调审批类函数，PermissionDenied
必须在 hub.call 被调用之前抛出"。
"""
import unittest
from unittest.mock import patch

from sqlalchemy import text

from models.init_db import SessionLocal
from service import enterprise_hub_client as hub
from service import finance_workspace_service as finance_workspace
from service.exceptions import InvalidInput, NotFound, PermissionDenied
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
class FinanceWorkspaceServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.org_admin = rc.create_user("fws-org-admin")
        cls.team_admin = rc.create_user("fws-team-admin")
        cls.member = rc.create_user("fws-member")
        cls.outsider = rc.create_user("fws-outsider")

        cls.org_id = _create_org(cls.db, "fws-test-org", cls.org_admin["id"])
        _add_org_member(cls.db, cls.org_id, cls.org_admin["id"], "admin")
        _add_org_member(cls.db, cls.org_id, cls.team_admin["id"], "member")
        _add_org_member(cls.db, cls.org_id, cls.member["id"], "member")

        cls.team_id = _create_team(cls.db, cls.org_id, "fws-test-team", cls.team_admin["id"])
        _add_team_member(cls.db, cls.team_id, cls.team_admin["id"], "admin")
        _add_team_member(cls.db, cls.team_id, cls.member["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.execute(text("DELETE FROM teams WHERE id=:i"), {"i": cls.team_id})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": cls.org_id})
        cls.db.commit()
        cls.db.close()

    # ---------------- create_my_expense_draft_async ----------------

    def test_create_draft_denied_for_non_member_before_hub_call(self):
        with patch.object(hub, "call") as mock_call:
            with self.assertRaises(PermissionDenied):
                _run_db(lambda db: finance_workspace.create_my_expense_draft_async(
                    db, self.outsider["id"], self.team_id, [{"category": "TRAVEL", "amount": 100}],
                ))
        mock_call.assert_not_called()

    def test_create_draft_calls_hub_when_member(self):
        with patch.object(hub, "call", return_value={"id": 1, "status": "DRAFT"}) as mock_call:
            result = _run_db(lambda db: finance_workspace.create_my_expense_draft_async(
                db, self.member["id"], self.team_id, [{"category": "TRAVEL", "amount": 100}],
            ))
        self.assertEqual(result["status"], "DRAFT")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:2], ("POST", "/finance/expenses"))
        self.assertEqual(args[2], self.member["id"])
        self.assertEqual(args[3], self.team_id)
        self.assertEqual(kwargs["json_body"]["lines"], [{"category": "TRAVEL", "amount": 100}])

    # ---------------- list_team_pending_expense_claims_async ----------------

    def test_team_pending_denied_for_regular_member_before_hub_call(self):
        with patch.object(hub, "call") as mock_call:
            with self.assertRaises(PermissionDenied):
                _run_db(lambda db: finance_workspace.list_team_pending_expense_claims_async(
                    db, self.member["id"], self.team_id,
                ))
        mock_call.assert_not_called()

    def test_team_pending_denied_for_outsider_before_hub_call(self):
        with patch.object(hub, "call") as mock_call:
            with self.assertRaises(PermissionDenied):
                _run_db(lambda db: finance_workspace.list_team_pending_expense_claims_async(
                    db, self.outsider["id"], self.team_id,
                ))
        mock_call.assert_not_called()

    def test_team_pending_allowed_for_team_admin(self):
        with patch.object(hub, "call", return_value=[]) as mock_call:
            result = _run_db(lambda db: finance_workspace.list_team_pending_expense_claims_async(
                db, self.team_admin["id"], self.team_id,
            ))
        self.assertEqual(result, [])
        args, kwargs = mock_call.call_args
        self.assertEqual(kwargs["is_team_admin"], True)
        self.assertEqual(kwargs["is_org_admin"], False)

    def test_team_pending_allowed_for_org_admin(self):
        with patch.object(hub, "call", return_value=[]) as mock_call:
            result = _run_db(lambda db: finance_workspace.list_team_pending_expense_claims_async(
                db, self.org_admin["id"], self.team_id,
            ))
        self.assertEqual(result, [])
        args, kwargs = mock_call.call_args
        self.assertEqual(kwargs["is_org_admin"], True)

    # ---------------- decide_expense_claim_async ----------------

    def test_decide_denied_for_regular_member_before_hub_call(self):
        with patch.object(hub, "call") as mock_call:
            with self.assertRaises(PermissionDenied):
                _run_db(lambda db: finance_workspace.decide_expense_claim_async(
                    db, self.member["id"], 1, self.team_id, "approve", None,
                ))
        mock_call.assert_not_called()

    def test_decide_invalid_action_raises_invalid_input_before_hub_call(self):
        with patch.object(hub, "call") as mock_call:
            with self.assertRaises(InvalidInput):
                _run_db(lambda db: finance_workspace.decide_expense_claim_async(
                    db, self.team_admin["id"], 1, self.team_id, "delete", None,
                ))
        mock_call.assert_not_called()

    def test_decide_allowed_for_team_admin_calls_correct_path(self):
        with patch.object(hub, "call", return_value={"status": "APPROVED"}) as mock_call:
            result = _run_db(lambda db: finance_workspace.decide_expense_claim_async(
                db, self.team_admin["id"], 42, self.team_id, "approve", "同意",
            ))
        self.assertEqual(result["status"], "APPROVED")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:2], ("POST", "/finance/expenses/42/approve"))
        self.assertEqual(kwargs["json_body"], {"note": "同意", "departmentCode": None})

    # ---------------- list_my_expense_claims_async / submit ----------------

    def test_list_mine_calls_hub_with_no_team_id(self):
        with patch.object(hub, "call", return_value=[]) as mock_call:
            _run(finance_workspace.list_my_expense_claims_async(self.member["id"]))
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:2], ("GET", "/finance/expenses/mine"))
        self.assertEqual(args[3], None)  # team_id 不参与"我的报销"这个查询

    def test_submit_calls_hub_with_correct_path(self):
        with patch.object(hub, "call", return_value={"status": "SUBMITTED"}) as mock_call:
            result = _run(finance_workspace.submit_my_expense_claim_async(self.member["id"], 7))
        self.assertEqual(result["status"], "SUBMITTED")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:2], ("POST", "/finance/expenses/7/submit"))

    # ---------------- EnterpriseHubError 翻译 ----------------

    def test_hub_error_translated_to_not_found(self):
        with patch.object(hub, "call", side_effect=hub.EnterpriseHubError(404, "报销单不存在")):
            with self.assertRaises(NotFound):
                _run(finance_workspace.submit_my_expense_claim_async(self.member["id"], 999))

    def test_hub_error_translated_to_invalid_input(self):
        with patch.object(hub, "call", side_effect=hub.EnterpriseHubError(400, "预算不足")):
            with self.assertRaises(InvalidInput):
                _run(finance_workspace.submit_my_expense_claim_async(self.member["id"], 1))


if __name__ == "__main__":
    unittest.main()
