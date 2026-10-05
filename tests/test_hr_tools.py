"""人事入转调离 Agent 工具：都是只读、身份与工作台一致、没有能做决定的工具。"""
import json
import unittest
from unittest.mock import patch

from service import enterprise_agent_templates as templates
from service.exceptions import PermissionDenied
from service.tools.base import ToolContext, ToolRegistry
from service.tools.hr_cases import GetHrCaseTool, GetHrSummaryTool, GetMyHrTasksTool, ListHrCasesTool, PrecheckHrCaseTool

AUTH = {"team_id": 6, "is_org_admin": False, "is_team_admin": False}
ACTOR = {"organization_id": 1, "scope": [6, 7], "roles": ["HR"], "heads": [], "org_admin": False}


def tool(cls):
    instance = cls()
    instance.set_context(ToolContext(user_id=4301))
    return instance


@patch("service.tools.hr_cases.hub.resolve_caller_context", return_value=AUTH)
@patch("service.tools.hr_cases.hr.actor_for_sync", return_value=ACTOR)
class HrToolsTest(unittest.TestCase):
    def test_read_tools_sign_actor_into_path(self, _actor, _auth):
        cases = ((GetMyHrTasksTool, {}, "/hr/cases/my-tasks?scopeTeamIds=6,7&roles=HR"),
                 (GetHrCaseTool, {"case_id": 3}, "/hr/cases/3?scopeTeamIds=6,7&roles=HR"),
                 (ListHrCasesTool, {"view": "approval"}, "/hr/cases?scopeTeamIds=6,7&roles=HR&view=approval&limit=50"))
        for cls, kwargs, path in cases:
            with patch("service.tools.hr_cases.hub.call", return_value=[]) as call:
                tool(cls).execute(**kwargs)
            args = call.call_args.args
            self.assertEqual((args[0], args[1], args[4]), ("GET", path, ["hr.case.read"]))

    def test_precheck_computes_head_flag_and_only_reads(self, _actor, _auth):
        with patch("models.enterprise_dao.is_team_admin_of_team", return_value=True), \
                patch("service.tools.hr_cases.hub.call", return_value={"checks": [], "canSubmit": True}) as call:
            tool(PrecheckHrCaseTool).execute(case_type="OFFBOARDING", employee_user_id=9, employee_team_id=7, effective_date="2026-11-01")
        self.assertEqual(call.call_args.kwargs["json_body"]["employeeIsHead"], True)
        self.assertEqual(call.call_args.args[4], ["hr.case.read"])

    def test_summary_adds_narrative(self, _actor, _auth):
        with patch("service.tools.hr_cases.hub.call", return_value={"byType": {}, "overdueTasks": 2, "effectPending": 0}):
            data = json.loads(tool(GetHrSummaryTool).execute())
        self.assertIn("2 项办理任务已过期限", data["narrative"])

    def test_invalid_identity_is_reported_without_calling(self, _actor, _auth):
        _actor.side_effect = PermissionDenied("不属于该部门")
        with patch("service.tools.hr_cases.hub.call") as call:
            self.assertIn("不属于该部门", json.loads(tool(GetMyHrTasksTool).execute())["error"])
        call.assert_not_called()


class SurfaceTest(unittest.TestCase):
    def test_no_decision_tools(self):
        names = [n for n in ToolRegistry.list_all() if "hr" in n]
        self.assertTrue(names)
        for name in names:
            for word in ("approve", "reject", "complete", "create_hr", "apply", "cancel", "finish"):
                self.assertNotIn(word, name, name)

    def test_templates(self):
        oa = set(templates.get_template("oa")["tools"])
        self.assertTrue({"precheck_hr_case", "get_hr_summary", "get_my_hr_tasks"} <= oa)
        office = set(templates.get_template("office")["tools"])
        self.assertIn("get_my_hr_tasks", office)
        self.assertNotIn("precheck_hr_case", office)


if __name__ == "__main__":
    unittest.main()
