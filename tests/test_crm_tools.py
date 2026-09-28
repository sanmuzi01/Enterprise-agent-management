"""Phase 5：CRM Agent 工具（service/tools/crm.py）的单测，跟
tests/test_oa_leave_tools.py / test_procurement_tools.py 是同一套思路：
mock 掉 `hub.call` 和 `hub.resolve_caller_context`，只验证参数映射/scope/错误转换。
"""
import json
import unittest
from unittest.mock import patch

from service import enterprise_hub_client as hub
from service.tools.base import ToolContext
from service.tools.crm import (
    CreateFollowupDraftTool,
    CreateOrUpdateOpportunityTool,
    GetCustomerSummaryTool,
    GetOpportunitiesTool,
    SubmitCustomerFollowupTool,
)


def _ctx(user_id=3001):
    return ToolContext(user_id=user_id)


def _auth(team_id=5, is_org_admin=False, is_team_admin=False):
    return {"team_id": team_id, "is_org_admin": is_org_admin, "is_team_admin": is_team_admin}


@patch("service.tools.crm.hub.resolve_caller_context", return_value=_auth())
class CrmToolsTest(unittest.TestCase):
    def test_get_customer_summary_uses_read_scope(self, _auth_mock):
        with patch("service.tools.crm.hub.call", return_value={"id": 10}) as mock_call:
            tool = GetCustomerSummaryTool()
            tool.set_context(_ctx())
            result = tool.execute(customer_id=10)
        mock_call.assert_called_once_with(
            "GET", "/crm/customers/10", 3001, 5, ["crm.read"], "get_customer_summary",
            is_org_admin=False, is_team_admin=False,
        )
        self.assertEqual(json.loads(result)["id"], 10)

    def test_create_followup_draft_passes_content(self, _auth_mock):
        with patch("service.tools.crm.hub.call", return_value={"id": 1, "status": "DRAFT"}) as mock_call:
            tool = CreateFollowupDraftTool()
            tool.set_context(_ctx())
            result = tool.execute(customer_id=10, content="拜访了张经理")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/crm/customers/10/followups", 3001, 5, ["crm.write"], "create_followup_draft",
        ))
        self.assertEqual(kwargs["json_body"], {"content": "拜访了张经理"})
        self.assertEqual(json.loads(result)["status"], "DRAFT")

    def test_submit_followup_uses_write_scope(self, _auth_mock):
        with patch("service.tools.crm.hub.call", return_value={"status": "CONFIRMED"}) as mock_call:
            tool = SubmitCustomerFollowupTool()
            tool.set_context(_ctx())
            tool.execute(followup_id=1)
        args, _ = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/crm/followups/1/confirm", 3001, 5, ["crm.write"], "submit_customer_followup",
        ))

    def test_create_opportunity_without_id_omits_opportunityId(self, _auth_mock):
        with patch("service.tools.crm.hub.call", return_value={"id": 5, "stage": "LEAD"}) as mock_call:
            tool = CreateOrUpdateOpportunityTool()
            tool.set_context(_ctx())
            tool.execute(customer_id=10, stage="LEAD", amount=1000)
        _, kwargs = mock_call.call_args
        self.assertEqual(kwargs["json_body"], {"stage": "LEAD", "amount": 1000})

    def test_update_opportunity_with_id_includes_opportunityId(self, _auth_mock):
        with patch("service.tools.crm.hub.call", return_value={"id": 5, "stage": "WON"}) as mock_call:
            tool = CreateOrUpdateOpportunityTool()
            tool.set_context(_ctx())
            tool.execute(customer_id=10, opportunity_id=5, stage="WON", amount=2000)
        _, kwargs = mock_call.call_args
        self.assertEqual(kwargs["json_body"], {"stage": "WON", "amount": 2000, "opportunityId": 5})

    def test_get_opportunities_uses_read_scope(self, _auth_mock):
        with patch("service.tools.crm.hub.call", return_value=[]) as mock_call:
            tool = GetOpportunitiesTool()
            tool.set_context(_ctx())
            tool.execute(customer_id=10)
        args, _ = mock_call.call_args
        self.assertEqual(args[:6], (
            "GET", "/crm/customers/10/opportunities", 3001, 5, ["crm.read"], "get_opportunities",
        ))

    def test_hub_error_becomes_error_json(self, _auth_mock):
        with patch("service.tools.crm.hub.call", side_effect=hub.EnterpriseHubError(404, "客户不存在")):
            tool = GetCustomerSummaryTool()
            tool.set_context(_ctx())
            result = tool.execute(customer_id=999)
        parsed = json.loads(result)
        self.assertEqual(parsed["error"], "客户不存在")
        self.assertEqual(parsed["status_code"], 404)


class CrmNoTeamTest(unittest.TestCase):
    @patch("service.tools.crm.hub.resolve_caller_context", return_value=_auth(team_id=None, is_org_admin=False))
    def test_no_team_returns_error_without_calling_hub(self, _auth_mock):
        with patch("service.tools.crm.hub.call") as mock_call:
            tool = GetCustomerSummaryTool()
            tool.set_context(_ctx())
            result = tool.execute(customer_id=10)
        mock_call.assert_not_called()
        self.assertIn("不属于任何部门", json.loads(result)["error"])

    @patch("service.tools.crm.hub.resolve_caller_context", return_value=_auth(team_id=None, is_org_admin=True))
    def test_org_admin_without_team_is_allowed_through(self, _auth_mock):
        with patch("service.tools.crm.hub.call", return_value={"id": 10}) as mock_call:
            tool = GetCustomerSummaryTool()
            tool.set_context(_ctx())
            tool.execute(customer_id=10)
        mock_call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
