"""提示注入与 Agent 越权调用工具专项。

威胁模型：文档、网页、接口返回、对话历史里的“指令”可以让模型输出任何文字、调用任何它看得见的工具、给出任何参数。
所以安全边界不能靠“相信模型”，必须由代码保证：
1. 身份来自服务端上下文，不来自模型给的参数——没有任何工具暴露 user_id / team_id 之类的参数，适配层也会丢掉未声明的参数；
2. 高风险工具（审批、提交、验收……）模型永远执行不了，只能生成待确认单，由用户在界面上点确认；
3. 工具清单有快照：新增工具必须登记风险等级，逼着人评审“这个工具被注入指令诱导调用，最坏会怎样”；
4. 声明为只读的工具不能发起写请求；
5. 模型调用了没绑定给它的工具，只会得到错误，不会被执行。
"""
import json
import unittest
from unittest.mock import patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

import service.tools  # noqa: F401  触发工具自动注册
from service.tools.base import BaseTool, ToolContext, ToolRegistry
from service.tools.langchain_adapter import adapt_tool

_AVAILABLE, _WHY = rc.route_tests_available()

IDENTITY_FIELDS = {"user_id", "team_id", "organization_id", "org_id", "acting_user_id", "operator_id", "actor_id", "is_admin", "is_org_admin", "is_team_admin",
                   "role", "roles", "scope", "scopes", "token", "api_key", "password", "secret", "as_user", "impersonate", "tenant_id", "agent_id"}

# 工具风险快照：name → risk_level。新增 / 改动工具必须来这里登记，并回答“被提示注入诱导调用时最坏会怎样”。
#  read      只读，没有副作用（run_skill_script 在隔离沙箱里运行已审核的脚本）
#  write     产生草稿 / 评论 / 进度记录，不改变他人的权益、不花钱、不对外发送
#  high_risk 审批、提交、验收、改商机……模型只能生成待确认单，由用户点确认才执行
RISK_SNAPSHOT = {
    "accept_responsibility": "high_risk", "add_it_ticket_comment": "write", "approve_expense_claim": "high_risk", "approve_leave_request": "high_risk",
    "approve_purchase_request": "high_risk", "calculator": "read", "chart_generator": "read", "create_expense_draft": "write", "create_followup_draft": "write",
    "create_it_ticket": "write", "create_leave_draft": "write", "create_or_update_opportunity": "high_risk", "create_purchase_draft": "write",
    "datetime_calculator": "read", "extract_responsibility_plan": "write", "generate_voucher_draft": "write", "get_attendance_summary": "read",
    "get_customer_summary": "read", "get_department_budget": "read", "get_department_responsibility_risks": "read", "get_expense_budget": "read",
    "get_expense_status": "read", "get_hr_case": "read", "get_hr_summary": "read", "get_inventory_status": "read", "get_it_desk_summary": "read",
    "get_it_ticket_detail": "read", "get_it_ticket_status": "read", "get_leave_balance": "read", "get_leave_status": "read",
    "get_my_attendance_anomalies": "read", "get_my_devices": "read", "get_my_expense_claims": "read", "get_my_hr_tasks": "read", "get_my_it_tickets": "read",
    "get_my_leave_requests": "read", "get_my_purchase_requests": "read", "get_opportunities": "read", "get_purchase_status": "read",
    "get_responsibility_detail": "read", "get_responsibility_weekly_summary": "read", "get_team_pending_expense_claims": "read",
    "get_team_pending_leave_requests": "read", "get_team_pending_purchase_requests": "read", "get_voucher_detail": "read", "get_voucher_monthly_summary": "read",
    "list_hr_cases": "read", "list_it_devices": "read", "list_it_queue": "read", "list_my_responsibilities": "read", "list_pending_acceptance": "read",
    "list_pending_verification": "read", "list_pending_vouchers": "read", "list_team_customers": "read", "outline_generator": "read", "precheck_hr_case": "read",
    "raise_responsibility_objection": "write", "reject_expense_claim": "high_risk", "reject_leave_request": "high_risk", "reject_purchase_request": "high_risk",
    "report_responsibility_blocker": "write", "report_responsibility_progress": "write", "request_rework": "high_risk", "run_skill_script": "read",
    "search_it_solutions": "read", "submit_customer_followup": "high_risk", "submit_deliverable": "high_risk", "submit_expense_claim": "high_risk",
    "submit_leave_request": "high_risk", "submit_purchase_request": "high_risk", "unit_converter": "read", "verify_deliverable": "high_risk", "word_count": "read",
    # 2026-10 新增的可执行动作，都是 high_risk（被诱导调用时最坏只是生成一张待确认单，用户不点确认就不执行）：
    # 员工侧——只能动自己的工单（服务端按提交人校验）：确认已解决会关单、重开 / 撤销改变处理流程；
    # IT 台侧——只对 IT 部门有效成员开放：接单只能指派给自己，标记已解决会通知提交人确认（提交人仍可重开）；
    # 人事——发起事项只给人事部门成员，发起后还要部门负责人批准；完成办理任务只能完成分给自己的任务。
    "cancel_it_ticket": "high_risk", "complete_hr_task": "high_risk", "confirm_it_ticket_resolved": "high_risk",
    "create_hr_case": "high_risk", "reopen_it_ticket": "high_risk", "resolve_it_ticket": "high_risk", "take_it_ticket": "high_risk",
}
WRITE_VERBS = ('"POST"', '"PUT"', '"PATCH"', '"DELETE"', "'POST'", "'PUT'", "'PATCH'", "'DELETE'")
# 声明为 read 但用 POST 发请求的工具：必须写明为什么没有副作用
READ_TOOLS_USING_POST = {
    "precheck_hr_case": "POST /hr/cases/precheck 把待检查的事项放在请求体里，业务系统只做规则检查，不创建任何数据（有 Java 测试覆盖）",
}


