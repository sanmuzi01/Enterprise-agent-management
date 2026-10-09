"""IT 服务台 Agent 工具：员工侧权限与参数映射、IT 台工具仅限 IT 部门；能执行的动作都要用户确认，批准 / 改分类 / 发放设备没有工具。"""
import json
import unittest
from unittest.mock import patch

from service import enterprise_agent_templates as templates
from service.exceptions import PermissionDenied
from service.tools.base import ToolContext, ToolRegistry
from service.tools.it_service import (AddItTicketCommentTool, CancelItTicketTool, ConfirmItTicketResolvedTool, CreateItTicketTool,
                                      GetItDeskSummaryTool, GetItTicketDetailTool, GetItTicketStatusTool, GetMyDevicesTool,
                                      GetMyItTicketsTool, ListItDevicesTool, ListItQueueTool, ReopenItTicketTool,
                                      ResolveItTicketTool, SearchItSolutionsTool, TakeItTicketTool)

AUTH = {"team_id": 6, "is_org_admin": False, "is_team_admin": False}


def setUpModule():
    global _patch
    _patch = patch("service.tools.it_service.check_team_module")   # 部门类型校验另有专门测试
    _patch.start()


def tearDownModule():
    _patch.stop()


def tool(cls, user_id=4201):
    instance = cls()
    instance.set_context(ToolContext(user_id=user_id))
    return instance


@patch("service.tools.it_service.hub.resolve_caller_context", return_value=AUTH)
class EmployeeToolsTest(unittest.TestCase):
    def test_create_ticket_uses_write_scope_defaults_priority_and_idempotency_key(self, _auth):
        with patch("service.tools.it_service.hub.call", return_value={"id": 9, "status": "OPEN"}) as call:
            result = tool(CreateItTicketTool).execute(category="INCIDENT", title="打印机坏了", description="卡纸后脱机")
        args, kwargs = call.call_args
        self.assertEqual(args[:6], ("POST", "/it/tickets", 4201, 6, ["it.ticket.read", "it.ticket.write"], "create_it_ticket"))
        self.assertEqual(kwargs["json_body"], {"category": "INCIDENT", "priority": "NORMAL", "title": "打印机坏了", "description": "卡纸后脱机"})
        self.assertTrue(kwargs["idempotency_key"])
        self.assertEqual(json.loads(result)["id"], 9)

    def test_read_tools_have_no_idempotency_key(self, _auth):
        for cls, kwargs, path in ((GetMyItTicketsTool, {}, "/it/tickets/mine"), (GetItTicketStatusTool, {"ticket_id": 9}, "/it/tickets/9"),
                                  (GetMyDevicesTool, {}, "/it/devices/mine")):
            with patch("service.tools.it_service.hub.call", return_value=[]) as call:
                tool(cls).execute(**kwargs)
            self.assertEqual(call.call_args.args[1], path)
            self.assertIsNone(call.call_args.kwargs["idempotency_key"])

    def test_comment_tool_posts_public_comment(self, _auth):
        with patch("service.tools.it_service.hub.call", return_value={"id": 9}) as call:
            tool(AddItTicketCommentTool).execute(ticket_id=9, body="三楼东侧")
        self.assertEqual(call.call_args.kwargs["json_body"], {"body": "三楼东侧", "internal": False})
        self.assertTrue(call.call_args.kwargs["idempotency_key"])

    def test_search_solutions_encodes_text(self, _auth):
        with patch("service.tools.it_service.hub.call", side_effect=[{"category": "INCIDENT"}, [{"title": "打印机无法打印"}]]) as call:
            result = json.loads(tool(SearchItSolutionsTool).execute(text="打印机 无法打印"))
        self.assertIn("%E6%89%93", call.call_args_list[0].args[1])
        self.assertEqual(result["articles"][0]["title"], "打印机无法打印")
        self.assertIn("error", json.loads(tool(SearchItSolutionsTool).execute(text="")))

    def test_user_without_department_is_rejected(self, _auth):
        with patch("service.tools.it_service.hub.resolve_caller_context", return_value={**AUTH, "team_id": None}), \
                patch("service.tools.it_service.hub.call") as call:
            result = json.loads(tool(CreateItTicketTool).execute(category="OTHER", title="咨询", description="咨询内容"))
        self.assertIn("不属于任何部门", result["error"])
        call.assert_not_called()


