"""企业管理员只在自己的企业里有效（跨企业越权修复）。

此前 is_org_admin 看的是“用户在任何企业里的最高角色”，签进业务系统后，企业 B 的管理员可以审批、查看企业 A 的请假和 IT 工单
（scripts/e2e_security.py 在真实 Java + MySQL 上复现）。现在签进去的身份按资源所属部门所在的企业重新计算，并带上该企业的部门范围。"""
import base64
import json
import unittest
import uuid
from unittest.mock import MagicMock, patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import SessionLocal
from service import enterprise_access, enterprise_hub_client as hub
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, _WHY)
class OrgAdminScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.admin_a = rc.create_user("oas-aa")
        cls.admin_b = rc.create_user("oas-ab")
        cls.admin_both = rc.create_user("oas-bo")
        cls.member_a = rc.create_user("oas-ma")
        cls.org_a = _create_org(cls.db, "oas-a-" + uuid.uuid4().hex[:6], cls.admin_a["id"])
        cls.org_b = _create_org(cls.db, "oas-b-" + uuid.uuid4().hex[:6], cls.admin_b["id"])
        cls.team_a1 = _create_team(cls.db, cls.org_a, "oas-a1", cls.admin_a["id"])
        cls.team_a2 = _create_team(cls.db, cls.org_a, "oas-a2", cls.admin_a["id"])
        cls.team_a_off = _create_team(cls.db, cls.org_a, "oas-a-off", cls.admin_a["id"])
        cls.team_b1 = _create_team(cls.db, cls.org_b, "oas-b1", cls.admin_b["id"])
        cls.db.execute(text("UPDATE teams SET status='disabled' WHERE id=:t"), {"t": cls.team_a_off})
        cls.db.commit()
        _add_org_member(cls.db, cls.org_a, cls.admin_a["id"], "admin")
        _add_org_member(cls.db, cls.org_b, cls.admin_b["id"], "admin")
        _add_org_member(cls.db, cls.org_a, cls.admin_both["id"], "admin")
        _add_org_member(cls.db, cls.org_b, cls.admin_both["id"], "admin")
        _add_org_member(cls.db, cls.org_a, cls.member_a["id"], "member")
        _add_team_member(cls.db, cls.team_a1, cls.admin_a["id"], "member")
        _add_team_member(cls.db, cls.team_b1, cls.admin_b["id"], "member")
        _add_team_member(cls.db, cls.team_a1, cls.member_a["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.commit()
        for org in (cls.org_a, cls.org_b):
            cls.db.execute(text("DELETE FROM team_members WHERE team_id IN (SELECT id FROM teams WHERE organization_id=:o)"), {"o": org})
            cls.db.execute(text("DELETE FROM teams WHERE organization_id=:o"), {"o": org})
            cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": org})
            cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org})
        cls.db.commit()
        cls.db.close()

    def scope(self, user, team):
        self.db.commit()
        return enterprise_access.org_admin_scope(self.db, user["id"], team)

    def test_admin_of_an_organization_covers_exactly_that_organizations_active_teams(self):
        is_admin, teams = self.scope(self.admin_a, self.team_a1)
        self.assertTrue(is_admin)
        self.assertEqual(teams, sorted([self.team_a1, self.team_a2]))              # 已停用的部门不在范围里
        self.assertNotIn(self.team_b1, teams)

    def test_admin_of_another_organization_is_not_an_admin_here(self):
        self.assertEqual(self.scope(self.admin_b, self.team_a1), (False, []))
        self.assertEqual(self.scope(self.admin_b, self.team_a2), (False, []))
        self.assertEqual(self.scope(self.admin_a, self.team_b1), (False, []))

    def test_a_member_who_is_not_an_admin_gets_nothing(self):
        self.assertEqual(self.scope(self.member_a, self.team_a1), (False, []))

    def test_admin_of_two_organizations_gets_the_organization_of_the_team_only(self):
        is_admin, teams = self.scope(self.admin_both, self.team_b1)
        self.assertTrue(is_admin)
        self.assertEqual(teams, [self.team_b1])
        is_admin, teams = self.scope(self.admin_both, self.team_a1)
        self.assertEqual(teams, sorted([self.team_a1, self.team_a2]))

    def test_without_a_team_the_scope_is_every_organization_the_user_administers(self):
        is_admin, teams = self.scope(self.admin_both, None)
        self.assertEqual((is_admin, teams), (True, sorted([self.team_a1, self.team_a2, self.team_b1])))
        self.assertEqual(self.scope(self.member_a, None), (False, []))

    def test_unknown_or_disabled_team_and_disabled_organization(self):
        self.assertEqual(self.scope(self.admin_a, 99999999), (False, []))
        self.assertEqual(self.scope(self.admin_a, self.team_a_off), (False, []))
        self.db.execute(text("UPDATE organizations SET status='disabled' WHERE id=:o"), {"o": self.org_b})
        self.db.commit()
        try:
            self.assertEqual(self.scope(self.admin_b, self.team_b1), (False, []))
        finally:
            self.db.execute(text("UPDATE organizations SET status='active' WHERE id=:o"), {"o": self.org_b})
            self.db.commit()


