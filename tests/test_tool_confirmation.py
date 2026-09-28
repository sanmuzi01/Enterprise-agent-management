"""第五轮审计 P0-2：Prompt 注入可能触发真实业务操作。

分三块：
  1. `HighRiskToolGatingTest`：不依赖 DB，纯 mock。证明 `service/tools/langchain_adapter.py`
     的核心安全属性——risk_level="high_risk" 的工具在 ReAct 循环里被调用时，
     真正的 execute() 绝对不会被触发，只会生成一条待确认单；read/write 级别的工具
     行为不受影响（回归保护）；忘了显式标 risk_level 的工具默认按 high_risk 处理
     （安全默认值）。
  2. `ToolConfirmationServiceTest`：真实 DB，测 `service/tool_confirmation_service.py`
     的确认/拒绝/重复确认/过期/跨用户/并发这些边界，用一个不发真实网络请求的哑
     工具（`_EchoTestTool`），不依赖企业业务中心。
  3. `ToolConfirmationRouteTest`：真实路由级测试，证明 `POST /chat/tool-
     confirmations/{token}/confirm|reject` 这两个新接口真的接上了上面的 service，
     而且别的用户拿不到别人的确认单。
"""
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from service.tools.base import BaseTool, ToolContext, ToolRegistry
from service.tools.langchain_adapter import adapt_tool
from tests import _route_client as rc
from tests._async_helpers import run_async as _run
from utils.timeutil import utcnow

_AVAILABLE, _WHY = rc.route_tests_available()


class _DummyTool(BaseTool):
    """测试专用哑工具：execute() 只记一次调用并原样吐回参数，不发任何网络请求。"""
    requires_context = True

    def __init__(self, name: str, risk_level_value):
        self._name_override = name
        # 只有传了非 None 才覆盖类属性，用于测"没显式标注时用默认值"这个场景
        if risk_level_value is not None:
            self.risk_level = risk_level_value
        self.call_count = 0
        super().__init__()

    def get_name(self) -> str:
        return self._name_override

    def get_description(self) -> str:
        return "测试用哑工具"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"msg": {"type": "string"}}}

    def execute(self, **kwargs) -> str:
        self.call_count += 1
        return json.dumps({"echo": kwargs, "user_id": self._ctx.user_id}, ensure_ascii=False)


