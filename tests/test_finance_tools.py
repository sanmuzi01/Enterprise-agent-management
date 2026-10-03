"""里程碑4：财务报销 Agent 工具（service/tools/finance.py）的单测。

跟 tests/test_procurement_tools.py 是同一套思路：mock 掉 `hub.call` 和
`hub.resolve_caller_context`，只验证工具层的参数映射/scope/错误转换，不依赖真实的
企业业务中心或 MySQL。真实链路验证在 enterprise-business-hub 自己的 JUnit 集成
测试（FinanceControllerIntegrationTest）里。

报销跟采购一样要求 team_id（按部门查预算），审批工具在本地就要拒绝"既不是部门
负责人也不是企业管理员"的调用。
"""
import json
import unittest
from unittest.mock import patch

from service import enterprise_hub_client as hub
from service.tools.base import ToolContext
from service.tools.finance import (
    ApproveExpenseClaimTool,
    CreateExpenseDraftTool,
    GetExpenseBudgetTool,
    GetExpenseStatusTool,
    GetMyExpenseClaimsTool,
    GetTeamPendingExpenseClaimsTool,
    RejectExpenseClaimTool,
    SubmitExpenseClaimTool,
)

def setUpModule():
    # 工具单测用的是伪造的 team_id，部门业务类型校验另有专门测试（tests/test_department_access.py）。
    global _module_patch
    _module_patch = patch("service.department_access.check_team_module")
    _module_patch.start()


def tearDownModule():
    _module_patch.stop()



def _ctx(user_id=4001):
    return ToolContext(user_id=user_id)


def _auth(team_id=6, is_org_admin=False, is_team_admin=False):
    return {"team_id": team_id, "is_org_admin": is_org_admin, "is_team_admin": is_team_admin}


@patch("service.tools.finance.hub.resolve_caller_context", return_value=_auth())
class FinanceToolsTest(unittest.TestCase):
    def test_get_expense_budget_builds_read_scope(self, _auth_mock):
        with patch("service.tools.finance.hub.call", return_value={"remainingAmount": 1000}) as mock_call:
            tool = GetExpenseBudgetTool()
            tool.set_context(_ctx())
            tool.execute()
        mock_call.assert_called_once_with(
            "GET", "/finance/budget", 4001, 6, ["finance.read"], "get_expense_budget",
            is_org_admin=False, is_team_admin=False,
        )

    def test_get_budget_with_year(self, _auth_mock):
        with patch("service.tools.finance.hub.call", return_value={"remainingAmount": 500}) as mock_call:
            tool = GetExpenseBudgetTool()
            tool.set_context(_ctx())
            tool.execute(year=2026)
        mock_call.assert_called_once_with(
            "GET", "/finance/budget?year=2026", 4001, 6, ["finance.read"], "get_expense_budget",
            is_org_admin=False, is_team_admin=False,
        )

    def test_create_draft_passes_lines_and_write_scope(self, _auth_mock):
        lines = [{"category": "TRAVEL", "amount": 100}]
        with patch("service.tools.finance.hub.call", return_value={"id": 1, "status": "DRAFT"}) as mock_call:
            tool = CreateExpenseDraftTool()
            tool.set_context(_ctx())
            result = tool.execute(lines=lines)
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/finance/expenses", 4001, 6, ["finance.write"], "create_expense_draft",
        ))
        self.assertEqual(kwargs["json_body"], {"lines": [
            {"category": "TRAVEL", "amount": 100, "description": None, "invoiceNo": None},
        ]})
        self.assertEqual(json.loads(result)["status"], "DRAFT")

    def test_submit_uses_write_scope(self, _auth_mock):
        with patch("service.tools.finance.hub.call", return_value={"status": "SUBMITTED"}) as mock_call:
            tool = SubmitExpenseClaimTool()
            tool.set_context(_ctx())
            tool.execute(request_id=7)
        args, _ = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/finance/expenses/7/submit", 4001, 6, ["finance.write"], "submit_expense_claim",
        ))

    def test_get_status_uses_read_scope(self, _auth_mock):
        with patch("service.tools.finance.hub.call", return_value={"status": "APPROVED"}) as mock_call:
            tool = GetExpenseStatusTool()
            tool.set_context(_ctx())
            tool.execute(request_id=7)
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "GET", "/finance/expenses/7", 4001, 6, ["finance.read"], "get_expense_status",
        ))
        self.assertEqual(kwargs["is_org_admin"], False)
        self.assertEqual(kwargs["is_team_admin"], False)

    def test_get_my_expense_claims_uses_read_scope_and_team_id(self, _auth_mock):
        with patch("service.tools.finance.hub.call", return_value=[]) as mock_call:
            tool = GetMyExpenseClaimsTool()
            tool.set_context(_ctx())
            result = tool.execute()
        mock_call.assert_called_once_with(
            "GET", "/finance/expenses/mine", 4001, 6, ["finance.read"], "get_my_expense_claims",
            is_org_admin=False, is_team_admin=False,
        )
        self.assertEqual(json.loads(result), [])

    def test_hub_error_becomes_error_json(self, _auth_mock):
        with patch("service.tools.finance.hub.call",
                   side_effect=hub.EnterpriseHubError(400, "预算不足")):
            tool = SubmitExpenseClaimTool()
            tool.set_context(_ctx())
            result = tool.execute(request_id=7)
        parsed = json.loads(result)
        self.assertEqual(parsed["error"], "预算不足")
        self.assertEqual(parsed["status_code"], 400)


