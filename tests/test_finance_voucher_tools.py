"""财务记账 Agent 工具：权限与财务工作台一致，且只有“整理/建议”类工具，没有能入账的工具。"""
import json
import unittest
from unittest.mock import patch

from service import enterprise_agent_templates as templates
from service.exceptions import PermissionDenied
from service.tools.base import ToolContext, ToolRegistry
from service.tools.finance_voucher import (
    GenerateVoucherDraftTool,
    GetVoucherDetailTool,
    GetVoucherMonthlySummaryTool,
    ListPendingVouchersTool,
)

AUTH = {"team_id": 6, "is_org_admin": False, "is_team_admin": False}


def _tool(cls, user_id=4101):
    tool = cls()
    tool.set_context(ToolContext(user_id=user_id))
    return tool


@patch("service.tools.finance_voucher.hub.resolve_caller_context", return_value=AUTH)
@patch("service.tools.finance_voucher.svc.staff_scope", return_value=[6, 7, 8])
class VoucherToolsTest(unittest.TestCase):
    def test_list_pending_requests_drafts_with_enterprise_scope(self, _scope, _auth):
        with patch("service.tools.finance_voucher.hub.call", return_value=[{"id": 1}]) as call:
            result = _tool(ListPendingVouchersTool).execute(limit=500)
        args, kwargs = call.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "/finance/vouchers?scopeTeamIds=6,7,8&status=DRAFT&limit=100")
        self.assertEqual(args[2:5], (4101, 6, ["finance.voucher.read"]))
        self.assertIsNone(kwargs["idempotency_key"])
        self.assertEqual(json.loads(result), [{"id": 1}])

    def test_detail_reads_one_voucher(self, _scope, _auth):
        with patch("service.tools.finance_voucher.hub.call", return_value={"id": 9}) as call:
            _tool(GetVoucherDetailTool).execute(voucher_id=9)
        self.assertEqual(call.call_args.args[1], "/finance/vouchers/9?scopeTeamIds=6,7,8")

    def test_generate_draft_uses_write_scope_and_idempotency_key(self, _scope, _auth):
        with patch("service.tools.finance_voucher.hub.call", return_value={"id": 3}) as call:
            _tool(GenerateVoucherDraftTool).execute(claim_id=12)
        args, kwargs = call.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "/finance/vouchers/from-claim/12?scopeTeamIds=6,7,8")
        self.assertIn("finance.voucher.write", args[4])
        self.assertTrue(kwargs["idempotency_key"])

    def test_monthly_summary_adds_narrative(self, _scope, _auth):
        summary = {"period": "2026-10", "balanced": True, "byStatus": {"POSTED": {"count": 1, "amount": 10}},
                   "bySubject": []}
        with patch("service.tools.finance_voucher.hub.call", return_value=summary) as call:
            result = _tool(GetVoucherMonthlySummaryTool).execute(period="2026-10")
        self.assertIn("period=2026-10", call.call_args.args[1])
        self.assertIn("已入账凭证 1 张", json.loads(result)["narrative"])


class VoucherToolAccessTest(unittest.TestCase):
    @patch("service.tools.finance_voucher.hub.resolve_caller_context", return_value=AUTH)
    def test_non_finance_user_is_rejected_without_calling_the_business_system(self, _auth):
        with patch("service.tools.finance_voucher.svc.staff_scope", side_effect=PermissionDenied("记账凭证仅对财务部门开放")), \
                patch("service.tools.finance_voucher.hub.call") as call:
            for cls, kwargs in ((ListPendingVouchersTool, {}), (GetVoucherDetailTool, {"voucher_id": 1}),
                                (GenerateVoucherDraftTool, {"claim_id": 1}), (GetVoucherMonthlySummaryTool, {})):
                result = json.loads(_tool(cls).execute(**kwargs))
                self.assertIn("仅对财务部门开放", result["error"], cls.__name__)
        call.assert_not_called()

    @patch("service.tools.finance_voucher.hub.resolve_caller_context",
           return_value={"team_id": None, "is_org_admin": False, "is_team_admin": False})
    def test_user_without_department_is_rejected(self, _auth):
        with patch("service.tools.finance_voucher.hub.call") as call:
            result = json.loads(_tool(ListPendingVouchersTool).execute())
        self.assertIn("不属于任何财务部门", result["error"])
        call.assert_not_called()

    def test_no_tool_can_post_void_or_change_subjects(self):
        names = set(ToolRegistry.list_all())
        self.assertIn("list_pending_vouchers", names)
        for name in names:
            self.assertNotIn("confirm_voucher", name)
            self.assertNotIn("void_voucher", name)
            self.assertNotIn("post_voucher", name)

    def test_finance_template_exposes_voucher_tools_and_routing_keywords(self):
        template = templates.get_template("finance")
        for name in ("list_pending_vouchers", "get_voucher_detail", "generate_voucher_draft",
                     "get_voucher_monthly_summary"):
            self.assertIn(name, template["tools"])
            self.assertIsNotNone(ToolRegistry.get(name))
        for keyword in ("凭证", "记账", "入账", "科目"):
            self.assertIn(keyword, template["routing_keywords"])
        self.assertFalse({"confirm_voucher", "void_voucher"} & set(template["tools"]))


if __name__ == "__main__":
    unittest.main()
