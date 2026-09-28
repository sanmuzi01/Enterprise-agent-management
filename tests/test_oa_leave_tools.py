"""Phase 5：OA 请假 Agent 工具（service/tools/oa_leave.py）的单测。

不依赖真实的企业业务中心（Spring Boot）或 MySQL——`enterprise_hub_client.call` 和
`resolve_caller_context` 都打桩，只验证工具层自己的职责：参数怎么映射成请求体、
成功怎么转 JSON、`EnterpriseHubError` 怎么转成给 LLM 看的错误 JSON、以及审批类工具
在本地就拒绝"既不是部门负责人也不是企业管理员"的调用（不依赖网络往返）。真实的
端到端链路验证见 `scripts/smoke_test_enterprise_hub.py`（需要真的起 Java 服务）和
`enterprise-business-hub` 自己的 JUnit 集成测试（包括跨部门越权、自审批拒绝这些
资源级检查——Python 侧只测"该不该发起这次调用"，资源归属判断在 Java 那边）。
"""
import json
import unittest
from unittest.mock import patch

from service import enterprise_hub_client as hub
from service.tools.base import ToolContext
from service.tools.oa_leave import (
    ApproveLeaveRequestTool,
    CreateLeaveDraftTool,
    GetLeaveBalanceTool,
    GetLeaveStatusTool,
    RejectLeaveRequestTool,
    SubmitLeaveRequestTool,
)


def _ctx(user_id=1001):
    return ToolContext(user_id=user_id)


def _auth(team_id=7, is_org_admin=False, is_team_admin=False):
    return {"team_id": team_id, "is_org_admin": is_org_admin, "is_team_admin": is_team_admin}


@patch("service.tools.oa_leave.hub.resolve_caller_context", return_value=_auth())
class OaLeaveToolsTest(unittest.TestCase):
    def test_get_balance_builds_read_scope_and_year_query(self, _auth_mock):
        with patch("service.tools.oa_leave.hub.call", return_value=[{"leaveTypeCode": "annual"}]) as mock_call:
            tool = GetLeaveBalanceTool()
            tool.set_context(_ctx())
            result = tool.execute(year=2026)
        mock_call.assert_called_once_with(
            "GET", "/oa/leave/balance?year=2026", 1001, 7, ["oa.leave.read"], "get_leave_balance",
            is_org_admin=False, is_team_admin=False,
        )
        self.assertEqual(json.loads(result), [{"leaveTypeCode": "annual"}])

    def test_get_balance_without_year_omits_query(self, _auth_mock):
        with patch("service.tools.oa_leave.hub.call", return_value=[]) as mock_call:
            tool = GetLeaveBalanceTool()
            tool.set_context(_ctx())
            tool.execute()
        mock_call.assert_called_once_with(
            "GET", "/oa/leave/balance", 1001, 7, ["oa.leave.read"], "get_leave_balance",
            is_org_admin=False, is_team_admin=False,
        )

    def test_create_draft_maps_fields_and_write_scope(self, _auth_mock):
        with patch("service.tools.oa_leave.hub.call", return_value={"id": 1, "status": "DRAFT"}) as mock_call:
            tool = CreateLeaveDraftTool()
            tool.set_context(_ctx())
            result = tool.execute(leave_type_code="annual", start_date="2026-10-01",
                                   end_date="2026-10-02", reason="测试")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], ("POST", "/oa/leave/requests", 1001, 7, ["oa.leave.write"], "create_leave_draft"))
        self.assertEqual(kwargs["json_body"], {
            "leaveTypeCode": "annual", "startDate": "2026-10-01", "endDate": "2026-10-02", "reason": "测试",
        })
        self.assertIn("idempotency_key", kwargs)
        self.assertEqual(json.loads(result)["status"], "DRAFT")

    def test_submit_uses_write_scope_and_path(self, _auth_mock):
        with patch("service.tools.oa_leave.hub.call", return_value={"status": "SUBMITTED"}) as mock_call:
            tool = SubmitLeaveRequestTool()
            tool.set_context(_ctx())
            tool.execute(request_id=42)
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/oa/leave/requests/42/submit", 1001, 7, ["oa.leave.write"], "submit_leave_request",
        ))

    def test_get_status_uses_read_scope(self, _auth_mock):
        with patch("service.tools.oa_leave.hub.call", return_value={"status": "APPROVED"}) as mock_call:
            tool = GetLeaveStatusTool()
            tool.set_context(_ctx())
            tool.execute(request_id=42)
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "GET", "/oa/leave/requests/42", 1001, 7, ["oa.leave.read"], "get_leave_status",
        ))
        self.assertEqual(kwargs["is_org_admin"], False)
        self.assertEqual(kwargs["is_team_admin"], False)

    def test_hub_error_becomes_error_json_not_exception(self, _auth_mock):
        with patch("service.tools.oa_leave.hub.call", side_effect=hub.EnterpriseHubError(400, "余额不足")):
            tool = SubmitLeaveRequestTool()
            tool.set_context(_ctx())
            result = tool.execute(request_id=1)
        parsed = json.loads(result)
        self.assertEqual(parsed["error"], "余额不足")
        self.assertEqual(parsed["status_code"], 400)

    def test_missing_context_raises_value_error(self, _auth_mock):
        tool = GetLeaveBalanceTool()
        # 不调用 set_context —— 模拟工具被直接调用、没有 ToolExecutor 注入上下文的情况
        with self.assertRaises(ValueError):
            tool.execute()


