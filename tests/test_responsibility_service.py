"""部门责任执行的 FastAPI 层：身份（scope/member/head）怎么算、可被指派的名单怎么算、写进签名路径和请求体的是什么、
姓名与中文状态怎么补。Java 的状态机与权限规则由 ResponsibilityIntegrationTest 覆盖，这里把 Java 调用换成桩。"""
import json
import unittest
import uuid
from unittest.mock import patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import SessionLocal
from service import enterprise_hub_client as hub
from service import responsibility_service as rs
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


class NarrativeTest(unittest.TestCase):
    def test_week_narrative_states_facts_without_judging_people(self):
        body = rs.narrative_for({
            "week": {"from": "2026-10-05", "to": "2026-10-11", "due": 4, "done": 3, "doneOnTime": 2},
            "byStatus": {"PENDING_ACCEPT": 2, "IN_PROGRESS": 3, "BLOCKED": 1, "DONE": 3, "CANCELLED": 0},
            "overdueCount": 1, "waitingAccept": [{"hoursWaiting": 30}, {"hoursWaiting": 5}], "blockedOver2Days": 1,
            "load": [{"name": "张三", "overloaded": True}, {"name": "李四", "overloaded": False}],
            "metrics": {"firstPassRate": 66.7, "onTimeSubmitRate": 100.0, "acceptMedianHours": 3.5}})
        self.assertIn("到期的责任 4 项，已验收完成 3 项（其中按时 2 项）", body)
        self.assertIn("待员工接受 2 项、执行中 3 项、受阻 1 项", body)
        self.assertIn("已逾期 1 项", body)
        self.assertIn("最久已等 30 小时", body)
        self.assertIn("受阻超过两天", body)
        self.assertIn("张三", body)
        self.assertNotIn("李四", body)
        self.assertIn("首次验收通过率 66.7%", body)
        for judging in ("态度", "懒", "偷懒", "绩效差", "在线时长"):
            self.assertNotIn(judging, body)

    def test_empty_week(self):
        body = rs.narrative_for({"week": {"due": 0}, "byStatus": {}, "metrics": {}})
        self.assertIn("本周没有到期的责任", body)
        self.assertIn("当前进行中的责任：无", body)


class DecorationTest(unittest.TestCase):
    def test_changes_show_names_and_labels_not_raw_ids(self):
        names = {7: "张三", 8: "李四"}
        event = {"type": "REVISED", "actorUserId": 7, "note": "换人", "detail": json.dumps(
            {"changes": [{"field": "responsible", "from": "7", "to": "8"}, {"field": "dueDate", "from": "2026-10-10", "to": "2026-10-20"},
                         {"field": "collaborators", "from": [7], "to": [7, 8]}]})}
        rs._decorate(event, names)
        changes = event["detailData"]["changes"]
        self.assertEqual(event["typeLabel"], "变更责任")
        self.assertEqual(event["actorName"], "张三")
        self.assertEqual((changes[0]["fieldLabel"], changes[0]["fromText"], changes[0]["toText"]), ("主责员工", "张三", "李四"))
        self.assertEqual(changes[1]["toText"], "2026-10-20")
        self.assertEqual(changes[2]["toText"], "张三、李四")

    def test_task_status_and_people_labels(self):
        task = {"planId": 1, "seq": 1, "status": "PENDING_REVIEW", "priority": "URGENT", "responsibleUserId": 7, "reviewerUserId": 8,
                "collaboratorUserIds": [8, 99], "waitingOnUserId": None}
        rs._decorate(task, {7: "张三", 8: "李四"})
        self.assertEqual((task["statusLabel"], task["priorityLabel"]), ("待验收", "紧急"))
        self.assertEqual((task["responsibleName"], task["reviewerName"]), ("张三", "李四"))
        self.assertEqual(task["collaboratorNames"], ["李四", "用户 99"])

    def test_task_payload_maps_to_java_names(self):
        payload = rs.task_payload({"title": "联调", "responsible_user_id": 3, "collaborator_user_ids": [4], "reviewer_user_id": 5,
                                   "due_date": "2026-10-20", "deliverable": "构建包", "acceptance_criteria": "回归通过",
                                   "priority": "HIGH", "evidence": "原文", "depends_on_seq": [1], "ai_responsible_user_id": 3})
        self.assertEqual(payload["responsibleUserId"], 3)
        self.assertEqual(payload["dependsOnSeq"], [1])
        self.assertEqual(payload["aiResponsibleUserId"], 3)
        self.assertIsNone(rs.task_payload({"title": "x", "due_date": ""})["dueDate"])


