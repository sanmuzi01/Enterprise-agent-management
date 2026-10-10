"""责任执行 Agent 工具：能起草、能查询、员工与验收人的决定要人确认、没有任何能指派/改责任人/改期限/取消的工具。"""
import json
import unittest
from unittest.mock import patch

from service import enterprise_agent_templates as templates
from service import enterprise_hub_client as hub
from service.exceptions import PermissionDenied
from service.tools.base import ToolContext, ToolRegistry
from service.tools import responsibility as tools

AUTH = {"team_id": 6, "is_org_admin": False, "is_team_admin": False}
ACTOR = {"organization_id": 1, "scope": [6, 7], "members": [6], "heads": [], "org_admin": False}
MEMBERS = [{"user_id": 11, "name": "zhang3", "is_head": False}, {"user_id": 12, "name": "li4", "is_head": False},
           {"user_id": 13, "name": "wang5", "is_head": True}]
PEOPLE = {"members": MEMBERS, "reviewers": MEMBERS + [{"user_id": 99, "name": "boss"}],
          "organization_id": 1, "actor": ACTOR, "eligible": {"memberIds": [11, 12, 13], "reviewerIds": [11, 12, 13, 99]}}
SOURCE = "例会纪要：zhang3负责新版首页联调，下周五前提交可部署的前端构建包，验收标准是回归通过，由boss验收。li4协助。"
TASK = {"id": 5, "seq": 1, "title": "联调", "status": "IN_PROGRESS", "statusLabel": "执行中", "planId": 1, "myActions": []}


def make(cls, user_id=4301):
    instance = cls()
    instance.set_context(ToolContext(user_id=user_id))
    return instance


def patched(fn):
    for target, value in (("service.tools.responsibility.hub.resolve_caller_context", AUTH),
                          ("service.tools.responsibility.rs.actor_for_sync", ACTOR)):
        fn = patch(target, return_value=value)(fn)
    return patch("service.tools.responsibility.rs.enrich_sync", side_effect=lambda x: x)(fn)


class SurfaceTest(unittest.TestCase):
    def test_risk_levels_follow_who_decides(self):
        high = {"accept_responsibility", "submit_deliverable", "verify_deliverable", "request_rework"}
        write = {"extract_responsibility_plan", "raise_responsibility_objection", "report_responsibility_progress", "report_responsibility_blocker"}
        read = {"list_my_responsibilities", "get_responsibility_detail", "list_pending_acceptance", "list_pending_verification",
                "get_department_responsibility_risks", "get_responsibility_weekly_summary"}
        for names, level in ((high, "high_risk"), (write, "write"), (read, "read")):
            for name in names:
                with self.subTest(name=name):
                    self.assertEqual(ToolRegistry.get(name)().risk_level, level)

    def test_no_tool_can_publish_reassign_reschedule_or_cancel(self):
        names = [n for n in ToolRegistry.list_all() if any(w in n for w in ("responsib", "deliverable", "rework", "acceptance", "verification"))]
        self.assertGreaterEqual(len(names), 14)
        for name in names:
            for word in ("publish", "assign", "revise", "reassign", "cancel", "extension", "transfer", "close", "force", "delete"):
                self.assertNotIn(word, name, name)

    def test_high_risk_tools_only_create_a_confirmation_instead_of_executing(self):
        from service.tools.langchain_adapter import adapt_tool
        for cls, args in ((tools.AcceptResponsibilityTool, {"task_id": 5}), (tools.VerifyDeliverableTool, {"task_id": 5}),
                          (tools.SubmitDeliverableTool, {"task_id": 5, "summary": "做好了"}),
                          (tools.RequestReworkTool, {"task_id": 5, "reason": "没覆盖登录页"})):
            with self.subTest(tool=cls.__name__), \
                    patch("service.tool_confirmation_service.create_pending", return_value={"token": "t1", "expires_at": "x"}), \
                    patch.object(hub, "call") as call:
                lc = adapt_tool(cls(), ToolContext(user_id=4301))
                result = json.loads(lc.func(**args))
            self.assertEqual(result["status"], "confirmation_required")
            call.assert_not_called()

    def test_templates(self):
        office = set(templates.get_template("office")["tools"])
        self.assertTrue({"extract_responsibility_plan", "list_pending_acceptance", "get_department_responsibility_risks",
                         "get_responsibility_weekly_summary", "accept_responsibility", "verify_deliverable"} <= office)
        for code in ("oa", "procurement", "crm", "finance", "it"):
            tool_set = set(templates.get_template(code)["tools"])
            self.assertTrue({"list_my_responsibilities", "accept_responsibility", "submit_deliverable", "verify_deliverable"} <= tool_set, code)
            self.assertNotIn("extract_responsibility_plan", tool_set, code)   # 标杆能力先放在部门办公助手
        task = templates.get_template("office")["task"]
        for phrase in ("没有通知任何人", "不能替负责人指派", "不评价员工态度"):
            self.assertIn(phrase, task)