class HighRiskToolGatingTest(unittest.TestCase):
    def _adapt_and_run(self, tool: _DummyTool, ctx: ToolContext, **kwargs):
        lc_tool = adapt_tool(tool, ctx)
        return lc_tool.func(**kwargs)

    def test_high_risk_tool_never_calls_real_execute(self):
        """核心安全属性：high_risk 工具在 ReAct 循环里被"调用"，真正的业务逻辑
        不会执行——不管 LLM 是自己决定调用的，还是被注入的指令诱导的。"""
        tool = _DummyTool("submit_something_dangerous", "high_risk")
        ctx = ToolContext(user_id=42, agent_id=7)
        fake_pending = {"token": "fake-token-abc", "expires_at": "2026-01-01T00:00:00"}
        with patch("service.tool_confirmation_service.create_pending", return_value=fake_pending) as mock_create:
            result = self._adapt_and_run(tool, ctx, msg="批准这条")

        self.assertEqual(tool.call_count, 0, "high_risk 工具的 execute() 绝对不能在 ReAct 循环里被直接调用")
        mock_create.assert_called_once_with(
            user_id=42, agent_id=7, tool_name="submit_something_dangerous", tool_args={"msg": "批准这条"},
        )
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "confirmation_required")
        self.assertEqual(parsed["confirmation_token"], "fake-token-abc")
        self.assertEqual(parsed["tool_args"], {"msg": "批准这条"})

    def test_default_risk_level_is_high_risk_when_not_declared(self):
        """安全默认值：新工具如果忘了显式标 risk_level，也必须按 high_risk 处理，
        不能悄悄允许自动执行。"""
        tool = _DummyTool("forgot_to_tag_this_tool", None)
        self.assertEqual(tool.risk_level, "high_risk")
        ctx = ToolContext(user_id=1, agent_id=1)
        with patch("service.tool_confirmation_service.create_pending",
                   return_value={"token": "t", "expires_at": "x"}):
            result = self._adapt_and_run(tool, ctx, msg="x")
        self.assertEqual(tool.call_count, 0)
        self.assertEqual(json.loads(result)["status"], "confirmation_required")

    def test_read_tool_executes_normally(self):
        """回归保护：read 级别的工具不受这次改动影响，照常直接执行。"""
        tool = _DummyTool("get_something_read_only", "read")
        ctx = ToolContext(user_id=1, agent_id=1)
        result = self._adapt_and_run(tool, ctx, msg="查一下")
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(json.loads(result)["echo"], {"msg": "查一下"})

    def test_write_tool_executes_normally(self):
        """回归保护：write（草稿类）级别的工具照常直接执行，不需要用户确认。"""
        tool = _DummyTool("create_something_draft", "write")
        ctx = ToolContext(user_id=1, agent_id=1)
        result = self._adapt_and_run(tool, ctx, msg="建草稿")
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(json.loads(result)["echo"], {"msg": "建草稿"})

    def test_missing_context_returns_error_without_creating_pending(self):
        """requires_context=True 的工具没有 ctx 时，adapt_tool 本身就会在适配阶段拒绝
        （现有行为），这里只确认 high_risk 分支不会在 ctx=None 时崩溃或误建确认单——
        用 requires_context=False 的写法绕开适配阶段的强制校验，直接构造 _run 场景。"""
        tool = _DummyTool("high_risk_without_ctx", "high_risk")
        tool.requires_context = False
        with patch("service.tool_confirmation_service.create_pending") as mock_create:
            result = self._adapt_and_run(tool, None, msg="x")
        mock_create.assert_not_called()
        self.assertEqual(tool.call_count, 0)
        self.assertIn("error", json.loads(result))