class FinanceNoTeamTest(unittest.TestCase):
    """当前用户既不属于任何部门、也不是企业管理员——不该带着 team_id=None 去调后端，直接拒绝。"""

    @patch("service.tools.finance.hub.resolve_caller_context",
           return_value=_auth(team_id=None, is_org_admin=False))
    def test_no_team_returns_error_without_calling_hub(self, _auth_mock):
        with patch("service.tools.finance.hub.call") as mock_call:
            tool = GetExpenseBudgetTool()
            tool.set_context(_ctx())
            result = tool.execute()
        mock_call.assert_not_called()
        self.assertIn("不属于任何部门", json.loads(result)["error"])

    @patch("service.tools.finance.hub.resolve_caller_context",
           return_value=_auth(team_id=None, is_org_admin=True))
    def test_org_admin_without_team_is_allowed_through(self, _auth_mock):
        with patch("service.tools.finance.hub.call", return_value={"status": "APPROVED"}) as mock_call:
            tool = ApproveExpenseClaimTool()
            tool.set_context(_ctx())
            tool.execute(request_id=7, note="企业管理员批准")
        mock_call.assert_called_once()
        self.assertEqual(mock_call.call_args.kwargs["is_org_admin"], True)


class ApproveRejectAuthorizationTest(unittest.TestCase):
    """既不是部门负责人也不是企业管理员时，本地直接拒绝，不发起网络调用。"""

    @patch("service.tools.finance.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=False))
    def test_approve_denied_locally_when_neither_org_nor_team_admin(self, _auth_mock):
        with patch("service.tools.finance.hub.call") as mock_call:
            tool = ApproveExpenseClaimTool()
            tool.set_context(_ctx())
            result = tool.execute(request_id=7, note="同意")
        mock_call.assert_not_called()
        self.assertIn("error", json.loads(result))

    @patch("service.tools.finance.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=False))
    def test_reject_denied_locally_when_neither_org_nor_team_admin(self, _auth_mock):
        with patch("service.tools.finance.hub.call") as mock_call:
            tool = RejectExpenseClaimTool()
            tool.set_context(_ctx())
            result = tool.execute(request_id=7, note="缺发票")
        mock_call.assert_not_called()
        self.assertIn("error", json.loads(result))

    @patch("service.tools.finance.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=True))
    def test_approve_proceeds_when_team_admin(self, _auth_mock):
        with patch("service.tools.finance.hub.call", return_value={"status": "APPROVED"}) as mock_call:
            tool = ApproveExpenseClaimTool()
            tool.set_context(_ctx())
            tool.execute(request_id=7, note="同意")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/finance/expenses/7/approve", 4001, 6, ["finance.approve"], "approve_expense_claim",
        ))
        self.assertEqual(kwargs["json_body"], {"note": "同意"})
        self.assertEqual(kwargs["is_team_admin"], True)

    @patch("service.tools.finance.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=True, is_team_admin=False))
    def test_reject_proceeds_when_org_admin(self, _auth_mock):
        with patch("service.tools.finance.hub.call", return_value={"status": "REJECTED"}) as mock_call:
            tool = RejectExpenseClaimTool()
            tool.set_context(_ctx())
            tool.execute(request_id=7, note="缺发票")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/finance/expenses/7/reject", 4001, 6, ["finance.approve"], "reject_expense_claim",
        ))
        self.assertEqual(kwargs["is_org_admin"], True)

    @patch("service.tools.finance.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=False))
    def test_team_pending_denied_locally_when_neither_org_nor_team_admin(self, _auth_mock):
        with patch("service.tools.finance.hub.call") as mock_call:
            tool = GetTeamPendingExpenseClaimsTool()
            tool.set_context(_ctx())
            result = tool.execute()
        mock_call.assert_not_called()
        self.assertIn("error", json.loads(result))

    @patch("service.tools.finance.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=True))
    def test_team_pending_proceeds_when_team_admin(self, _auth_mock):
        with patch("service.tools.finance.hub.call", return_value=[]) as mock_call:
            tool = GetTeamPendingExpenseClaimsTool()
            tool.set_context(_ctx())
            tool.execute()
        mock_call.assert_called_once_with(
            "GET", "/finance/expenses/team-pending?teamId=6", 4001, 6,
            ["finance.read"], "get_team_pending_expense_claims",
            is_org_admin=False, is_team_admin=True,
        )


if __name__ == "__main__":
    unittest.main()