class ApproveRejectAuthorizationTest(unittest.TestCase):
    """审批/拒绝工具的核心修复点：既不是部门负责人也不是企业管理员时，本地直接
    拒绝，不发起网络调用——这是"工具能自行签发审批权限"那个漏洞的修复。"""

    @patch("service.tools.oa_leave.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=False))
    def test_approve_denied_locally_when_neither_org_nor_team_admin(self, _auth_mock):
        with patch("service.tools.oa_leave.hub.call") as mock_call:
            tool = ApproveLeaveRequestTool()
            tool.set_context(_ctx())
            result = tool.execute(request_id=42, note="同意")
        mock_call.assert_not_called()
        self.assertIn("error", json.loads(result))

    @patch("service.tools.oa_leave.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=False))
    def test_reject_denied_locally_when_neither_org_nor_team_admin(self, _auth_mock):
        with patch("service.tools.oa_leave.hub.call") as mock_call:
            tool = RejectLeaveRequestTool()
            tool.set_context(_ctx())
            result = tool.execute(request_id=42, note="人手不够")
        mock_call.assert_not_called()
        self.assertIn("error", json.loads(result))

    @patch("service.tools.oa_leave.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=False, is_team_admin=True))
    def test_approve_proceeds_when_team_admin(self, _auth_mock):
        with patch("service.tools.oa_leave.hub.call", return_value={"status": "APPROVED"}) as mock_call:
            tool = ApproveLeaveRequestTool()
            tool.set_context(_ctx())
            tool.execute(request_id=42, note="同意")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/oa/leave/requests/42/approve", 1001, 7, ["oa.leave.approve"], "approve_leave_request",
        ))
        self.assertEqual(kwargs["json_body"], {"note": "同意"})
        self.assertEqual(kwargs["is_team_admin"], True)

    @patch("service.tools.oa_leave.hub.resolve_caller_context",
           return_value=_auth(is_org_admin=True, is_team_admin=False))
    def test_reject_proceeds_when_org_admin(self, _auth_mock):
        with patch("service.tools.oa_leave.hub.call", return_value={"status": "REJECTED"}) as mock_call:
            tool = RejectLeaveRequestTool()
            tool.set_context(_ctx())
            tool.execute(request_id=42, note="人手不够")
        args, kwargs = mock_call.call_args
        self.assertEqual(args[:6], (
            "POST", "/oa/leave/requests/42/reject", 1001, 7, ["oa.leave.approve"], "reject_leave_request",
        ))
        self.assertEqual(kwargs["is_org_admin"], True)


class SignContextTest(unittest.TestCase):
    def test_signature_is_deterministic_hmac_over_base64_payload(self):
        import base64
        import hashlib
        import hmac as hmac_module

        with patch("service.enterprise_hub_client._secret", return_value="unit-test-secret"):
            headers = hub.sign_context(1, 2, ["a.b"], "op", is_org_admin=True, is_team_admin=False)
        payload = headers["X-Context"]
        expected_sig = hmac_module.new(
            b"unit-test-secret", payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        self.assertEqual(headers["X-Signature"], expected_sig)

        decoded = json.loads(base64.b64decode(payload))
        self.assertEqual(decoded["user_id"], 1)
        self.assertEqual(decoded["team_id"], 2)
        self.assertEqual(decoded["scopes"], ["a.b"])
        self.assertEqual(decoded["operation"], "op")
        self.assertEqual(decoded["is_org_admin"], True)
        self.assertEqual(decoded["is_team_admin"], False)
        self.assertIn("nonce", decoded)
        self.assertIn("timestamp", decoded)

    def test_sign_context_defaults_admin_flags_to_false(self):
        import base64

        with patch("service.enterprise_hub_client._secret", return_value="unit-test-secret"):
            headers = hub.sign_context(1, 2, ["a.b"], "op")
        decoded = json.loads(base64.b64decode(headers["X-Context"]))
        self.assertEqual(decoded["is_org_admin"], False)
        self.assertEqual(decoded["is_team_admin"], False)


if __name__ == "__main__":
    unittest.main()