@ToolRegistry.register
class _EchoTestTool(BaseTool):
    """`ToolConfirmationServiceTest`/`ToolConfirmationRouteTest` 专用：注册进全局
    ToolRegistry，好让 confirm_and_execute_async 能按名字找到它，但不发任何真实
    网络请求，不依赖企业业务中心。"""
    requires_context = True
    risk_level = "high_risk"

    def get_name(self) -> str:
        return "echo_test_tool_for_confirmation"

    def get_description(self) -> str:
        return "测试用哑工具"

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {"msg": {"type": "string"}}}

    def execute(self, **kwargs) -> str:
        return json.dumps({"echo": kwargs, "user_id": self._ctx.user_id}, ensure_ascii=False)


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class ToolConfirmationServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = rc.create_user("toolconf-user")
        cls.other_user = rc.create_user("toolconf-other")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()

    def _create_pending(self, msg="hi"):
        from service import tool_confirmation_service
        return tool_confirmation_service.create_pending(
            self.user["id"], None, "echo_test_tool_for_confirmation", {"msg": msg},
        )

    def test_confirm_actually_executes_the_tool(self):
        from service import tool_confirmation_service
        pending = self._create_pending("批准这条请假单")
        result = _run(tool_confirmation_service.confirm_and_execute_async(pending["token"], self.user["id"]))
        self.assertEqual(result["tool_name"], "echo_test_tool_for_confirmation")
        parsed = json.loads(result["result"])
        self.assertEqual(parsed["echo"], {"msg": "批准这条请假单"})
        self.assertEqual(parsed["user_id"], self.user["id"])

    def test_confirm_twice_second_call_conflicts(self):
        from service import tool_confirmation_service
        pending = self._create_pending()
        _run(tool_confirmation_service.confirm_and_execute_async(pending["token"], self.user["id"]))
        with self.assertRaises(tool_confirmation_service.ConfirmationError) as cm:
            _run(tool_confirmation_service.confirm_and_execute_async(pending["token"], self.user["id"]))
        self.assertEqual(cm.exception.status_code, 409)

    def test_wrong_user_cannot_confirm_or_reject(self):
        from service import tool_confirmation_service
        pending = self._create_pending()
        with self.assertRaises(tool_confirmation_service.ConfirmationError) as cm:
            _run(tool_confirmation_service.confirm_and_execute_async(pending["token"], self.other_user["id"]))
        self.assertEqual(cm.exception.status_code, 404)
        with self.assertRaises(tool_confirmation_service.ConfirmationError) as cm2:
            _run(tool_confirmation_service.reject_async(pending["token"], self.other_user["id"]))
        self.assertEqual(cm2.exception.status_code, 404)

    def test_reject_then_confirm_fails(self):
        from service import tool_confirmation_service
        pending = self._create_pending()
        _run(tool_confirmation_service.reject_async(pending["token"], self.user["id"]))
        with self.assertRaises(tool_confirmation_service.ConfirmationError) as cm:
            _run(tool_confirmation_service.confirm_and_execute_async(pending["token"], self.user["id"]))
        self.assertEqual(cm.exception.status_code, 409)

    def test_expired_token_cannot_be_confirmed(self):
        from service import tool_confirmation_service
        from models.init_db import SessionLocal, ToolConfirmation

        pending = self._create_pending()
        db = SessionLocal()
        try:
            row = db.query(ToolConfirmation).filter_by(token=pending["token"]).first()
            row.expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        finally:
            db.close()
        with self.assertRaises(tool_confirmation_service.ConfirmationError) as cm:
            _run(tool_confirmation_service.confirm_and_execute_async(pending["token"], self.user["id"]))
        self.assertEqual(cm.exception.status_code, 410)

    def test_concurrent_confirm_only_one_executes(self):
        """真实并发：同一条待确认单被两个请求同时点确认，只能有一个真的执行——
        跟这轮之前修的审批 decide / Java 幂等占位是同一类并发缺口，用真正的
        asyncio.gather 并发调用测，不是顺序调两次。"""
        from service import tool_confirmation_service

        async def _do():
            import asyncio
            pending = self._create_pending()

            async def _confirm():
                return await tool_confirmation_service.confirm_and_execute_async(
                    pending["token"], self.user["id"],
                )

            results = await asyncio.gather(_confirm(), _confirm(), return_exceptions=True)
            oks = [r for r in results if not isinstance(r, Exception)]
            errs = [r for r in results if isinstance(r, Exception)]
            self.assertEqual(len(oks), 1)
            self.assertEqual(len(errs), 1)
            self.assertIsInstance(errs[0], tool_confirmation_service.ConfirmationError)
            self.assertEqual(errs[0].status_code, 409)

        _run(_do())


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class ToolConfirmationRouteTest(unittest.TestCase):
    """证明 FasdtApi/chat.py 里新增的两个路由真的接上了 tool_confirmation_service，
    而不只是 service 层本身能跑。"""

    @classmethod
    def setUpClass(cls):
        cls.user = rc.create_user("toolconf-route-user")
        cls.other_user = rc.create_user("toolconf-route-other")
        cls.client = rc.make_client()

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()

    def _create_pending(self, msg="hi"):
        from service import tool_confirmation_service
        return tool_confirmation_service.create_pending(
            self.user["id"], None, "echo_test_tool_for_confirmation", {"msg": msg},
        )

    def test_confirm_endpoint_executes_and_returns_result(self):
        pending = self._create_pending("走接口确认")
        resp = self.client.post(
            f"/chat/tool-confirmations/{pending['token']}/confirm", headers=self.user["headers"],
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["tool_name"], "echo_test_tool_for_confirmation")
        self.assertEqual(json.loads(body["result"])["echo"], {"msg": "走接口确认"})

    def test_reject_endpoint_marks_rejected(self):
        pending = self._create_pending()
        resp = self.client.post(
            f"/chat/tool-confirmations/{pending['token']}/reject", headers=self.user["headers"],
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "rejected")

    def test_other_user_gets_404_on_confirm(self):
        pending = self._create_pending()
        resp = self.client.post(
            f"/chat/tool-confirmations/{pending['token']}/confirm", headers=self.other_user["headers"],
        )
        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated_request_rejected(self):
        pending = self._create_pending()
        resp = self.client.post(f"/chat/tool-confirmations/{pending['token']}/confirm")
        self.assertIn(resp.status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
