"""Phase 5：采购 Agent 工具（service/tools/procurement.py）的单测。

跟 tests/test_oa_leave_tools.py 是同一套思路：mock 掉 `hub.call` 和
`hub.resolve_caller_context`，只验证工具层的参数映射/scope/错误转换，不依赖真实的
企业业务中心或 MySQL。真实链路验证在 enterprise-business-hub 自己的
JUnit 集成测试（ProcurementControllerIntegrationTest）里，包括跨部门越权、
自审批拒绝这些资源级检查。

采购比请假多两条要测的：
1. 当前用户既不属于任何部门、也不是企业管理员时，工具应该直接返回错误 JSON，
   不应该带着 team_id=None 去调后端（Java 侧大多数采购接口 team_id 是必填的）。
2. 审批/拒绝工具在本地就要拒绝"既不是部门负责人也不是企业管理员"的调用——
   跟请假工具同一个修复点，权限判断只能来自 `hub.resolve_caller_context()`。
"""
import json
import unittest
from unittest.mock import patch

from service import enterprise_hub_client as hub
from service.tools.base import ToolContext
from service.tools.procurement import (
    ApprovePurchaseRequestTool,
    CreatePurchaseDraftTool,
    GetDepartmentBudgetTool,
    GetInventoryStatusTool,
    GetMyPurchaseRequestsTool,
    GetPurchaseStatusTool,
    GetTeamPendingPurchaseRequestsTool,
    RejectPurchaseRequestTool,
    SubmitPurchaseRequestTool,
)


def _ctx(user_id=2001):
    return ToolContext(user_id=user_id)


def _auth(team_id=9, is_org_admin=False, is_team_admin=False):
    return {"team_id": team_id, "is_org_admin": is_org_admin, "is_team_admin": is_team_admin}


@patch("service.tools.procurement.hub.resolve_caller_context", return_value=_auth())
class ProcurementToolsTest(unittest.TestCase):
    def test_get_inventory_status_builds_read_scope(self, _auth_mock):
        with patch("service.tools.procurement.hub.call", return_value={"sku": "SKU-1"}) as mock_call:
            tool = GetInventoryStatusTool()
            tool.set_context(_ctx())
            result = tool.execute(sku="SKU-1")
        mock_call.assert_called_once_with(
            "GET", "/procurement/products/SKU-1", 2001, 9, ["procurement.read"], "get_inventory_status",
            is_org_admin=False, is_team_admin=False,
        )
        self.assertEqual(json.loads(result)["sku"], "SKU-1")

    def test_get_budget_with_year(self, _auth_mock):
        with patch("service.tools.procurement.hub.call", return_value={"remainingAmount": 500}) as mock_call:
            tool = GetDepartmentBudgetTool()
            tool.set_context(_ctx())
            tool.execute(year=2026)
        mock_call.assert_called_once_with(
            "GET", "/procurement/budget?year=2026", 2001, 9, ["procurement.read"], "get_department_budget",
            is_org_admin=False, is_team_admin=False,
        )

    def test_create_draft_passes_lines_and_write_scope(self, _auth_mock):
        lines = [{"sku": "SKU-1", "quantity": 3}]
        with patch("service.tools.procurement.hub.call", return_value={"id": 1, "status": "DRAFT"}) as mock_call:
            tool = CreatePurchaseDraftTool()
            tool.set_context(_ctx())
            result = tool.execute(lines=lines)
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/procurement/requests", 2001, 9, ["procurement.write"], "create_purchase_draft",
        ))
        self.assertEqual(kwargs["json_body"], {"lines": lines})
        self.assertEqual(json.loads(result)["status"], "DRAFT")

    def test_submit_uses_write_scope(self, _auth_mock):
        with patch("service.tools.procurement.hub.call", return_value={"status": "SUBMITTED"}) as mock_call:
            tool = SubmitPurchaseRequestTool()
            tool.set_context(_ctx())
            tool.execute(request_id=7)
        args, _ = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/procurement/requests/7/submit", 2001, 9, ["procurement.write"], "submit_purchase_request",
        ))

    def test_get_status_uses_read_scope(self, _auth_mock):
        with patch("service.tools.procurement.hub.call", return_value={"status": "APPROVED"}) as mock_call:
            tool = GetPurchaseStatusTool()
            tool.set_context(_ctx())
            tool.execute(request_id=7)
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "GET", "/procurement/requests/7", 2001, 9, ["procurement.read"], "get_purchase_status",
        ))
        self.assertEqual(kwargs["is_org_admin"], False)
        self.assertEqual(kwargs["is_team_admin"], False)

    def test_get_my_purchase_requests_uses_read_scope_and_team_id(self, _auth_mock):
        # 跟请假模块的 GetMyLeaveRequestsTool 不同：那边 team_id 传 None（请假不
        # 分部门查），这里要跟 procurement.py 里其余工具一样传 auth["team_id"]——
        # _require_user_and_auth 已经保证了有部门上下文，混用请假模块的 None 写法
        # 会破坏这个文件内部的一致性。
        with patch("service.tools.procurement.hub.call", return_value=[]) as mock_call:
            tool = GetMyPurchaseRequestsTool()
            tool.set_context(_ctx())
            result = tool.execute()
        mock_call.assert_called_once_with(
            "GET", "/procurement/requests/mine", 2001, 9, ["procurement.read"], "get_my_purchase_requests",
            is_org_admin=False, is_team_admin=False,
        )
        self.assertEqual(json.loads(result), [])

    def test_hub_error_becomes_error_json(self, _auth_mock):
        with patch("service.tools.procurement.hub.call",
                   side_effect=hub.EnterpriseHubError(400, "预算不足")):
            tool = SubmitPurchaseRequestTool()
            tool.set_context(_ctx())
            result = tool.execute(request_id=7)
        parsed = json.loads(result)
        self.assertEqual(parsed["error"], "预算不足")
        self.assertEqual(parsed["status_code"], 400)


