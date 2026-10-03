"""财务记账（凭证）的 FastAPI 权限层：只有财务部门的有效成员能用，可见范围限定在同一企业，
写操作带幂等键和 write scope。Java 业务规则（科目建议、风险检查、入账）由 Java 集成测试覆盖，
这里把 Java 调用替换成桩，专门验证"谁能调、调的时候带了什么"。
"""
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import text

from models.init_db import SessionLocal
from service import enterprise_hub_client as hub
from service import finance_voucher_service as svc
from service import finance_workspace_service as fin
from tests import _route_client as rc
from tests._async_helpers import run_async
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


class NarrativeTest(unittest.TestCase):
    def test_narrative_only_restates_summary_numbers(self):
        text_ = svc.narrative_for({
            "period": "2026-10", "balanced": True,
            "byStatus": {"POSTED": {"count": 3, "amount": 1234.5}, "DRAFT": {"count": 2, "amount": 80},
                         "VOID": {"count": 1, "amount": 5}},
            "bySubject": [{"name": "管理费用-差旅费", "direction": "D", "amount": 900},
                          {"name": "管理费用-办公费", "direction": "D", "amount": 334.5},
                          {"name": "其他应付款-员工报销款", "direction": "C", "amount": 1234.5}],
            "draftRisk": {"WARN": 1, "BLOCK": 0},
            "unbookedClaims": 2, "unbookedAmount": 66,
            "efficiency": {"postedCount": 3, "adoptedAsIs": 2, "adoptedRate": 66.7, "avgHoursToPost": 4.5},
        })
        self.assertIn("2026-10 已入账凭证 3 张，合计 ¥1,234.50", text_)
        self.assertIn("借方最大科目是「管理费用-差旅费」¥900.00", text_)
        self.assertIn("其中 66.7% 的凭证科目建议被原样采纳，从自动生成到入账平均 4.5 小时", text_)
        self.assertIn("另有 2 张待确认凭证", text_)
        self.assertIn("需核对 1 张", text_)
        self.assertIn("2 张已批准报销单还没有凭证", text_)
        self.assertIn("本期作废 1 张", text_)
        self.assertNotIn("不平衡", text_)

    def test_narrative_warns_when_unbalanced(self):
        text_ = svc.narrative_for({"period": "2026-10", "balanced": False, "byStatus": {}, "bySubject": []})
        self.assertIn("借贷合计不平衡", text_)