def production_tools():
    """只看生产代码里的工具：别的测试会往全局注册表里临时注册测试用工具，不属于被审查的清单。"""
    return [tool for tool in ToolRegistry.get_all_tools() if type(tool).__module__.startswith("service.tools")]


def sample_arguments(tool) -> dict:
    """按工具声明的参数模式造一组最小合法参数。"""
    schema = tool.parameters or {}
    values = {}
    for name, prop in (schema.get("properties") or {}).items():
        kind = prop.get("type")
        values[name] = 1 if kind == "integer" else 1.0 if kind == "number" else True if kind == "boolean" else [] if kind == "array" else {} if kind == "object" else "x"
    return values


class ToolSurfaceTest(unittest.TestCase):
    def test_no_tool_exposes_identity_or_credential_parameters(self):
        for tool in production_tools():
            exposed = set((tool.parameters or {}).get("properties", {})) & IDENTITY_FIELDS
            with self.subTest(tool=tool.name):
                self.assertEqual(exposed, set(), f"{tool.name} 暴露了身份/凭证参数：身份必须来自服务端上下文，不能由模型传入")

    def test_risk_snapshot_matches_the_registry(self):
        actual = {tool.name: tool.risk_level for tool in production_tools()}
        added = sorted(set(actual) - set(RISK_SNAPSHOT))
        removed = sorted(set(RISK_SNAPSHOT) - set(actual))
        changed = {n: (RISK_SNAPSHOT[n], actual[n]) for n in actual if n in RISK_SNAPSHOT and RISK_SNAPSHOT[n] != actual[n]}
        self.assertEqual((added, removed, changed), ([], [], {}),
                         "工具清单变了：请在 RISK_SNAPSHOT 里登记（并评审：被提示注入诱导调用时最坏会怎样？能不能降低风险等级？）")

    def test_read_only_tools_never_issue_write_requests(self):
        import inspect
        for tool in production_tools():
            if tool.risk_level != "read" or tool.name == "run_skill_script" or tool.name in READ_TOOLS_USING_POST:
                continue
            source = inspect.getsource(type(tool))
            with self.subTest(tool=tool.name):
                self.assertFalse([verb for verb in WRITE_VERBS if verb in source], f"{tool.name} 声明为 read，但代码里有写请求")

    def test_every_non_read_tool_is_justified_by_a_reviewed_category(self):
        """写类工具只允许生成草稿 / 记录；改变别人权益或花钱的一律 high_risk。名字里带这些动词的写工具必须是 high_risk。"""
        decisive = ("approve", "reject", "submit", "verify", "accept", "rework", "pay", "delete", "cancel", "publish", "assign", "transfer")
        for tool in production_tools():
            if tool.name.split("_")[0] in decisive:           # 以动词开头才算（list_pending_acceptance 只是列出待接受的，不是接受）
                with self.subTest(tool=tool.name):
                    self.assertEqual(tool.risk_level, "high_risk", f"{tool.name} 看起来是决定性操作，却不是 high_risk")


