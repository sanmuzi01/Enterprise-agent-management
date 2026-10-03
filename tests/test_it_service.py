"""IT 服务台的 FastAPI 权限层：谁能提工单、谁能审批、谁能进 IT 台，以及调 Java 时带了什么。
Java 的业务规则（状态机、SLA、设备生命周期）由 Java 集成测试覆盖，这里把 Java 调用换成桩。"""
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import text

from models.init_db import SessionLocal
from service import enterprise_hub_client as hub
from service import it_service as it
from tests import _route_client as rc
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


class NarrativeTest(unittest.TestCase):
    def test_narrative_restates_summary_numbers(self):
        body = it.narrative_for({
            "days": 30, "unassigned": 2, "overdue": 1,
            "byStatus": {"OPEN": 2, "IN_PROGRESS": 3, "WAITING_USER": 1, "PENDING_APPROVAL": 4},
            "effect": {"resolved": 10, "slaMetRate": 90.0, "avgResolveHours": 5.5, "avgFirstResponseHours": 0.8,
                       "reopenedTickets": 2}})
        self.assertIn("处理中的工单 6 张（待接单 2 张，已超时 1 张）", body)
        self.assertIn("另有 4 张在等部门负责人批准", body)
        self.assertIn("近 30 天解决 10 张，SLA 达成率 90%", body)
        self.assertIn("平均首次响应 0.8 小时", body)
        self.assertIn("2 张被重新打开过", body)

    def test_narrative_without_resolved_tickets(self):
        body = it.narrative_for({"days": 7, "byStatus": {}, "effect": {"resolved": 0}})
        self.assertIn("近 7 天还没有解决的工单", body)