@patch("service.tools.it_service.hub.resolve_caller_context", return_value=AUTH)
class DeskToolsTest(unittest.TestCase):
    def test_desk_tools_are_read_only_with_enterprise_scope(self, _auth):
        with patch("service.tools.it_service.department_staff_scope", return_value={"organization_id": 1, "scope": [6, 7]}):
            cases = ((ListItQueueTool, {"assignee": "unassigned", "overdue": True}, "/it/desk/tickets?scopeTeamIds=6,7&assignee=unassigned&overdue=true&limit=50"),
                     (GetItTicketDetailTool, {"ticket_id": 3}, "/it/desk/tickets/3?scopeTeamIds=6,7"),
                     (ListItDevicesTool, {"status": "IN_STOCK"}, "/it/desk/devices?scopeTeamIds=6,7&status=IN_STOCK"))
            for cls, kwargs, path in cases:
                with patch("service.tools.it_service.hub.call", return_value=[]) as call:
                    tool(cls).execute(**kwargs)
                args = call.call_args.args
                self.assertEqual((args[0], args[1], args[4]), ("GET", path, ["it.desk.read"]))
                self.assertIsNone(call.call_args.kwargs.get("json_body"))

    def test_summary_adds_narrative(self, _auth):
        summary = {"days": 30, "byStatus": {"OPEN": 1}, "effect": {"resolved": 0}, "unassigned": 1, "overdue": 0}
        with patch("service.tools.it_service.department_staff_scope", return_value={"organization_id": 1, "scope": [6]}), \
                patch("service.tools.it_service.hub.call", return_value=summary):
            data = json.loads(tool(GetItDeskSummaryTool).execute())
        self.assertIn("处理中的工单 1 张", data["narrative"])

    def test_non_it_user_cannot_use_desk_tools_and_nothing_is_called(self, _auth):
        with patch("service.tools.it_service.department_staff_scope", side_effect=PermissionDenied("IT 服务台仅对IT部门开放")), \
                patch("service.tools.it_service.hub.call") as call:
            for cls, kwargs in ((ListItQueueTool, {}), (GetItTicketDetailTool, {"ticket_id": 1}), (GetItDeskSummaryTool, {}),
                                (ListItDevicesTool, {})):
                self.assertIn("仅对IT部门开放", json.loads(tool(cls).execute(**kwargs))["error"], cls.__name__)
        call.assert_not_called()