@unittest.skipUnless(_AVAILABLE, _WHY)
class ResponsibilityAccessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.owner = rc.create_user("rsp-owner")
        cls.u = {n: rc.create_user(f"rsp-{n}") for n in ("head", "emp", "emp2", "gone", "it1", "admin", "out")}
        cls.org = _create_org(cls.db, "rsp-org-" + uuid.uuid4().hex[:6], cls.owner["id"])
        cls.other_org = _create_org(cls.db, "rsp-org2-" + uuid.uuid4().hex[:6], cls.u["out"]["id"])
        cls.t = {}
        for code in ("sales", "it"):
            cls.t[code] = _create_team(cls.db, cls.org, f"rsp-{code}", cls.owner["id"])
            cls.db.execute(text("UPDATE teams SET department_code=:c WHERE id=:t"), {"c": code, "t": cls.t[code]})
        cls.other_team = _create_team(cls.db, cls.other_org, "rsp-other", cls.u["out"]["id"])
        cls.db.commit()
        _add_team_member(cls.db, cls.t["sales"], cls.u["head"]["id"], "admin")
        for n in ("emp", "emp2", "gone", "admin"):
            _add_team_member(cls.db, cls.t["sales"], cls.u[n]["id"], "member")
        _add_team_member(cls.db, cls.t["it"], cls.u["it1"]["id"], "member")
        _add_team_member(cls.db, cls.t["it"], cls.u["emp2"]["id"], "member")
        _add_team_member(cls.db, cls.other_team, cls.u["out"]["id"], "member")
        for n in ("head", "emp", "emp2", "gone", "it1"):
            _add_org_member(cls.db, cls.org, cls.u[n]["id"], "member")
        _add_org_member(cls.db, cls.org, cls.u["admin"]["id"], "admin")
        _add_org_member(cls.db, cls.other_org, cls.u["out"]["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        ids = ",".join(str(t) for t in [*cls.t.values(), cls.other_team])
        cls.db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({ids})"))
        cls.db.execute(text(f"DELETE FROM teams WHERE id IN ({ids})"))
        for o in (cls.org, cls.other_org):
            cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": o})
            cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": o})
        cls.db.commit()
        cls.db.close()

    def setUp(self):
        self.db.commit()
        self.db.execute(text("UPDATE organization_members SET status='active' WHERE organization_id=:o"), {"o": self.org})
        self.db.execute(text("UPDATE team_members SET status='active' WHERE team_id IN (:a, :b)"), {"a": self.t["sales"], "b": self.t["it"]})
        self.db.commit()
        self.calls = []
        self.mine = {"pendingAccept": 2, "inProgress": 3, "overdue": 1, "dueSoon": 0, "blocked": 0, "pendingReview": 0,
                     "assignedNotAccepted": 0, "isHead": False}
        self.task = {"id": 5, "planId": 2, "seq": 1, "teamId": self.t["sales"], "status": "IN_PROGRESS", "priority": "NORMAL",
                     "responsibleUserId": self.u["emp"]["id"], "reviewerUserId": self.u["head"]["id"], "collaboratorUserIds": [],
                     "title": "联调首页"}

        def fake(method, path, user_id, team_id, scopes, operation, **kwargs):
            self.calls.append({"method": method, "path": path, "scopes": scopes, "user_id": user_id, "operation": operation, **kwargs})
            if path.startswith("/responsibility/plans/") and method == "GET":
                return {"id": 2, "teamId": self.t["sales"], "sourceType": "MEETING", "status": "DRAFT", "tasks": [dict(self.task)],
                        "createdBy": self.u["emp"]["id"]}
            if path.startswith("/responsibility/tasks/") and method == "GET":
                return {"task": dict(self.task), "events": [], "deliverables": []}
            if path.startswith("/responsibility/tasks") or path.startswith("/responsibility/plans"):
                return [] if method == "GET" else {"task": dict(self.task), "events": [], "deliverables": []}
            if path.startswith("/responsibility/mine"):
                return dict(self.mine)
            if path.startswith("/responsibility/summary"):
                return {"byStatus": {}, "week": {"due": 0}, "metrics": {}, "load": []}
            return {}

        p = patch.object(hub, "call", side_effect=fake)
        p.start()
        self.addCleanup(p.stop)

    def h(self, name):
        return self.u[name]["headers"]

    def params(self, call):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(call["path"]).query)
        return {k: v[0].split(",") if k.endswith("Ids") else v[0] for k, v in q.items()}

    def uid(self, name):
        return self.u[name]["id"]

    def write(self, name, path, body, team="sales"):
        return self.client.post(f"/enterprise/responsibility{path}", json={"team_id": self.t[team], **body}, headers=self.h(name))

    # ---- 身份 ----

    def test_identity_is_computed_per_user(self):
        scope = sorted(str(t) for t in self.t.values())
        cases = (("head", "sales", [str(self.t["sales"])], [str(self.t["sales"])]),
                 ("emp", "sales", [str(self.t["sales"])], None),
                 ("it1", "it", [str(self.t["it"])], None),
                 ("emp2", "sales", sorted([str(self.t["sales"]), str(self.t["it"])]), None))
        for name, team, members, heads in cases:
            with self.subTest(name=name):
                self.calls.clear()
                self.assertEqual(self.client.get(f"/enterprise/responsibility/tasks?team_id={self.t[team]}", headers=self.h(name)).status_code, 200)
                p = self.params(self.calls[-1])
                self.assertEqual(sorted(p["scopeTeamIds"]), scope)
                self.assertEqual(sorted(p["memberTeamIds"]), members)
                self.assertEqual(p.get("headTeamIds"), heads)
                self.assertEqual(self.calls[-1]["scopes"], ["responsibility.read"])

    def test_org_admin_heads_every_department(self):
        self.client.get(f"/enterprise/responsibility/mine?team_id={self.t['sales']}", headers=self.h("admin"))
        self.assertEqual(sorted(self.params(self.calls[-1])["headTeamIds"]), sorted(str(t) for t in self.t.values()))

    def test_leaving_a_department_drops_it_from_member_teams(self):
        self.client.get(f"/enterprise/responsibility/mine?team_id={self.t['sales']}", headers=self.h("emp2"))
        self.assertIn(str(self.t["it"]), self.params(self.calls[-1])["memberTeamIds"])
        self.db.execute(text("UPDATE team_members SET status='disabled' WHERE team_id=:t AND user_id=:u"),
                        {"t": self.t["it"], "u": self.uid("emp2")})
        self.db.commit()
        self.client.get(f"/enterprise/responsibility/mine?team_id={self.t['sales']}", headers=self.h("emp2"))
        self.assertEqual(self.params(self.calls[-1])["memberTeamIds"], [str(self.t["sales"])])

    def test_non_members_and_disabled_members_are_rejected_before_java(self):
        self.assertEqual(self.client.get(f"/enterprise/responsibility/mine?team_id={self.t['sales']}", headers=self.h("out")).status_code, 403)
        self.assertEqual(self.client.get(f"/enterprise/responsibility/mine?team_id={self.t['sales']}", headers=self.h("it1")).status_code, 403)
        self.db.execute(text("UPDATE organization_members SET status='disabled' WHERE organization_id=:o AND user_id=:u"),
                        {"o": self.org, "u": self.uid("emp")})
        self.db.commit()
        self.assertEqual(self.client.get(f"/enterprise/responsibility/mine?team_id={self.t['sales']}", headers=self.h("emp")).status_code, 403)
        self.assertFalse(self.calls)

    # ---- 可被指派的名单 ----

    def test_candidates_list_only_active_members_and_mark_heads(self):
        self.db.execute(text("UPDATE organization_members SET status='disabled' WHERE organization_id=:o AND user_id=:u"),
                        {"o": self.org, "u": self.uid("gone")})
        self.db.commit()
        data = self.client.get(f"/enterprise/responsibility/candidates?team_id={self.t['sales']}", headers=self.h("emp")).json()
        ids = {m["user_id"] for m in data["members"]}
        self.assertEqual(ids, {self.uid("head"), self.uid("emp"), self.uid("emp2"), self.uid("admin")})
        self.assertNotIn(self.uid("gone"), ids)           # 账号停用的人不能被指派
        self.assertNotIn(self.uid("it1"), ids)            # 别的部门的人不是本部门成员
        self.assertTrue(next(m for m in data["members"] if m["user_id"] == self.uid("head"))["is_head"])
        self.assertFalse(data["me"]["is_head"])
        self.assertIn(self.uid("admin"), {r["user_id"] for r in data["reviewers"]})
        self.assertEqual(self.client.get(f"/enterprise/responsibility/candidates?team_id={self.t['sales']}", headers=self.h("out")).status_code, 403)

    def test_eligible_reviewers_include_org_admins_who_are_not_team_members(self):
        eligible = rs.eligible_sync(self.org, self.t["it"])
        self.assertEqual(set(eligible["memberIds"]), {self.uid("it1"), self.uid("emp2")})
        self.assertIn(self.uid("admin"), eligible["reviewerIds"])
        self.assertNotIn(self.uid("admin"), eligible["memberIds"])
        self.assertNotIn(self.uid("out"), eligible["reviewerIds"])

    # ---- 写入 ----

    def test_create_plan_sends_server_computed_eligible_list_not_the_clients(self):
        body = {"title": "例会责任计划", "source_type": "MEETING", "source_text": "张三负责联调", "unresolved": ["没说验收人"],
                "tasks": [{"title": "联调首页", "responsible_user_id": self.uid("emp"), "reviewer_user_id": self.uid("head"),
                           "due_date": "2026-11-01", "deliverable": "构建包", "acceptance_criteria": "回归通过", "evidence": "张三负责联调"}]}
        response = self.write("emp", "/plans", body)
        self.assertEqual(response.status_code, 200, response.text)
        call = next(c for c in self.calls if c["method"] == "POST")
        self.assertEqual(call["scopes"], ["responsibility.read", "responsibility.write"])
        sent = call["json_body"]
        self.assertEqual(sent["teamId"], self.t["sales"])
        self.assertEqual(sent["tasks"][0]["responsibleUserId"], self.uid("emp"))
        self.assertEqual(set(sent["eligible"]["memberIds"]), {self.uid("head"), self.uid("emp"), self.uid("emp2"), self.uid("gone"), self.uid("admin")})
        self.assertIsNotNone(call["idempotency_key"])
        # 客户端自带的 eligible 之类的字段会被拒绝或忽略：请求体里没有这个字段
        extra = self.client.post("/enterprise/responsibility/plans", headers=self.h("emp"),
                                 json={"team_id": self.t["sales"], "title": "x", "eligible": {"memberIds": [1]},
                                       "tasks": [{"title": "联调首页"}]})
        self.assertEqual(extra.status_code, 200)
        self.assertNotIn(1, self.calls[-1]["json_body"]["eligible"]["memberIds"])

    def test_create_plan_validates_shape(self):
        self.assertEqual(self.write("emp", "/plans", {"title": "x", "tasks": []}).status_code, 422)
        bad = {"title": "x", "tasks": [{"title": "联调", "due_date": "明天"}]}
        self.assertEqual(self.write("emp", "/plans", bad).status_code, 422)
        self.assertEqual(self.write("emp", "/plans", {"title": "x", "tasks": [{"title": "联调", "priority": "ASAP"}]}).status_code, 422)
        self.assertFalse([c for c in self.calls if c["method"] == "POST"])

    def test_only_the_head_can_publish_and_it_is_checked_before_java(self):
        denied = self.write("emp", "/plans/2/publish", {})
        self.assertEqual(denied.status_code, 403)
        self.assertFalse([c for c in self.calls if c["method"] == "POST"])
        ok = self.write("head", "/plans/2/publish", {"note": "按会议决定指派"})
        self.assertEqual(ok.status_code, 200, ok.text)
        call = next(c for c in self.calls if c["method"] == "POST")
        self.assertTrue(call["path"].startswith("/responsibility/plans/2/publish?"))
        self.assertEqual(call["json_body"]["note"], "按会议决定指派")
        self.assertIn(self.uid("emp"), call["json_body"]["eligible"]["memberIds"])

    def test_publish_uses_current_membership_so_removed_people_are_not_eligible(self):
        self.db.execute(text("UPDATE team_members SET status='disabled' WHERE team_id=:t AND user_id=:u"),
                        {"t": self.t["sales"], "u": self.uid("emp")})
        self.db.commit()
        self.write("head", "/plans/2/publish", {})
        call = next(c for c in self.calls if c["method"] == "POST")
        self.assertNotIn(self.uid("emp"), call["json_body"]["eligible"]["memberIds"])

    def test_actions_requiring_people_fetch_the_eligible_list_and_plain_ones_do_not(self):
        self.write("emp", "/tasks/5/accept", {})
        accept = [c for c in self.calls if c["method"] == "POST"][-1]
        self.assertNotIn("eligible", accept["json_body"])
        self.assertEqual(accept["operation"], "responsibility_accept")
        for action, extra in (("revise", {"reason": "换人", "responsible_user_id": self.uid("emp2")}),
                              ("block", {"reason": "缺资料", "waiting_on_user_id": self.uid("head")}),
                              ("decide-transfer", {"approve": True, "responsible_user_id": self.uid("emp2")})):
            with self.subTest(action=action):
                self.calls.clear()
                self.write("head", f"/tasks/5/{action}", extra)
                sent = [c for c in self.calls if c["method"] == "POST"][-1]["json_body"]
                self.assertIn(self.uid("emp2"), sent["eligible"]["memberIds"])

    def test_unknown_action_is_rejected_and_forwarded_fields_are_camel_cased(self):
        self.assertIn(self.write("emp", "/tasks/5/hack", {}).status_code, (400, 422))
        self.write("emp", "/tasks/5/request-extension", {"proposed_date": "2026-12-01", "reason": "依赖方延迟"})
        sent = [c for c in self.calls if c["method"] == "POST"][-1]["json_body"]
        self.assertEqual((sent["proposedDate"], sent["reason"]), ("2026-12-01", "依赖方延迟"))
        self.assertEqual(self.write("emp", "/tasks/5/progress", {"note": "做了一半", "percent": 150}).status_code, 422)

    # ---- 部门首页卡片 ----

    def cards(self, name, team="sales"):
        data = self.client.get(f"/enterprise/home?team_id={self.t[team]}", headers=self.h(name)).json()
        return {c["key"]: c for c in data["cards"]}

    def test_home_cards_for_employee_and_head(self):
        employee = self.cards("emp")
        self.assertEqual((employee["resp_accept"]["value"], employee["resp_accept"]["section"], employee["resp_accept"]["tab"]), (2, "collab", "mine"))
        self.assertEqual(employee["resp_accept"]["tone"], "warn")
        self.assertEqual(employee["resp_doing"]["value"], 3)
        self.assertEqual(employee["resp_doing"]["tone"], "danger")                  # 有逾期
        self.assertIn("逾期 1 项", employee["resp_doing"]["hint"])
        self.assertNotIn("resp_blocked", employee)                                  # 没有受阻就不占位
        self.assertNotIn("resp_review", employee)
        self.assertNotIn("resp_team_overdue", employee)                             # 部门维度只给负责人
        self.mine.update(isHead=True, blocked=1, pendingReview=2, teamOverdue=4, weekCompletionRate=62.5)
        head = self.cards("head")
        self.assertEqual(head["resp_blocked"]["value"], 1)
        self.assertEqual((head["resp_review"]["value"], head["resp_review"]["tab"]), (2, "review"))
        self.assertEqual((head["resp_team_overdue"]["value"], head["resp_team_overdue"]["tone"]), (4, "danger"))
        self.assertEqual(head["resp_week"]["value"], "62.5%")

    def test_home_hides_the_cards_when_the_business_service_is_unavailable(self):
        with patch.object(hub, "call", side_effect=hub.EnterpriseHubError(503, "unavailable")):
            keys = set(self.cards("emp"))
        self.assertFalse({k for k in keys if k.startswith("resp_")})

    # ---- 展示 ----

    def test_detail_gets_names_and_chinese_labels(self):
        data = self.client.get(f"/enterprise/responsibility/tasks/5?team_id={self.t['sales']}", headers=self.h("emp")).json()
        task = data["task"]
        self.assertEqual(task["statusLabel"], "执行中")
        self.assertTrue(task["responsibleName"])
        self.assertTrue(task["reviewerName"])

    def test_summary_adds_narrative(self):
        data = self.client.get(f"/enterprise/responsibility/summary?team_id={self.t['sales']}", headers=self.h("head")).json()
        self.assertIn("本周没有到期的责任", data["narrative"])


if __name__ == "__main__":
    unittest.main()
