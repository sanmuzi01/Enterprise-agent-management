"""人事入转调离的 FastAPI 权限层：身份（scope/roles/heads）怎么算、写进签名路径的是什么、谁能发起/落实系统变更。
Java 业务规则（检查、审批、清单、办结）由 HrCaseIntegrationTest 覆盖，这里把 Java 调用换成桩。"""
import unittest
import uuid
from unittest.mock import patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import SessionLocal
from service import enterprise_hub_client as hub
from service import hr_service as hr
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


class NarrativeTest(unittest.TestCase):
    def test_narrative(self):
        body = hr.narrative_for({"byType": {"ONBOARDING": {"PENDING_APPROVAL": 1, "IN_PROGRESS": 2, "COMPLETED": 3},
                                            "OFFBOARDING": {"COMPLETED": 1}},
                                 "avgDaysToComplete": 4.5, "overdueTasks": 2, "effectPending": 1})
        self.assertIn("进行中的人事事项：入职 3 件", body)
        self.assertIn("已办结 4 件，平均 4.5 天办完", body)
        self.assertIn("2 项办理任务已过期限", body)
        self.assertIn("1 件调岗/离职已办结但系统变更还没落实", body)


@unittest.skipUnless(_AVAILABLE, _WHY)
class HrAccessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.owner = rc.create_user("hrs-owner")
        names = ("hr1", "head", "emp", "it1", "fin1", "admin", "out")
        cls.u = {n: rc.create_user(f"hrs-{n}") for n in names}
        cls.org = _create_org(cls.db, "hrs-org-" + uuid.uuid4().hex[:6], cls.owner["id"])
        cls.other_org = _create_org(cls.db, "hrs-org2-" + uuid.uuid4().hex[:6], cls.u["out"]["id"])
        cls.t = {}
        for code in ("hr", "sales", "it", "finance"):
            cls.t[code] = _create_team(cls.db, cls.org, f"hrs-{code}", cls.owner["id"])
            cls.db.execute(text("UPDATE teams SET department_code=:c WHERE id=:t"), {"c": code, "t": cls.t[code]})
        cls.other_hr = _create_team(cls.db, cls.other_org, "hrs-other-hr", cls.u["out"]["id"])
        cls.db.execute(text("UPDATE teams SET department_code='hr' WHERE id=:t"), {"t": cls.other_hr})
        cls.db.commit()
        _add_team_member(cls.db, cls.t["hr"], cls.u["hr1"]["id"], "member")
        _add_team_member(cls.db, cls.t["sales"], cls.u["head"]["id"], "admin")
        _add_team_member(cls.db, cls.t["sales"], cls.u["emp"]["id"], "member")
        _add_team_member(cls.db, cls.t["it"], cls.u["it1"]["id"], "member")
        _add_team_member(cls.db, cls.t["finance"], cls.u["fin1"]["id"], "member")
        _add_team_member(cls.db, cls.t["sales"], cls.u["admin"]["id"], "member")
        _add_team_member(cls.db, cls.other_hr, cls.u["out"]["id"], "member")
        for n in ("hr1", "head", "emp", "it1", "fin1"):
            _add_org_member(cls.db, cls.org, cls.u[n]["id"], "member")
        _add_org_member(cls.db, cls.org, cls.u["admin"]["id"], "admin")
        _add_org_member(cls.db, cls.other_org, cls.u["out"]["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        ids = ",".join(str(t) for t in [*cls.t.values(), cls.other_hr])
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
        self.db.execute(text("DELETE FROM team_members WHERE team_id=:t AND user_id=:u"), {"t": self.t["it"], "u": self.u["emp"]["id"]})
        if not self.db.execute(text("SELECT 1 FROM team_members WHERE team_id=:t AND user_id=:u"),
                               {"t": self.t["sales"], "u": self.u["emp"]["id"]}).first():
            self.db.commit()
            _add_team_member(self.db, self.t["sales"], self.u["emp"]["id"], "member")
        self.db.commit()
        self.calls = []
        self.case = {"id": 9, "caseType": "TRANSFER", "status": "COMPLETED", "effectPending": True,
                     "employeeUserId": self.u["emp"]["id"], "teamId": self.t["sales"], "targetTeamId": self.t["it"],
                     "initiatorUserId": self.u["hr1"]["id"], "tasks": [{"owner": "IT", "doneBy": self.u["it1"]["id"]}]}

        def fake(method, path, user_id, team_id, scopes, operation, **kwargs):
            self.calls.append({"method": method, "path": path, "scopes": scopes, "user_id": user_id, **kwargs})
            if method == "GET" and (path.startswith("/hr/cases?") or path.startswith("/hr/cases/my-tasks")):
                return []
            if path.startswith("/hr/cases/summary"):
                return {"byType": {}, "overdueTasks": 0, "effectPending": 0}
            if path.startswith("/hr/cases/precheck"):
                return {"checks": [], "riskLevel": "NONE", "canSubmit": True}
            return dict(self.case)

        p = patch.object(hub, "call", side_effect=fake)
        p.start()
        self.addCleanup(p.stop)

    def h(self, n):
        return self.u[n]["headers"]

    def params(self, call):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(call["path"]).query)
        return {k: v[0].split(",") for k, v in q.items()}

    def body(self, **over):
        return {"team_id": self.t["hr"], "case_type": "PROBATION", "employee_user_id": self.u["emp"]["id"],
                "employee_team_id": self.t["sales"], "effective_date": "2026-11-01", **over}

    # ---- 身份计算 ----

    def test_actor_identity_is_computed_per_user(self):
        expected_scope = sorted(str(t) for t in self.t.values())
        cases = (("hr1", "hr", ["HR"], None), ("it1", "it", ["IT"], None), ("fin1", "finance", ["FINANCE"], None),
                 ("head", "sales", None, [str(self.t["sales"])]), ("emp", "sales", None, None))
        for name, team, roles, heads in cases:
            with self.subTest(name=name):
                self.calls.clear()
                self.assertEqual(self.client.get(f"/enterprise/hr/cases/my-tasks?team_id={self.t[team]}", headers=self.h(name)).status_code, 200)
                p = self.params(self.calls[-1])
                self.assertEqual(sorted(p["scopeTeamIds"]), expected_scope)
                self.assertEqual(p.get("roles"), roles)
                self.assertEqual(p.get("headTeamIds"), heads)
                self.assertEqual(self.calls[-1]["scopes"], ["hr.case.read"])

    def test_org_admin_heads_every_department(self):
        self.client.get(f"/enterprise/hr/cases/my-tasks?team_id={self.t['sales']}", headers=self.h("admin"))
        self.assertEqual(sorted(self.params(self.calls[-1])["headTeamIds"]), sorted(str(t) for t in self.t.values()))

    def test_disabled_member_and_non_member_are_rejected(self):
        self.assertEqual(self.client.get(f"/enterprise/hr/cases/my-tasks?team_id={self.t['hr']}", headers=self.h("emp")).status_code, 403)
        self.assertEqual(self.client.get(f"/enterprise/hr/cases/my-tasks?team_id={self.t['hr']}", headers=self.h("out")).status_code, 403)
        self.db.execute(text("UPDATE organization_members SET status='disabled' WHERE organization_id=:o AND user_id=:u"),
                        {"o": self.org, "u": self.u["hr1"]["id"]})
        self.db.commit()
        self.assertEqual(self.client.get(f"/enterprise/hr/cases/my-tasks?team_id={self.t['hr']}", headers=self.h("hr1")).status_code, 403)
        self.assertFalse(self.calls)

    def test_other_enterprise_hr_gets_only_its_own_scope(self):
        self.client.get(f"/enterprise/hr/cases?team_id={self.other_hr}", headers=self.h("out"))
        self.assertEqual(self.params(self.calls[-1])["scopeTeamIds"], [str(self.other_hr)])

    def test_me_reports_roles(self):
        self.assertEqual(self.client.get(f"/enterprise/hr/me?team_id={self.t['hr']}", headers=self.h("hr1")).json(),
                         {"roles": ["HR"], "is_hr": True, "is_head": False, "org_admin": False})

    # ---- 发起 ----

    def test_only_hr_can_create_and_employee_is_head_is_computed_server_side(self):
        for name, team in (("head", "sales"), ("it1", "it"), ("emp", "sales")):
            self.assertEqual(self.client.post("/enterprise/hr/cases", json=self.body(team_id=self.t[team]), headers=self.h(name)).status_code, 403, name)
        self.assertFalse(self.calls)
        ok = self.client.post("/enterprise/hr/cases", json=self.body(employee_user_id=self.u["head"]["id"]), headers=self.h("hr1"))
        self.assertEqual(ok.status_code, 200, ok.text)
        call = self.calls[-1]
        self.assertEqual(call["json_body"]["employeeIsHead"], True)
        self.assertIn("hr.case.write", call["scopes"])
        self.assertTrue(call["idempotency_key"])
        self.assertEqual(ok.json()["employeeName"], self.u["emp"]["name"])
        self.assertEqual(ok.json()["tasks"][0]["ownerLabel"], "IT")

    def test_employee_must_belong_to_the_department_and_target_must_be_in_enterprise(self):
        r = self.client.post("/enterprise/hr/cases", json=self.body(employee_team_id=self.t["it"]), headers=self.h("hr1"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("不是这个部门的有效成员", r.text)
        r = self.client.post("/enterprise/hr/cases", json=self.body(case_type="TRANSFER"), headers=self.h("hr1"))
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/enterprise/hr/cases", json=self.body(case_type="TRANSFER", target_team_id=self.other_hr), headers=self.h("hr1"))
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/enterprise/hr/cases", json=self.body(employee_team_id=self.other_hr), headers=self.h("hr1"))
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.calls)
        self.assertEqual(self.client.post("/enterprise/hr/cases", json=self.body(effective_date="下周一"), headers=self.h("hr1")).status_code, 422)
        self.assertEqual(self.client.post("/enterprise/hr/cases", json=self.body(case_type="PROMOTION"), headers=self.h("hr1")).status_code, 422)

    def test_actions_and_tasks_are_whitelisted(self):
        self.assertEqual(self.client.post("/enterprise/hr/cases/9/delete", json={"team_id": self.t["hr"]}, headers=self.h("hr1")).status_code, 400)
        self.assertEqual(self.client.post("/enterprise/hr/cases/9/tasks/1/undo", json={"team_id": self.t["hr"]}, headers=self.h("hr1")).status_code, 400)
        self.assertFalse(self.calls)
        r = self.client.post("/enterprise/hr/cases/9/tasks/1/done", json={"team_id": self.t["it"], "note": "已开通"}, headers=self.h("it1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self.calls[-1]["path"].startswith("/hr/cases/9/tasks/1/done?"))
        self.assertEqual(self.calls[-1]["json_body"], {"note": "已开通"})
        self.assertEqual(self.params(self.calls[-1])["roles"], ["IT"])

    def test_candidates_only_for_hr(self):
        self.assertEqual(self.client.get(f"/enterprise/hr/candidates?team_id={self.t['sales']}", headers=self.h("head")).status_code, 403)
        data = self.client.get(f"/enterprise/hr/candidates?team_id={self.t['hr']}", headers=self.h("hr1")).json()
        self.assertEqual({t["id"] for t in data["teams"]}, set(self.t.values()))
        heads = [m for m in data["members"] if m["user_id"] == self.u["head"]["id"]]
        self.assertTrue(heads and heads[0]["is_head"])

    # ---- 落实系统变更 ----

    def test_apply_effect_requires_org_admin(self):
        r = self.client.post("/enterprise/hr/cases/9/apply-effect", json={"team_id": self.t["hr"]}, headers=self.h("hr1"))
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.calls)

    def test_apply_transfer_moves_department_membership(self):
        r = self.client.post("/enterprise/hr/cases/9/apply-effect", json={"team_id": self.t["sales"]}, headers=self.h("admin"))
        self.assertEqual(r.status_code, 200, r.text)
        self.db.commit()
        rows = self.db.execute(text("SELECT team_id FROM team_members WHERE user_id=:u AND status='active'"), {"u": self.u["emp"]["id"]}).all()
        self.assertEqual([row[0] for row in rows], [self.t["it"]])
        self.assertTrue(self.calls[-1]["path"].startswith("/hr/cases/9/effect-applied?"))

    def test_apply_offboarding_disables_enterprise_membership(self):
        self.case.update(caseType="OFFBOARDING", targetTeamId=None)
        r = self.client.post("/enterprise/hr/cases/9/apply-effect", json={"team_id": self.t["sales"]}, headers=self.h("admin"))
        self.assertEqual(r.status_code, 200, r.text)
        self.db.commit()
        status = self.db.execute(text("SELECT status FROM organization_members WHERE organization_id=:o AND user_id=:u"),
                                 {"o": self.org, "u": self.u["emp"]["id"]}).scalar()
        self.assertEqual(status, "disabled")
        # 停用后员工立即失去部门工作台权限
        self.assertEqual(self.client.get(f"/enterprise/hr/cases?view=mine&team_id={self.t['sales']}", headers=self.h("emp")).status_code, 403)

    def test_apply_effect_refuses_admins_and_self_and_nothing_pending(self):
        self.case.update(caseType="OFFBOARDING", employeeUserId=self.u["admin"]["id"])
        r = self.client.post("/enterprise/hr/cases/9/apply-effect", json={"team_id": self.t["sales"]}, headers=self.h("admin"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("不能为自己", r.text)
        self.case.update(effectPending=False, employeeUserId=self.u["emp"]["id"])
        r = self.client.post("/enterprise/hr/cases/9/apply-effect", json={"team_id": self.t["sales"]}, headers=self.h("admin"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("没有待落实", r.text)


if __name__ == "__main__":
    unittest.main()