class ProcurementNoTeamTest(unittest.TestCase):
    """当前用户既不属于任何部门、也不是企业管理员——不该带着 team_id=None 去调后端，直接拒绝。"""

    @patch("service.tools.procurement.hub.resolve_caller_context",
           return_value=_auth(team_id=None, is_org_admin=False))
    def test_no_team_returns_error_without_calling_hub(self, _auth_mock):
        with patch("service.tools.procurement.hub.call") as mock_call:
            tool = GetInventoryStatusTool()
            tool.set_context(_ctx())
            result = tool.execute(sku="SKU-1")
        mock_call.assert_not_called()
        self.assertIn("不属于任何部门", json.loads(result)["error"])

    @patch("service.tools.procurement.hub.resolve_caller_context",
           return_value=_auth(team_id=None, is_org_admin=True))
    def test_org_admin_without_team_is_allowed_through(self, _auth_mock):
        # 企业管理员没有自己的部门也应该能继续（比如跨部门审批），只是采购草稿/预算类
        # 接口本身还是要求 team_id——那是 Java 侧的业务约束，不是这里要拦的。
        with patch("service.tools.procurement.hub.call", return_value={"status": "APPROVED"}) as mock_call:
            tool = ApprovePurchaseRequestTool()
            tool.set_context(_ctx())
            tool.execute(request_id=7, note="企业管理员批准")
        mock_call.assert_called_once()
        self.assertEqual(mock_call.call_args.kwargs["is_org_admin"], True)


class ApproveRejectAuthorizationTest(unittest.TestCase):
    """跟请假工具同一个修复点：既不是部门负责人也不是企业管理员时，本地直接拒绝，
    不发起网络调用。"""

    @patch("service.tools.procurement.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=False))
    def test_approve_denied_locally_when_neither_org_nor_team_admin(self, _auth_mock):
        with patch("service.tools.procurement.hub.call") as mock_call:
            tool = ApprovePurchaseRequestTool()
            tool.set_context(_ctx())
            result = tool.execute(request_id=7, note="同意")
        mock_call.assert_not_called()
        self.assertIn("error", json.loads(result))

    @patch("service.tools.procurement.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=False))
    def test_reject_denied_locally_when_neither_org_nor_team_admin(self, _auth_mock):
        with patch("service.tools.procurement.hub.call") as mock_call:
            tool = RejectPurchaseRequestTool()
            tool.set_context(_ctx())
            result = tool.execute(request_id=7, note="预算紧张")
        mock_call.assert_not_called()
        self.assertIn("error", json.loads(result))

    @patch("service.tools.procurement.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=True))
    def test_approve_proceeds_when_team_admin(self, _auth_mock):
        with patch("service.tools.procurement.hub.call", return_value={"status": "APPROVED"}) as mock_call:
            tool = ApprovePurchaseRequestTool()
            tool.set_context(_ctx())
            tool.execute(request_id=7, note="同意")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/procurement/requests/7/approve", 2001, 9, ["procurement.approve"], "approve_purchase_request",
        ))
        self.assertEqual(kwargs["json_body"], {"note": "同意"})
        self.assertEqual(kwargs["is_team_admin"], True)

    @patch("service.tools.procurement.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=True, is_team_admin=False))
    def test_reject_proceeds_when_org_admin(self, _auth_mock):
        with patch("service.tools.procurement.hub.call", return_value={"status": "REJECTED"}) as mock_call:
            tool = RejectPurchaseRequestTool()
            tool.set_context(_ctx())
            tool.execute(request_id=7, note="预算紧张")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/procurement/requests/7/reject", 2001, 9, ["procurement.approve"], "reject_purchase_request",
        ))
        self.assertEqual(kwargs["is_org_admin"], True)

    @patch("service.tools.procurement.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=False))
    def test_team_pending_denied_locally_when_neither_org_nor_team_admin(self, _auth_mock):
        with patch("service.tools.procurement.hub.call") as mock_call:
            tool = GetTeamPendingPurchaseRequestsTool()
            tool.set_context(_ctx())
            result = tool.execute()
        mock_call.assert_not_called()
        self.assertIn("error", json.loads(result))

    @patch("service.tools.procurement.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=True))
    def test_team_pending_proceeds_when_team_admin(self, _auth_mock):
        with patch("service.tools.procurement.hub.call", return_value=[]) as mock_call:
            tool = GetTeamPendingPurchaseRequestsTool()
            tool.set_context(_ctx())
            tool.execute()
        mock_call.assert_called_once_with(
            "GET", "/procurement/requests/team-pending?teamId=9", 2001, 9,
            ["procurement.read"], "get_team_pending_purchase_requests",
            is_org_admin=False, is_team_admin=True,
        )


if __name__ == "__main__":
    unittest.main()