@patch("service.tools.it_service.hub.resolve_caller_context", return_value=AUTH)
class ActionToolsTest(unittest.TestCase):
    def test_employee_actions_post_to_own_ticket_with_write_scope(self, _auth):
        for cls, kwargs, action, body in ((ConfirmItTicketResolvedTool, {"ticket_id": 9}, "confirm", None),
                                          (ReopenItTicketTool, {"ticket_id": 9, "reason": "还是脱机"}, "reopen", {"reason": "还是脱机"}),
                                          (CancelItTicketTool, {"ticket_id": 9}, "cancel", None)):
            with patch("service.tools.it_service.hub.call", return_value={"id": 9, "status": "CLOSED"}) as call:
                tool(cls).execute(**kwargs)
            args, kw = call.call_args
            self.assertEqual(args[:5], ("POST", f"/it/tickets/9/{action}", 4201, 6, ["it.ticket.read", "it.ticket.write"]))
            self.assertEqual(kw["json_body"], body)
            self.assertTrue(kw["idempotency_key"])

    def test_take_assigns_to_myself_only_for_it_staff(self, _auth):
        scope = {"organization_id": 1, "scope": [6, 7]}
        with patch("service.tools.it_service.department_staff_scope", return_value=scope), \
                patch("service.tools.it_service._is_it_staff_sync", return_value=True), \
                patch("service.tools.it_service.hub.call", return_value={"id": 3}) as call:
            tool(TakeItTicketTool).execute(ticket_id=3)
        args, kw = call.call_args
        self.assertEqual((args[0], args[1], args[4]), ("POST", "/it/desk/tickets/3/assign?scopeTeamIds=6,7", ["it.desk.read", "it.desk.write"]))
        self.assertEqual(kw["json_body"], {"assigneeUserId": 4201})
        with patch("service.tools.it_service.department_staff_scope", return_value=scope), \
                patch("service.tools.it_service._is_it_staff_sync", return_value=False), \
                patch("service.tools.it_service.hub.call") as call:
            self.assertIn("IT 部门", json.loads(tool(TakeItTicketTool).execute(ticket_id=3))["error"])
        call.assert_not_called()

    def test_resolve_sends_resolution_and_non_it_user_is_rejected(self, _auth):
        with patch("service.tools.it_service.department_staff_scope", return_value={"organization_id": 1, "scope": [6]}), \
                patch("service.tools.it_service.hub.call", return_value={"id": 3}) as call:
            tool(ResolveItTicketTool).execute(ticket_id=3, resolution="更换硒鼓")
        self.assertEqual(call.call_args.kwargs["json_body"], {"resolution": "更换硒鼓"})
        with patch("service.tools.it_service.department_staff_scope", side_effect=PermissionDenied("IT 服务台仅对IT部门开放")), \
                patch("service.tools.it_service.hub.call") as call:
            self.assertIn("仅对IT部门开放", json.loads(tool(ResolveItTicketTool).execute(ticket_id=3, resolution="x"))["error"])
        call.assert_not_called()


class ToolSurfaceTest(unittest.TestCase):
    # 只能在工作台里由人操作，没有任何工具
    FORBIDDEN = ("approve_it", "reject_it", "reclassify", "retire", "assign_device", "close_it")
    # 可以由助手提出、但必须用户在对话里点确认才执行
    CONFIRMED = ("take_it_ticket", "resolve_it_ticket", "confirm_it_ticket_resolved", "reopen_it_ticket", "cancel_it_ticket")

    def test_desk_decisions_have_no_tool_and_actions_need_confirmation(self):
        names = set(ToolRegistry.list_all())
        self.assertIn("list_it_queue", names)
        for name in names:
            if "it_" in name or "device" in name or "ticket" in name:
                for word in self.FORBIDDEN:
                    self.assertNotIn(word, name, name)
        for name in self.CONFIRMED:
            self.assertEqual(ToolRegistry.get(name).risk_level, "high_risk", name)

    def test_templates_expose_the_right_tools(self):
        it_tools = set(templates.get_template("it")["tools"])
        self.assertTrue({"search_it_solutions", "create_it_ticket", "list_it_queue", "list_it_devices", "take_it_ticket", "resolve_it_ticket"} <= it_tools)
        office = set(templates.get_template("office")["tools"])
        self.assertTrue({"create_it_ticket", "get_my_it_tickets", "search_it_solutions"} <= office)
        self.assertFalse({"list_it_queue", "get_it_desk_summary", "take_it_ticket", "resolve_it_ticket"} & office)     # IT 台工具只给 IT 部门模板
        self.assertTrue({"confirm_it_ticket_resolved", "reopen_it_ticket", "cancel_it_ticket"} <= office)
        for name in it_tools:
            self.assertIsNotNone(ToolRegistry.get(name), name)
        self.assertIn("故障", templates.get_template("it")["routing_keywords"])


if __name__ == "__main__":
    unittest.main()