@unittest.skipUnless(_AVAILABLE, _WHY)
class ItServiceAccessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.owner = rc.create_user("its-owner")
        cls.employee = rc.create_user("its-emp")        # 销售部门成员
        cls.head = rc.create_user("its-head")           # 销售部门负责人
        cls.it_staff = rc.create_user("its-it1")        # IT 部门成员
        cls.it_staff2 = rc.create_user("its-it2")
        cls.hr_head = rc.create_user("its-hr")
        cls.outsider = rc.create_user("its-out")        # 另一家企业的 IT
        cls.stranger = rc.create_user("its-str")        # 同企业，没有部门
        cls.org = _create_org(cls.db, "its-org-" + uuid.uuid4().hex[:6], cls.owner["id"])
        cls.other_org = _create_org(cls.db, "its-org2-" + uuid.uuid4().hex[:6], cls.outsider["id"])
        cls.teams = {}
        for code in ("it", "sales", "hr"):
            cls.teams[code] = _create_team(cls.db, cls.org, f"its-{code}", cls.owner["id"])
            cls.db.execute(text("UPDATE teams SET department_code=:c WHERE id=:t"), {"c": code, "t": cls.teams[code]})
        cls.other_it = _create_team(cls.db, cls.other_org, "its-other-it", cls.outsider["id"])
        cls.db.execute(text("UPDATE teams SET department_code='it' WHERE id=:t"), {"t": cls.other_it})
        cls.db.commit()
        _add_team_member(cls.db, cls.teams["sales"], cls.employee["id"], "member")
        _add_team_member(cls.db, cls.teams["sales"], cls.head["id"], "admin")
        _add_team_member(cls.db, cls.teams["it"], cls.it_staff["id"], "member")
        _add_team_member(cls.db, cls.teams["it"], cls.it_staff2["id"], "member")
        _add_team_member(cls.db, cls.teams["hr"], cls.hr_head["id"], "admin")
        _add_team_member(cls.db, cls.other_it, cls.outsider["id"], "member")
        for user in (cls.employee, cls.head, cls.it_staff, cls.it_staff2, cls.hr_head, cls.stranger):
            _add_org_member(cls.db, cls.org, user["id"], "member")
        _add_org_member(cls.db, cls.other_org, cls.outsider["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        team_ids = ",".join(str(t) for t in [*cls.teams.values(), cls.other_it])
        cls.db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({team_ids})"))
        cls.db.execute(text(f"DELETE FROM teams WHERE id IN ({team_ids})"))
        for org in (cls.org, cls.other_org):
            cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": org})
            cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org})
        cls.db.commit()
        cls.db.close()

    def setUp(self):
        self.db.commit()
        self.db.execute(text("UPDATE organization_members SET status='active' WHERE organization_id=:o"), {"o": self.org})
        self.db.commit()
        self.calls = []

        def fake_call(method, path, user_id, team_id, scopes, operation, **kwargs):
            self.calls.append({"method": method, "path": path, "user_id": user_id, "team_id": team_id,
                               "scopes": scopes, "operation": operation, **kwargs})
            if "/classify" in path:
                return {"category": "INCIDENT", "priority": "HIGH", "reasons": ["命中 无法"]}
            if "/kb/suggest" in path:
                return [{"id": 1, "title": "打印机无法打印", "steps": "1. 重启", "score": 2}]
            if path.startswith("/it/desk/summary"):
                return {"days": 30, "byStatus": {"OPEN": 1}, "effect": {"resolved": 0},
                        "load": [{"userId": self.it_staff["id"], "open": 2}]}
            if method == "GET" and (path.startswith("/it/desk/tickets?") or path.startswith("/it/tickets/mine") or "team-pending" in path):
                return [self.ticket()]
            if method == "GET" and path.startswith("/it/desk/devices?"):
                return [self.device()]
            if "/devices" in path:
                return self.device()
            if path.startswith("/it/devices/mine"):
                return []
            return self.ticket()

        patcher = patch.object(hub, "call", side_effect=fake_call)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ticket(self):
        return {"id": 5, "requesterUserId": self.employee["id"], "assigneeUserId": self.it_staff["id"],
                "teamId": self.teams["sales"], "category": "INCIDENT", "status": "IN_PROGRESS", "priority": "HIGH",
                "approverUserId": None, "title": "打印机坏了",
                "comments": [{"id": 1, "authorUserId": self.it_staff["id"], "body": "已接单", "internal": False}]}

    def device(self):
        return {"id": 3, "assigneeUserId": self.employee["id"], "events": [
            {"eventType": "ASSIGNED", "actorUserId": self.it_staff["id"], "subjectUserId": self.employee["id"]}]}

    def get(self, path, user):
        return self.client.get(path, headers=user["headers"])

    def post(self, path, body, user):
        return self.client.post(path, json=body, headers=user["headers"])

    def desk_url(self, path="/enterprise/it/desk/tickets", team="it", **params):
        query = "&".join([f"team_id={self.teams[team]}"] + [f"{k}={v}" for k, v in params.items()])
        return f"{path}?{query}"

    def scope_ids(self, call):
        return sorted(int(i) for i in call["path"].split("scopeTeamIds=")[1].split("&")[0].split(","))

    # ---- 员工侧 ----

    def test_any_department_member_can_submit_ticket_with_write_scope(self):
        for user, team in ((self.employee, "sales"), (self.hr_head, "hr"), (self.it_staff, "it")):
            self.calls.clear()
            response = self.post("/enterprise/it/tickets", {"team_id": self.teams[team], "category": "INCIDENT",
                                                            "title": "打印机坏了", "description": "卡纸后无法打印"}, user)
            self.assertEqual(response.status_code, 200, (team, response.text))
            call = self.calls[-1]
            self.assertEqual((call["method"], call["path"], call["team_id"]), ("POST", "/it/tickets", self.teams[team]))
            self.assertIn("it.ticket.write", call["scopes"])
            self.assertTrue(call["idempotency_key"])
            self.assertEqual(call["json_body"]["title"], "打印机坏了")
        data = response.json()
        self.assertEqual(data["requesterName"], self.employee["name"])
        self.assertEqual(data["statusLabel"], "处理中")
        self.assertEqual(data["comments"][0]["authorName"], self.it_staff["name"])

    def test_submitting_for_a_department_you_do_not_belong_to_is_rejected(self):
        for user, team in ((self.employee, "hr"), (self.stranger, "sales"), (self.employee, "it")):
            response = self.post("/enterprise/it/tickets", {"team_id": self.teams[team], "category": "OTHER",
                                                            "title": "咨询一下", "description": "想问一个问题"}, user)
            self.assertEqual(response.status_code, 403, (team, response.text))
        self.assertFalse(self.calls)

    def test_disabled_org_member_cannot_submit(self):
        self.db.execute(text("UPDATE organization_members SET status='disabled' WHERE organization_id=:o AND user_id=:u"),
                        {"o": self.org, "u": self.employee["id"]})
        self.db.commit()
        response = self.post("/enterprise/it/tickets", {"team_id": self.teams["sales"], "category": "OTHER",
                                                        "title": "咨询一下", "description": "想问一个问题"}, self.employee)
        self.assertEqual(response.status_code, 403, response.text)

    def test_request_validation(self):
        base = {"team_id": self.teams["sales"], "category": "INCIDENT", "title": "标题", "description": "描述内容"}
        for patch_body in ({"category": "WHATEVER"}, {"priority": "SUPER"}, {"title": ""}, {"description": "短"}):
            response = self.post("/enterprise/it/tickets", {**base, **patch_body}, self.employee)
            self.assertEqual(response.status_code, 422, (patch_body, response.text))
        self.assertEqual(self.post(f"/enterprise/it/tickets/5/reopen", {"reason": "x"}, self.employee).status_code, 422)
        self.assertFalse(self.calls)

    def test_own_ticket_actions_use_write_scope_and_idempotency_key(self):
        for path, body, suffix in (("/cancel", None, "/cancel"), ("/confirm", None, "/confirm"),
                                   ("/comments", {"body": "补充信息"}, "/comments"),
                                   ("/reopen", {"reason": "还是不行"}, "/reopen")):
            self.calls.clear()
            response = self.client.post(f"/enterprise/it/tickets/5{path}", json=body, headers=self.employee["headers"]) \
                if body else self.client.post(f"/enterprise/it/tickets/5{path}", headers=self.employee["headers"])
            self.assertEqual(response.status_code, 200, (path, response.text))
            call = self.calls[-1]
            self.assertIn("it.ticket.write", call["scopes"])
            self.assertTrue(call["idempotency_key"])
            self.assertTrue(call["path"].endswith(suffix))

    def test_approval_is_only_for_department_heads(self):
        sales = self.teams["sales"]
        self.assertEqual(self.get(f"/enterprise/it/tickets/team-pending?team_id={sales}", self.employee).status_code, 403)
        self.assertEqual(self.post("/enterprise/it/tickets/5/decide", {"team_id": sales, "action": "approve"},
                                   self.employee).status_code, 403)
        self.assertFalse(self.calls)
        ok = self.get(f"/enterprise/it/tickets/team-pending?team_id={sales}", self.head)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertTrue(self.calls[-1]["is_team_admin"])
        decided = self.post("/enterprise/it/tickets/5/decide", {"team_id": sales, "action": "reject", "note": "预算不足"}, self.head)
        self.assertEqual(decided.status_code, 200, decided.text)
        call = self.calls[-1]
        self.assertEqual(call["json_body"], {"note": "预算不足"})
        self.assertIn("it.ticket.approve", call["scopes"])
        # 其他部门的负责人不能处理销售部门的待审批
        self.assertEqual(self.get(f"/enterprise/it/tickets/team-pending?team_id={sales}", self.hr_head).status_code, 403)

    def test_suggest_quotes_text_and_requires_membership(self):
        response = self.get(f"/enterprise/it/suggest?text=打印机 无法打印&team_id={self.teams['sales']}", self.employee)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["classification"]["categoryLabel"], "故障")
        self.assertEqual(body["articles"][0]["title"], "打印机无法打印")
        self.assertIn("%E6%89%93", self.calls[0]["path"])   # 中文已编码，签名的就是实际发出的路径
        self.assertEqual(self.get(f"/enterprise/it/suggest?text=打印机&team_id={self.teams['hr']}", self.employee).status_code, 403)

    # ---- IT 台 ----

    def test_it_department_member_can_use_desk_with_enterprise_scope(self):
        response = self.get(self.desk_url(status="OPEN"), self.it_staff)
        self.assertEqual(response.status_code, 200, response.text)
        call = self.calls[-1]
        self.assertEqual(call["scopes"], ["it.desk.read"])
        self.assertEqual(self.scope_ids(call), sorted(self.teams.values()))
        self.assertNotIn(self.other_it, self.scope_ids(call))
        self.assertIn("status=OPEN", call["path"])
        row = response.json()[0]
        self.assertEqual((row["requesterName"], row["assigneeName"], row["teamName"]), (self.employee["name"], self.it_staff["name"], "its-sales"))
        self.assertEqual(row["categoryLabel"], "故障")

    def test_non_it_departments_and_other_enterprises_cannot_use_desk(self):
        for user, team in ((self.employee, "sales"), (self.head, "sales"), (self.hr_head, "hr")):
            self.assertEqual(self.get(self.desk_url(team=team), user).status_code, 403, team)
        for user in (self.employee, self.head, self.stranger, self.hr_head):
            self.assertEqual(self.get(self.desk_url(), user).status_code, 403)       # 用 IT 部门 id 也不行：不是成员
        self.assertEqual(self.get(self.desk_url(), self.outsider).status_code, 403)   # 其他企业的 IT
        ok = self.client.get(f"/enterprise/it/desk/tickets?team_id={self.other_it}", headers=self.outsider["headers"])
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.scope_ids(self.calls[-1]), [self.other_it])
        self.assertEqual(len(self.calls), 1)

    def test_disabled_it_member_loses_desk(self):
        self.db.execute(text("UPDATE organization_members SET status='disabled' WHERE organization_id=:o AND user_id=:u"),
                        {"o": self.org, "u": self.it_staff["id"]})
        self.db.commit()
        self.assertEqual(self.get(self.desk_url(), self.it_staff).status_code, 403)

    def test_org_admin_can_use_it_team_only(self):
        with patch("service.enterprise_access.is_org_admin_async", return_value=True):
            self.assertEqual(self.get(self.desk_url(), self.stranger).status_code, 200)
            self.assertEqual(self.get(self.desk_url(team="sales"), self.stranger).status_code, 403)

    def test_desk_writes_use_write_scope_and_idempotency_key(self):
        team = self.teams["it"]
        cases = [
            ("/enterprise/it/desk/tickets/5/status", {"team_id": team, "status": "WAITING_USER", "note": "请补充"}, "/status"),
            ("/enterprise/it/desk/tickets/5/resolve", {"team_id": team, "resolution": "重装驱动后恢复"}, "/resolve"),
            ("/enterprise/it/desk/tickets/5/comments", {"team_id": team, "body": "内部记录", "internal": True}, "/comments"),
            ("/enterprise/it/desk/tickets/5/reclassify", {"team_id": team, "category": "INCIDENT", "priority": "URGENT", "reason": "多人受影响"}, "/reclassify"),
        ]
        for url, body, suffix in cases:
            self.calls.clear()
            response = self.post(url, body, self.it_staff)
            self.assertEqual(response.status_code, 200, (url, response.text))
            call = self.calls[-1]
            self.assertIn("it.desk.write", call["scopes"])
            self.assertTrue(call["idempotency_key"])
            self.assertIn(suffix, call["path"])
            self.assertIn("scopeTeamIds=", call["path"])

    def test_desk_writes_are_rejected_for_non_it_users(self):
        for url, body in (("/enterprise/it/desk/tickets/5/resolve", {"resolution": "我也想解决一下"}),
                          ("/enterprise/it/desk/tickets/5/assign", {"take": True}),
                          ("/enterprise/it/desk/devices", {"asset_no": "A1", "device_type": "LAPTOP", "model": "x"})):
            response = self.post(url, {"team_id": self.teams["sales"], **body}, self.head)
            self.assertEqual(response.status_code, 403, (url, response.text))
        self.assertFalse(self.calls)

    def test_assign_validates_assignee_is_it_staff(self):
        team = self.teams["it"]
        self.assertEqual(self.post("/enterprise/it/desk/tickets/5/assign", {"team_id": team, "assignee_user_id": self.employee["id"]},
                                   self.it_staff).status_code, 400)
        self.assertEqual(self.post("/enterprise/it/desk/tickets/5/assign", {"team_id": team, "assignee_user_id": self.outsider["id"]},
                                   self.it_staff).status_code, 400)
        self.assertFalse(self.calls)
        ok = self.post("/enterprise/it/desk/tickets/5/assign", {"team_id": team, "assignee_user_id": self.it_staff2["id"]}, self.it_staff)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.calls[-1]["json_body"], {"assigneeUserId": self.it_staff2["id"]})
        self.calls.clear()
        self.assertEqual(self.post("/enterprise/it/desk/tickets/5/assign", {"team_id": team, "take": True}, self.it_staff).status_code, 200)
        self.assertEqual(self.calls[-1]["json_body"], {"assigneeUserId": self.it_staff["id"]})
        self.calls.clear()
        self.assertEqual(self.post("/enterprise/it/desk/tickets/5/assign", {"team_id": team}, self.it_staff).status_code, 200)
        self.assertEqual(self.calls[-1]["json_body"], {"assigneeUserId": None})   # 取消指派

    def test_staff_list_only_contains_active_it_members_of_this_enterprise(self):
        response = self.get(self.desk_url("/enterprise/it/desk/staff"), self.it_staff)
        self.assertEqual(response.status_code, 200, response.text)
        ids = {row["id"] for row in response.json()}
        self.assertEqual(ids, {self.it_staff["id"], self.it_staff2["id"]})
        self.db.execute(text("UPDATE organization_members SET status='disabled' WHERE organization_id=:o AND user_id=:u"),
                        {"o": self.org, "u": self.it_staff2["id"]})
        self.db.commit()
        self.assertEqual({row["id"] for row in self.get(self.desk_url("/enterprise/it/desk/staff"), self.it_staff).json()},
                         {self.it_staff["id"]})

    def test_device_assignment_holder_must_be_enterprise_member(self):
        team = self.teams["it"]
        self.assertEqual(self.post("/enterprise/it/desk/devices/3/assign", {"team_id": team, "user_id": self.outsider["id"]},
                                   self.it_staff).status_code, 400)
        self.assertFalse(self.calls)
        ok = self.post("/enterprise/it/desk/devices/3/assign", {"team_id": team, "user_id": self.employee["id"], "ticket_id": 5},
                       self.it_staff)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["assigneeName"], self.employee["name"])
        self.assertEqual(ok.json()["events"][0]["actorName"], self.it_staff["name"])
        self.assertEqual(self.calls[-1]["json_body"]["ticketId"], 5)

    def test_device_actions_are_whitelisted(self):
        team = self.teams["it"]
        for action in ("return", "repair", "repair-done", "retire"):
            self.calls.clear()
            self.assertEqual(self.post(f"/enterprise/it/desk/devices/3/{action}", {"team_id": team}, self.it_staff).status_code, 200, action)
            self.assertTrue(self.calls[-1]["path"].startswith(f"/it/desk/devices/3/{action}?"))
        self.calls.clear()
        self.assertEqual(self.post("/enterprise/it/desk/devices/3/delete", {"team_id": team}, self.it_staff).status_code, 400)
        self.assertFalse(self.calls)

    def test_create_device_sends_the_it_team_as_manager(self):
        response = self.post("/enterprise/it/desk/devices", {"team_id": self.teams["it"], "asset_no": " NB-001 ", "device_type": "LAPTOP",
                                                              "model": "ThinkPad", "warranty_until": "2029-01-01"}, self.it_staff)
        self.assertEqual(response.status_code, 200, response.text)
        body = self.calls[-1]["json_body"]
        self.assertEqual((body["assetNo"], body["managingTeamId"], body["warrantyUntil"]), ("NB-001", self.teams["it"], "2029-01-01"))
        self.assertEqual(self.post("/enterprise/it/desk/devices", {"team_id": self.teams["it"], "asset_no": "x", "device_type": "TOASTER",
                                                                    "model": "m"}, self.it_staff).status_code, 422)

    def test_summary_adds_names_and_narrative(self):
        response = self.get(self.desk_url("/enterprise/it/desk/summary", days=7), self.it_staff)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["load"][0]["userName"], self.it_staff["name"])
        self.assertIn("近 30 天还没有解决的工单", body["narrative"])
        self.assertIn("days=7", self.calls[-1]["path"])

    def test_hub_errors_are_translated(self):
        for status, expected in ((400, 400), (403, 403), (404, 404), (409, 409), (500, 502)):
            with self.subTest(status=status):
                with patch.object(hub, "call", side_effect=hub.EnterpriseHubError(status, "业务服务说不行")):
                    response = self.post("/enterprise/it/desk/tickets/5/resolve",
                                         {"team_id": self.teams["it"], "resolution": "试着解决一下"}, self.it_staff)
                self.assertEqual(response.status_code, expected, response.text)
                self.assertIn("业务服务说不行", response.text)


if __name__ == "__main__":
    unittest.main()