def decode(headers):
    return json.loads(base64.b64decode(headers["X-Context"]))


@unittest.skipUnless(_AVAILABLE, _WHY)
class SignedContextTest(OrgAdminScopeTest):
    """hub.call 签出去的上下文：不信任调用方传来的 is_org_admin。"""

    def signed(self, user, team, claim=True):
        captured = {}

        def fake_request(method, url, headers=None, data=None, timeout=None):
            captured.update(headers)
            response = MagicMock(status_code=200, content=b"{}")
            response.json.return_value = {}
            return response
        with patch("service.enterprise_hub_client.requests.request", side_effect=fake_request):
            hub.call("GET", "/x", user["id"], team, ["s"], "op", is_org_admin=claim)
        return decode(captured)

    def test_the_admin_of_the_resources_organization_gets_the_org_scope(self):
        context = self.signed(self.admin_a, self.team_a1)
        self.assertTrue(context["is_org_admin"])
        self.assertEqual(sorted(context["org_team_ids"]), sorted([self.team_a1, self.team_a2]))

    def test_a_caller_cannot_claim_org_admin_for_someone_elses_organization(self):
        context = self.signed(self.admin_b, self.team_a1, claim=True)             # 企业 B 的管理员，工作台里当前部门是企业 A 的
        self.assertFalse(context["is_org_admin"])
        self.assertEqual(context["org_team_ids"], [])

    def test_non_admins_never_get_an_org_scope_even_if_the_caller_claims_it(self):
        context = self.signed(self.member_a, self.team_a1, claim=True)
        self.assertEqual((context["is_org_admin"], context["org_team_ids"]), (False, []))

    def test_unclaimed_org_admin_stays_false_and_costs_no_query(self):
        with patch("service.enterprise_hub_client._org_admin_scope") as scope:
            context = self.signed(self.admin_a, self.team_a1, claim=False)
        scope.assert_not_called()
        self.assertEqual((context["is_org_admin"], context["org_team_ids"]), (False, []))


class SignContextFormatTest(unittest.TestCase):
    def test_org_team_ids_only_signed_for_org_admins(self):
        admin = decode(hub.sign_context(1, 2, ["s"], "op", is_org_admin=True, org_team_ids=[2, 3], method="GET", path="/x", body_sha256="0"))
        plain = decode(hub.sign_context(1, 2, ["s"], "op", is_org_admin=False, org_team_ids=[2, 3], method="GET", path="/x", body_sha256="0"))
        self.assertEqual(admin["org_team_ids"], [2, 3])
        self.assertEqual(plain["org_team_ids"], [])


if __name__ == "__main__":
    unittest.main()