@patched
class ReadToolsTest(unittest.TestCase):
    def test_reads_sign_the_computed_actor_into_the_path(self, *_):
        cases = ((tools.ListMyResponsibilitiesTool, {}, "view=mine&limit=50"),
                 (tools.ListPendingAcceptanceTool, {}, "view=team&status=PENDING_ACCEPT,NEGOTIATING&limit=100"),
                 (tools.ListPendingVerificationTool, {}, "view=review&status=PENDING_REVIEW&limit=100"),
                 (tools.GetResponsibilityDetailTool, {"task_id": 5}, ""))
        for cls, kwargs, tail in cases:
            with self.subTest(tool=cls.__name__), patch.object(hub, "call", return_value=[] if cls is not tools.GetResponsibilityDetailTool
                                                              else {"task": dict(TASK), "events": [], "deliverables": []}) as call:
                make(cls).execute(**kwargs)
            args = call.call_args.args
            self.assertEqual(args[0], "GET")
            self.assertIn("scopeTeamIds=6,7", args[1])
            self.assertIn("memberTeamIds=6", args[1])
            self.assertTrue(args[1].endswith(tail), args[1])
            self.assertEqual(args[4], ["responsibility.read"])
            self.assertIsNone(call.call_args.kwargs["idempotency_key"])

    def test_list_returns_brief_rows(self, *_):
        rows = [{**TASK, "title": "联调", "internalField": "不该出现", "responsibleName": "zhang3", "dueDate": "2026-10-20"}]
        with patch.object(hub, "call", return_value=rows):
            data = json.loads(make(tools.ListMyResponsibilitiesTool).execute(view="collab"))
        self.assertEqual(data[0]["title"], "联调")
        self.assertNotIn("internalField", data[0])

    def test_business_system_rejection_is_reported_as_text(self, *_):
        with patch.object(hub, "call", side_effect=hub.EnterpriseHubError(404, "责任事项不存在")):
            data = json.loads(make(tools.GetResponsibilityDetailTool).execute(task_id=9))
        self.assertEqual(data["error"], "责任事项不存在")

    def test_invalid_identity_stops_before_calling(self, *_):
        with patch("service.tools.responsibility.rs.actor_for_sync", side_effect=PermissionDenied("不属于该部门")), \
                patch.object(hub, "call") as call:
            self.assertIn("不属于该部门", json.loads(make(tools.ListMyResponsibilitiesTool).execute())["error"])
        call.assert_not_called()

    def test_no_user_context(self, *_):
        bare = tools.ListMyResponsibilitiesTool()
        bare.set_context(ToolContext())
        self.assertIn("缺少用户上下文", json.loads(bare.execute())["error"])

    def test_summary_tools_add_names_and_narrative_without_judging(self, *_):
        summary = {"byStatus": {"IN_PROGRESS": 2}, "week": {"from": "2026-10-05", "to": "2026-10-11", "due": 2, "done": 1, "doneOnTime": 1, "rate": 50.0},
                   "metrics": {"firstPassRate": 100.0}, "overdueCount": 0, "overdue": [], "waitingAccept": [], "blocked": [],
                   "blockedOver2Days": 0, "pendingReview": [], "load": [{"userId": 11, "open": 3, "highPriority": 3, "overloaded": True}], "draftTasks": 0}
        with patch.object(hub, "call", return_value=json.loads(json.dumps(summary))), \
                patch("service.tools.responsibility.rs._names_sync", return_value={11: "zhang3"}):
            weekly = json.loads(make(tools.GetResponsibilityWeeklySummaryTool).execute())
            risks = json.loads(make(tools.GetDepartmentResponsibilityRisksTool).execute())
        self.assertIn("本周（2026-10-05 至 2026-10-11）到期的责任 2 项", weekly["narrative"])
        self.assertEqual(risks["load"][0]["name"], "zhang3")
        self.assertNotIn("绩效", json.dumps(weekly, ensure_ascii=False))