class AdapterTest(unittest.TestCase):
    def make(self, risk):
        calls = []

        class Dummy(BaseTool):
            requires_context = True

            def get_name(self):
                return f"dummy_{risk}"

            def get_description(self):
                return "测试工具"

            def get_parameters(self):
                return {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}

            def execute(self, **kwargs):
                calls.append(kwargs)
                return "executed"
        Dummy.risk_level = risk
        return adapt_tool(Dummy(), ToolContext(user_id=7, agent_id=3)), calls

    def test_undeclared_arguments_injected_by_the_model_are_dropped(self):
        tool, calls = self.make("read")
        tool.invoke({"q": "hello", "user_id": 999, "team_id": 999, "is_admin": True, "organization_id": 1})
        self.assertEqual(calls, [{"q": "hello"}])

    def test_high_risk_tool_stores_only_declared_arguments_for_the_confirmation(self):
        tool, calls = self.make("high_risk")
        with patch("service.tool_confirmation_service.create_pending", return_value={"token": "t", "expires_at": "x"}) as pending:
            result = json.loads(tool.invoke({"q": "pay", "user_id": 999, "is_admin": True}))
        self.assertEqual(calls, [])                                          # 没有执行
        self.assertEqual(result["status"], "confirmation_required")
        self.assertEqual(pending.call_args.kwargs["tool_args"], {"q": "pay"})   # 用户看到并确认的参数里没有注入的身份字段
        self.assertEqual(pending.call_args.kwargs["user_id"], 7)                # 确认单归属服务端上下文里的用户


class HighRiskToolsTest(unittest.TestCase):
    def test_every_high_risk_tool_defers_to_a_confirmation_and_never_executes(self):
        high = [tool for tool in production_tools() if tool.risk_level == "high_risk"]
        self.assertGreaterEqual(len(high), 15)
        for tool in high:
            with self.subTest(tool=tool.name), patch.object(type(tool), "execute", side_effect=AssertionError("高风险工具被模型直接执行了")), \
                    patch("service.tool_confirmation_service.create_pending", return_value={"token": "t", "expires_at": "x"}) as pending:
                adapted = adapt_tool(tool, ToolContext(user_id=7, agent_id=3))
                result = json.loads(adapted.invoke(sample_arguments(tool)))
                self.assertEqual(result["status"], "confirmation_required")
                self.assertEqual(pending.call_count, 1)

    def test_a_missing_user_context_cannot_create_a_confirmation(self):
        tool = ToolRegistry.get("approve_leave_request")()
        adapted = adapt_tool(tool, ToolContext(user_id=0, agent_id=None))
        result = json.loads(adapted.invoke(sample_arguments(tool)))
        self.assertIn("error", result)


class UnboundToolTest(unittest.TestCase):
    def test_calling_a_tool_that_was_not_bound_to_the_agent_does_nothing(self):
        """模型凭空“调用”一个没绑定给它的特权工具（比如被文档里的指令诱导）：ToolNode 里没有它，只会得到错误。"""
        from langchain_core.messages import AIMessage
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.prebuilt import ToolNode
        graph = StateGraph(MessagesState)
        graph.add_node("tools", ToolNode([adapt_tool(ToolRegistry.get("calculator")())], handle_tool_errors=True))
        graph.add_edge(START, "tools")
        graph.add_edge("tools", END)
        app = graph.compile()
        with patch.object(ToolRegistry.get("approve_leave_request"), "execute", side_effect=AssertionError("未绑定的工具被执行了")):
            message = AIMessage(content="", tool_calls=[{"name": "approve_leave_request", "args": {"request_id": 1}, "id": "call-1"}])
            result = app.invoke({"messages": [message]})
        reply = result["messages"][-1]
        self.assertEqual(reply.status, "error")
        self.assertIn("not a valid tool", reply.content)


@unittest.skipUnless(_AVAILABLE, _WHY)
class ConfirmationRecordTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = rc.create_user("sat-u")

    @classmethod
    def tearDownClass(cls):
        from sqlalchemy import text
        from models.init_db import SessionLocal
        db = SessionLocal()
        db.execute(text("DELETE FROM tool_confirmation WHERE user_id=:u"), {"u": cls.user["id"]})
        db.commit()
        db.close()
        rc.cleanup()

    def test_what_the_user_is_asked_to_confirm_is_exactly_what_gets_stored(self):
        from sqlalchemy import text
        from models.init_db import SessionLocal
        tool = ToolRegistry.get("approve_leave_request")()
        adapted = adapt_tool(tool, ToolContext(user_id=self.user["id"], agent_id=None))
        shown = json.loads(adapted.invoke({"request_id": 5, "note": "同意", "user_id": 1, "team_id": 9}))
        db = SessionLocal()
        try:
            row = db.execute(text("SELECT user_id, tool_name, tool_args, status FROM tool_confirmation WHERE token=:t"), {"t": shown["confirmation_token"]}).first()
        finally:
            db.close()
        self.assertEqual((row.user_id, row.tool_name, row.status), (self.user["id"], "approve_leave_request", "pending"))
        self.assertEqual(json.loads(row.tool_args), shown["tool_args"])
        self.assertNotIn("user_id", json.loads(row.tool_args))
        self.assertNotIn("team_id", json.loads(row.tool_args))


if __name__ == "__main__":
    unittest.main()
