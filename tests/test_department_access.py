"""部门业务接口的严格权限：前端隐藏之外，后端对直接请求同样生效。

覆盖：企业/部门/成员身份四层有效性、业务模块与部门业务类型一致、Agent 工具、部门助手的部门上下文。
"""
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import text

from models.init_db import Agent, SessionLocal
from service import department_access as da
from service import enterprise_hub_client as hub
from tests import _route_client as rc
from tests._async_helpers import run_async
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


class ModuleRuleTest(unittest.TestCase):
    def test_module_department_matrix(self):
        self.assertTrue(da.module_allows("procurement", "procurement"))
        self.assertFalse(da.module_allows("procurement", "sales"))
        self.assertFalse(da.module_allows("procurement", None))
        self.assertTrue(da.module_allows("crm", "sales"))
        self.assertFalse(da.module_allows("crm", "procurement"))
        for code in ("hr", "finance", "it", "sales", None):
            self.assertTrue(da.module_allows("leave", code))
            self.assertTrue(da.module_allows("finance", code))


@unittest.skipUnless(_AVAILABLE, _WHY)
class DepartmentAccessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.client = rc.make_client()
        cls.admin = rc.create_user("da-admin")
        cls.staff = rc.create_user("da-staff")      # 每个部门的普通成员
        cls.head = rc.create_user("da-head")        # 每个部门的负责人
        cls.org = _create_org(cls.db, "da-org-" + uuid.uuid4().hex[:6], cls.admin["id"])
        cls.teams = {}
        for code in ("hr", "procurement", "sales", None):
            team = _create_team(cls.db, cls.org, f"da-{code or 'none'}", cls.admin["id"])
            if code:
                cls.db.execute(text("UPDATE teams SET department_code=:c WHERE id=:t"), {"c": code, "t": team})
            cls.teams[code] = team
            _add_team_member(cls.db, team, cls.staff["id"], "member")
            _add_team_member(cls.db, team, cls.head["id"], "admin")
        cls.db.commit()
        for user in (cls.staff, cls.head):
            _add_org_member(cls.db, cls.org, user["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        ids = ",".join(str(t) for t in cls.teams.values())
        cls.db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({ids})"))
        cls.db.execute(text(f"DELETE FROM teams WHERE id IN ({ids})"))
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    def setUp(self):
        self.db.commit()
        self.set_org_member("active")
        self.db.execute(text(f"UPDATE teams SET status='active' WHERE id IN ({','.join(str(t) for t in self.teams.values())})"))
        self.db.execute(text("UPDATE organizations SET status='active' WHERE id=:o"), {"o": self.org})
        self.db.commit()

    def set_org_member(self, status, user=None):
        self.db.execute(text("UPDATE organization_members SET status=:s WHERE organization_id=:o AND user_id=:u"),
                        {"s": status, "o": self.org, "u": (user or self.staff)["id"]})
        self.db.commit()

    def get(self, path, user=None):
        return self.client.get(path, headers=(user or self.staff)["headers"])

    # ---- 业务模块必须与部门业务类型一致（直接请求同样被拦住） ----

    def test_procurement_only_for_procurement_department(self):
        for code, expected in (("procurement", None), ("hr", 403), ("sales", 403), (None, 403)):
            with self.subTest(department=code):
                with patch.object(hub, "call", return_value=[]):
                    response = self.get(f"/enterprise/procurement/team-pending?team_id={self.teams[code]}", self.head)
                if expected:
                    self.assertEqual(response.status_code, expected, response.text)
                    self.assertIn("仅对采购部门开放", response.json().get("detail") or response.text)
                else:
                    self.assertEqual(response.status_code, 200, response.text)

    def test_procurement_draft_creation_blocked_for_other_departments(self):
        body = {"team_id": self.teams["hr"], "lines": [{"sku": "X", "quantity": 1}]}
        response = self.client.post("/enterprise/procurement/mine", json=body, headers=self.staff["headers"])
        self.assertEqual(response.status_code, 403, response.text)

    def test_crm_only_for_sales_department(self):
        for code in ("hr", "procurement", None):
            with self.subTest(department=code):
                response = self.get(f"/enterprise/crm/customers?team_id={self.teams[code]}")
                self.assertEqual(response.status_code, 403, response.text)
        with patch.object(hub, "call", return_value=[]):
            self.assertEqual(self.get(f"/enterprise/crm/customers?team_id={self.teams['sales']}").status_code, 200)

    def test_leave_and_expense_available_in_every_department(self):
        with patch.object(hub, "call", return_value=[]):
            for code in ("hr", "procurement", "sales", None):
                for path in ("oa/leave", "finance"):
                    self.assertEqual(self.get(f"/enterprise/{path}/team-pending?team_id={self.teams[code]}",
                                              self.head).status_code, 200, (code, path))

    # ---- 企业 / 部门 / 成员身份必须有效 ----

    def test_disabled_org_member_loses_department_access_despite_team_row(self):
        with patch.object(hub, "call", return_value=[]):
            self.assertEqual(self.get(f"/enterprise/crm/customers?team_id={self.teams['sales']}").status_code, 200)
            self.set_org_member("disabled")
            response = self.get(f"/enterprise/crm/customers?team_id={self.teams['sales']}")
            self.assertEqual(response.status_code, 403, response.text)

    def test_removed_org_member_loses_department_access(self):
        self.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o AND user_id=:u"),
                        {"o": self.org, "u": self.staff["id"]})
        self.db.commit()
        response = self.get(f"/enterprise/crm/customers?team_id={self.teams['sales']}")
        self.assertEqual(response.status_code, 403, response.text)
        self.db.execute(text("INSERT INTO organization_members (organization_id, user_id, role_id, status, created_at, updated_at) "
                             "SELECT :o, :u, id, 'active', NOW(), NOW() FROM enterprise_role WHERE scope='organization' AND code='member'"),
                        {"o": self.org, "u": self.staff["id"]})
        self.db.commit()

    def test_disabled_department_and_disabled_organization_block_access(self):
        with patch.object(hub, "call", return_value=[]):
            self.db.execute(text("UPDATE teams SET status='disabled' WHERE id=:t"), {"t": self.teams["sales"]})
            self.db.commit()
            self.assertEqual(self.get(f"/enterprise/crm/customers?team_id={self.teams['sales']}").status_code, 403)
            self.db.execute(text("UPDATE teams SET status='active' WHERE id=:t"), {"t": self.teams["sales"]})
            self.db.execute(text("UPDATE organizations SET status='disabled' WHERE id=:o"), {"o": self.org})
            self.db.commit()
            self.assertEqual(self.get(f"/enterprise/crm/customers?team_id={self.teams['sales']}").status_code, 403)

    def test_disabled_org_member_is_not_a_team_admin_anymore(self):
        from models.enterprise_dao import is_team_admin_of_team
        from service import enterprise_access
        self.assertTrue(is_team_admin_of_team(self.db, self.head["id"], self.teams["hr"]))
        self.assertTrue(enterprise_access.is_team_admin(self.db, self.head["id"], self.teams["hr"]))
        self.set_org_member("disabled", self.head)
        self.db.commit()
        try:
            self.assertFalse(is_team_admin_of_team(self.db, self.head["id"], self.teams["hr"]))
            self.assertFalse(enterprise_access.is_team_admin(self.db, self.head["id"], self.teams["hr"]))
            response = self.get(f"/enterprise/oa/leave/team-pending?team_id={self.teams['hr']}", self.head)
            self.assertEqual(response.status_code, 403, response.text)
        finally:
            self.set_org_member("active", self.head)

    def test_disabled_org_member_cannot_use_department_agent(self):
        agent = Agent(user_id=self.admin["id"], name="da-dept-agent", agent_type="department", department_code="hr",
                      scope_type="department", team_id=self.teams["hr"], organization_id=self.org,
                      lifecycle_status="published")
        self.db.add(agent)
        self.db.commit()
        from service import access_control
        try:
            self.assertIsNotNone(access_control.get_usable_agent(self.db, self.staff["id"], agent.id))
            self.set_org_member("disabled")
            self.assertIsNone(access_control.get_usable_agent(self.db, self.staff["id"], agent.id))

            async def check(db):
                return await access_control.get_usable_agent_async(db, self.staff["id"], agent.id)
            from models.async_db import AsyncSessionLocal

            async def go():
                async with AsyncSessionLocal() as db:
                    return await check(db)
            self.assertIsNone(run_async(go()))
        finally:
            self.db.execute(text("DELETE FROM agent WHERE id=:a"), {"a": agent.id})
            self.db.commit()

    # ---- Agent 工具与部门助手的部门上下文 ----

    def test_tool_side_check_raises_for_wrong_department(self):
        da.check_team_module(self.teams["procurement"], "procurement")
        da.check_team_module(None, "procurement")   # 企业管理员的跨部门场景不带 team_id
        for code in ("hr", "sales", None):
            with self.assertRaises(ValueError):
                da.check_team_module(self.teams[code], "procurement")
        with self.assertRaises(ValueError):
            da.check_team_module(self.teams["hr"], "crm")
        self.db.execute(text("UPDATE teams SET status='disabled' WHERE id=:t"), {"t": self.teams["procurement"]})
        self.db.commit()
        with self.assertRaises(ValueError):
            da.check_team_module(self.teams["procurement"], "procurement")

    def test_department_agent_context_requires_caller_membership(self):
        agent = Agent(user_id=self.admin["id"], name="da-ctx-agent", agent_type="department", department_code="hr",
                      scope_type="department", team_id=self.teams["hr"], organization_id=self.org,
                      lifecycle_status="published")
        self.db.add(agent)
        self.db.commit()
        try:
            # 成员：用助手所在部门
            self.assertEqual(hub.resolve_caller_context(self.staff["id"], agent.id)["team_id"], self.teams["hr"])
            # 助手的创建者不是部门成员也不是企业管理员：不能借助手的部门身份，更不会拿到别的部门
            self.assertIsNone(hub.resolve_caller_context(self.admin["id"], agent.id)["team_id"])
            # 企业成员被停用：部门上下文失效，兜底的"在职第一个部门"也不会再解析出来
            self.set_org_member("disabled")
            self.assertIsNone(hub.resolve_caller_context(self.staff["id"], agent.id)["team_id"])
        finally:
            self.db.execute(text("DELETE FROM agent WHERE id=:a"), {"a": agent.id})
            self.db.commit()


if __name__ == "__main__":
    unittest.main()