@patched
class ActionToolsTest(unittest.TestCase):
    def post(self, cls, **kwargs):
        with patch.object(hub, "call", return_value={"task": dict(TASK), "events": [], "deliverables": []}) as call:
            out = json.loads(make(cls).execute(**kwargs))
        return call, out

    def test_accept_posts_with_write_scope_and_a_fresh_idempotency_key(self, *_):
        call, out = self.post(tools.AcceptResponsibilityTool, task_id=5)
        args = call.call_args.args
        self.assertEqual(args[0], "POST")
        self.assertTrue(args[1].startswith("/responsibility/tasks/5/accept?scopeTeamIds="))
        self.assertEqual(args[4], ["responsibility.read", "responsibility.write"])
        self.assertTrue(call.call_args.kwargs["idempotency_key"])
        self.assertTrue(out["ok"])
        self.assertIn("执行中", out["message"])

    def test_fields_are_forwarded_and_blank_ones_dropped(self, *_):
        cases = ((tools.RaiseResponsibilityObjectionTool, {"task_id": 5, "reason": "期限冲突"}, "object", {"reason": "期限冲突"}),
                 (tools.ReportResponsibilityProgressTool, {"task_id": 5, "note": "做了一半", "percent": 50}, "progress", {"note": "做了一半", "percent": 50}),
                 (tools.ReportResponsibilityBlockerTool, {"task_id": 5, "reason": "缺账号"}, "block", {"reason": "缺账号"}),
                 (tools.SubmitDeliverableTool, {"task_id": 5, "summary": "已上传", "link": ""}, "submit", {"summary": "已上传"}),
                 (tools.VerifyDeliverableTool, {"task_id": 5}, "verify", {}),
                 (tools.RequestReworkTool, {"task_id": 5, "reason": "没覆盖登录页"}, "rework", {"reason": "没覆盖登录页"}))
        for cls, kwargs, action, body in cases:
            with self.subTest(action=action):
                call, _out = self.post(cls, **kwargs)
                self.assertIn(f"/tasks/5/{action}?", call.call_args.args[1])
                self.assertEqual(call.call_args.kwargs["json_body"], body)

    def test_business_rules_are_enforced_by_the_business_system_and_reported(self, *_):
        with patch.object(hub, "call", side_effect=hub.EnterpriseHubError(403, "只有主责员工本人能接受责任")):
            data = json.loads(make(tools.AcceptResponsibilityTool).execute(task_id=5))
        self.assertIn("只有主责员工本人", data["error"])
        self.assertEqual(json.loads(make(tools.AcceptResponsibilityTool).execute())["error"], "缺少 task_id")