@unittest.skipUnless(_AVAILABLE, _WHY)
class VoucherPermissionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.owner = rc.create_user("fv-owner")
        cls.accountant = rc.create_user("fv-acc")        # 财务部门成员
        cls.sales_staff = rc.create_user("fv-sales")     # 销售部门成员
        cls.hr_head = rc.create_user("fv-hr")            # 人事部门负责人
        cls.sales_head = rc.create_user("fv-shead")      # 销售部门负责人
        cls.stranger = rc.create_user("fv-str")          # 同企业、不在财务部门
        cls.outsider = rc.create_user("fv-out")          # 另一家企业的财务
        cls.org = _create_org(cls.db, "fv-org-" + uuid.uuid4().hex[:6], cls.owner["id"])
        cls.other_org = _create_org(cls.db, "fv-org2-" + uuid.uuid4().hex[:6], cls.outsider["id"])
        cls.teams = {}
        for code in ("finance", "sales", "hr"):
            cls.teams[code] = _create_team(cls.db, cls.org, f"fv-{code}", cls.owner["id"])
            cls.db.execute(text("UPDATE teams SET department_code=:c WHERE id=:t"), {"c": code, "t": cls.teams[code]})
        cls.other_finance = _create_team(cls.db, cls.other_org, "fv-other-fin", cls.outsider["id"])
        cls.db.execute(text("UPDATE teams SET department_code='finance' WHERE id=:t"), {"t": cls.other_finance})
        cls.db.commit()
        _add_team_member(cls.db, cls.teams["finance"], cls.accountant["id"], "member")
        _add_team_member(cls.db, cls.teams["sales"], cls.sales_staff["id"], "member")
        _add_team_member(cls.db, cls.teams["hr"], cls.hr_head["id"], "admin")
        _add_team_member(cls.db, cls.teams["sales"], cls.sales_head["id"], "admin")
        _add_team_member(cls.db, cls.other_finance, cls.outsider["id"], "member")
        for user in (cls.accountant, cls.sales_staff, cls.hr_head, cls.sales_head, cls.stranger):
            _add_org_member(cls.db, cls.org, user["id"], "member")
        _add_org_member(cls.db, cls.other_org, cls.outsider["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        team_ids = ",".join(str(t) for t in [*cls.teams.values(), cls.other_finance])
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
            if path.startswith("/finance/vouchers/unbooked"):
                return [{"id": 77, "teamId": self.teams["sales"], "applicantUserId": self.sales_staff["id"]}]
            if "/summary" in path:
                return {"period": "2026-10", "balanced": True, "byStatus": {}, "bySubject": [],
                        "byTeam": [{"teamId": self.teams["sales"], "count": 1, "amount": 10}]}
            if path.startswith("/finance/vouchers?"):
                return [{"id": 1, "teamId": self.teams["sales"], "applicantUserId": self.sales_staff["id"],
                         "riskLevel": "WARN"}]
            return {"id": 1, "teamId": self.teams["sales"], "applicantUserId": self.sales_staff["id"],
                    "riskLevel": "NONE", "entries": []}

        patcher = patch.object(hub, "call", side_effect=fake_call)
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, path, user):
        return self.client.get(path, headers=user["headers"])

    def post(self, path, body, user):
        return self.client.post(path, json=body, headers=user["headers"])

    # ---- 谁能用 ----

    def test_finance_department_member_can_list_with_enterprise_scope(self):
        response = self.get(f"/enterprise/finance/vouchers?team_id={self.teams['finance']}&status=DRAFT", self.accountant)
        self.assertEqual(response.status_code, 200, response.text)
        call = self.calls[-1]
        self.assertEqual(call["scopes"], ["finance.voucher.read"])
        expected = sorted([self.teams["finance"], self.teams["sales"], self.teams["hr"]])
        scope_text = call["path"].split("scopeTeamIds=")[1].split("&")[0]
        self.assertEqual(sorted(int(i) for i in scope_text.split(",")), expected)
        self.assertNotIn(str(self.other_finance), scope_text.split(","))      # 另一家企业的部门不在范围内
        self.assertIn("status=DRAFT", call["path"])
        row = response.json()[0]
        self.assertEqual(row["applicantName"], self.sales_staff["name"])
        self.assertEqual(row["teamName"], "fv-sales")
        self.assertEqual(row["riskLabel"], "需核对")

    def test_non_finance_departments_are_rejected_even_for_their_own_head(self):
        for team in ("sales", "hr"):
            for user in (self.sales_staff, self.hr_head):
                response = self.get(f"/enterprise/finance/vouchers?team_id={self.teams[team]}", user)
                self.assertEqual(response.status_code, 403, (team, response.text))
        self.assertFalse(self.calls)

    def test_same_org_user_outside_finance_team_is_rejected(self):
        for user in (self.stranger, self.sales_staff, self.hr_head):
            response = self.get(f"/enterprise/finance/vouchers?team_id={self.teams['finance']}", user)
            self.assertEqual(response.status_code, 403, response.text)
        self.assertFalse(self.calls)

    def test_other_enterprise_finance_cannot_use_this_enterprise(self):
        response = self.get(f"/enterprise/finance/vouchers?team_id={self.teams['finance']}", self.outsider)
        self.assertEqual(response.status_code, 403, response.text)
        response = self.get(f"/enterprise/finance/vouchers?team_id={self.other_finance}", self.outsider)
        self.assertEqual(response.status_code, 200, response.text)
        scope_text = self.calls[-1]["path"].split("scopeTeamIds=")[1].split("&")[0]
        self.assertEqual(scope_text, str(self.other_finance))

    def test_disabled_org_member_loses_access(self):
        self.db.execute(text("UPDATE organization_members SET status='disabled' WHERE organization_id=:o AND user_id=:u"),
                        {"o": self.org, "u": self.accountant["id"]})
        self.db.commit()
        response = self.get(f"/enterprise/finance/vouchers?team_id={self.teams['finance']}", self.accountant)
        self.assertEqual(response.status_code, 403, response.text)

    def test_org_admin_can_use_finance_team_but_not_other_department_types(self):
        with patch("service.enterprise_access.is_org_admin_async", return_value=True):
            ok = self.get(f"/enterprise/finance/vouchers?team_id={self.teams['finance']}", self.stranger)
            self.assertEqual(ok.status_code, 200, ok.text)
            denied = self.get(f"/enterprise/finance/vouchers?team_id={self.teams['sales']}", self.stranger)
            self.assertEqual(denied.status_code, 403, denied.text)

    # ---- 写操作 ----

    def test_write_operations_use_write_scope_and_idempotency_key(self):
        team = self.teams["finance"]
        cases = [
            ("/enterprise/finance/vouchers/5/confirm", {"team_id": team, "note": "ok", "acknowledge_warnings": True},
             "/confirm"),
            ("/enterprise/finance/vouchers/5/void", {"team_id": team, "reason": "票据不合规"}, "/void"),
            ("/enterprise/finance/vouchers/5/regenerate", {"team_id": team}, "/regenerate"),
            ("/enterprise/finance/vouchers/5/entries/9/subject",
             {"team_id": team, "subject_code": "6602.03", "reason": "实为办公用品"}, "/entries/9/subject"),
            ("/enterprise/finance/vouchers/5/date", {"team_id": team, "voucher_date": "2026-10-01"}, "/date"),
        ]
        for url, body, suffix in cases:
            with self.subTest(url=url):
                self.calls.clear()
                response = self.post(url, body, self.accountant)
                self.assertEqual(response.status_code, 200, response.text)
                call = self.calls[-1]
                self.assertEqual(call["method"], "POST")
                self.assertIn("finance.voucher.write", call["scopes"])
                self.assertTrue(call.get("idempotency_key"))
                self.assertIn(suffix, call["path"])
                self.assertIn("scopeTeamIds=", call["path"])

    def test_confirm_passes_acknowledgement_and_note(self):
        self.post("/enterprise/finance/vouchers/5/confirm",
                  {"team_id": self.teams["finance"], "note": "已核对", "acknowledge_warnings": True}, self.accountant)
        self.assertEqual(self.calls[-1]["json_body"], {"note": "已核对", "acknowledgeWarnings": True})

    def test_write_operations_are_rejected_for_other_departments(self):
        for url, body in [
            ("/enterprise/finance/vouchers/5/confirm", {"team_id": self.teams["sales"]}),
            ("/enterprise/finance/vouchers/5/void", {"team_id": self.teams["hr"], "reason": "想作废"}),
            ("/enterprise/finance/vouchers/from-claim/3", {"team_id": self.teams["sales"]}),
        ]:
            response = self.post(url, body, self.sales_staff if "sales" in str(body) else self.hr_head)
            self.assertEqual(response.status_code, 403, (url, response.text))
        self.assertFalse(self.calls)

    def test_request_validation(self):
        team = self.teams["finance"]
        self.assertEqual(self.post("/enterprise/finance/vouchers/5/void", {"team_id": team, "reason": "x"},
                                   self.accountant).status_code, 422)
        self.assertEqual(self.post("/enterprise/finance/vouchers/5/entries/9/subject",
                                   {"team_id": team, "subject_code": "", "reason": "有理由"}, self.accountant).status_code, 422)
        self.assertEqual(self.get(f"/enterprise/finance/vouchers?team_id={team}&limit=999", self.accountant).status_code, 422)

    def test_generate_from_claim_derives_department_from_the_claim_team(self):
        response = self.post("/enterprise/finance/vouchers/from-claim/77", {"team_id": self.teams["finance"]},
                             self.accountant)
        self.assertEqual(response.status_code, 200, response.text)
        generate = [c for c in self.calls if "/from-claim/77" in c["path"]][0]
        self.assertEqual(generate["json_body"], {"departmentCode": "sales"})
        self.assertIn("finance.voucher.write", generate["scopes"])

    def test_hub_errors_are_translated(self):
        for status, expected in ((400, 400), (404, 404), (409, 409), (500, 502)):
            with self.subTest(status=status):
                with patch.object(hub, "call", side_effect=hub.EnterpriseHubError(status, "业务服务说不行")):
                    response = self.post("/enterprise/finance/vouchers/5/confirm", {"team_id": self.teams["finance"]},
                                         self.accountant)
                self.assertEqual(response.status_code, expected, response.text)
                self.assertIn("业务服务说不行", response.text)

    def test_monthly_summary_adds_team_names_and_narrative(self):
        response = self.get(f"/enterprise/finance/vouchers/summary?team_id={self.teams['finance']}&period=2026-10",
                            self.accountant)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["byTeam"][0]["teamName"], "fv-sales")
        self.assertIn("2026-10 已入账凭证", body["narrative"])
        self.assertIn("period=2026-10", self.calls[-1]["path"])

    # ---- 报销入口带上部门类型，供 Java 选科目大类 ----

    def test_create_and_approve_claim_carry_department_code(self):
        with patch.object(hub, "call", return_value={"id": 1}) as call:
            run_async(self._create_draft())
        self.assertEqual(call.call_args.kwargs["json_body"]["departmentCode"], "sales")
        with patch.object(hub, "call", return_value={"id": 1}) as call:
            run_async(self._decide())
        self.assertEqual(call.call_args.kwargs["json_body"]["departmentCode"], "sales")

    async def _create_draft(self):
        from models.async_db import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            return await fin.create_my_expense_draft_async(
                db, self.sales_staff["id"], self.teams["sales"], [{"category": "TRAVEL", "amount": 10}])

    async def _decide(self):
        from models.async_db import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            return await fin.decide_expense_claim_async(db, self.sales_head["id"], 9, self.teams["sales"], "approve", "好")


if __name__ == "__main__":
    unittest.main()