@patched
class ExtractToolTest(unittest.TestCase):
    def args(self, **over):
        task = {"title": "新版首页联调", "responsible_name": "zhang3", "collaborator_names": ["li4", "ghost9"], "reviewer_name": "boss",
                "due_text": "下周五前", "due_date": "2020-01-01", "deliverable": "可部署的前端构建包", "acceptance_criteria": "回归通过",
                "evidence": "zhang3负责新版首页联调，下周五前提交可部署的前端构建包"}
        base = {"title": "例会责任计划", "source_type": "MEETING", "source_text": SOURCE, "tasks": [task]}
        base.update(over)
        return base

    def run_tool(self, **over):
        with patch("service.tools.responsibility.rs.candidates_sync", return_value=PEOPLE), \
                patch("service.tools.responsibility.hub.call", return_value={"id": 8, "status": "DRAFT", "statusLabel": "草稿（待发布）", "unresolved": [],
                                                                            "tasks": [{"seq": 1, "title": "联调", "issues": []}]}) as call:
            out = json.loads(make(tools.ExtractResponsibilityPlanTool).execute(**self.args(**over)))
        return call, out

    def test_creates_a_draft_with_server_side_matching(self, *_):
        call, out = self.run_tool()
        self.assertEqual(out["planId"], 8)
        self.assertIn("没有通知任何人", out["note"])
        body = call.call_args.kwargs["json_body"]
        task = body["tasks"][0]
        self.assertEqual(task["responsibleUserId"], 11)
        self.assertEqual(task["reviewerUserId"], 99)
        self.assertEqual(task["collaboratorUserIds"], [12])           # ghost9 不在原文也不是员工
        self.assertNotEqual(task["dueDate"], "2020-01-01")            # 模型自己给的日期被丢弃，由期限说法换算
        self.assertRegex(task["dueDate"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(task["aiResponsibleUserId"], 11)
        self.assertEqual(body["eligible"], PEOPLE["eligible"])
        self.assertEqual(call.call_args.args[4], ["responsibility.read", "responsibility.write"])

    def test_same_input_uses_the_same_idempotency_key_so_repeated_calls_make_one_plan(self, *_):
        first, _ = self.run_tool()
        second, _ = self.run_tool()
        other, _ = self.run_tool(title="另一份计划")
        self.assertEqual(first.call_args.kwargs["idempotency_key"], second.call_args.kwargs["idempotency_key"])
        self.assertNotEqual(first.call_args.kwargs["idempotency_key"], other.call_args.kwargs["idempotency_key"])

    def test_fabricated_evidence_or_deadline_is_rejected_without_writing(self, *_):
        bad_evidence = self.args()
        bad_evidence["tasks"][0]["evidence"] = "zhang3 承诺本周完成全部联调"
        bad_due = self.args()
        bad_due["tasks"][0]["due_text"] = "后天上午"
        for over in (bad_evidence, bad_due):
            with patch("service.tools.responsibility.rs.candidates_sync", return_value=PEOPLE), patch("service.tools.responsibility.hub.call") as call:
                out = json.loads(make(tools.ExtractResponsibilityPlanTool).execute(**over))
            self.assertIn("error", out)
            call.assert_not_called()

    def test_missing_source_or_no_tasks(self, *_):
        for over, text in (({"source_text": "太短"}, "缺少原文"), ({"tasks": []}, "没有可以生成")):
            with patch("service.tools.responsibility.rs.candidates_sync", return_value=PEOPLE), patch("service.tools.responsibility.hub.call") as call:
                out = json.loads(make(tools.ExtractResponsibilityPlanTool).execute(**self.args(**over)))
            self.assertIn(text, out["error"])
            call.assert_not_called()

    def test_gaps_are_reported_back_so_the_agent_can_tell_the_user(self, *_):
        plan = {"id": 8, "status": "DRAFT", "statusLabel": "草稿（待发布）", "unresolved": ["没说验收人"],
                "tasks": [{"seq": 1, "title": "联调", "issues": [{"code": "NO_REVIEWER", "level": "BLOCK", "message": "没有验收人"},
                                                                  {"code": "VAGUE_TITLE", "level": "WARN", "message": "空泛"}]}]}
        with patch("service.tools.responsibility.rs.candidates_sync", return_value=PEOPLE), patch("service.tools.responsibility.hub.call", return_value=plan):
            out = json.loads(make(tools.ExtractResponsibilityPlanTool).execute(**self.args()))
        self.assertEqual(out["needSupplement"], [{"seq": 1, "title": "联调", "problems": ["没有验收人"]}])
        self.assertEqual(out["unresolved"], ["没说验收人"])


if __name__ == "__main__":
    unittest.main()
