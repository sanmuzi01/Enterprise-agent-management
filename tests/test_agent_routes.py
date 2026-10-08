"""管理端智能体接口（HTTP 层）：创建 / 详情 / 编辑 / 划分 / 可选项 / 权限。

service 层的测试覆盖不到“路由的请求模型有没有这个字段、有没有把它传下去”——
曾经编辑接口的请求模型漏了 config / space_ids / skill_ids，服务层测试全绿、浏览器里点保存却是 500。
这里走真实的 HTTP 路由，专门防这类接线错误。"""
import unittest

from sqlalchemy import text

from models.init_db import (EnterpriseRole, KnowledgeSpace, Organization, OrganizationMember, SessionLocal, Team)
from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class AgentRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = rc.create_user("arr-admin")
        cls.member = rc.create_user("arr-member")
        db = SessionLocal()
        cls.db = db
        org = Organization(name="arr-org", owner_user_id=cls.admin["id"])
        db.add(org)
        db.commit()
        cls.org_id = org.id
        team = Team(name="arr-team", owner_user_id=cls.admin["id"], organization_id=cls.org_id)
        db.add(team)
        db.commit()
        cls.team_id = team.id
        role = db.query(EnterpriseRole.id).filter_by(scope="organization", code="member").scalar()
        db.add(OrganizationMember(organization_id=cls.org_id, user_id=cls.admin["id"], role_id=role, status="active"))
        space = KnowledgeSpace(user_id=cls.admin["id"], name="arr-space", scope_type="enterprise", organization_id=cls.org_id)
        db.add(space)
        db.commit()
        cls.space_id = space.id
        cls.client = rc.make_client()
        cls.env = rc.admin_env(cls.admin["name"])
        cls.env.start()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        db = cls.db
        db.execute(text("DELETE FROM agent_knowledge_space WHERE agent_id IN (SELECT id FROM agent WHERE name LIKE 'arr-%')"))
        db.execute(text("DELETE FROM agent WHERE name LIKE 'arr-%'"))
        db.execute(text("DELETE FROM knowledge_spaces WHERE name LIKE 'arr-%'"))
        db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team_id})
        db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org_id})
        db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org_id})
        db.commit()
        db.close()
        rc.cleanup()

    def api(self, method, path, user=None, **kwargs):
        return getattr(self.client, method)(f"/admin/org{path}", headers=(user or self.admin)["headers"], **kwargs)

    def test_the_whole_lifecycle_over_http(self):
        created = self.api("post", "/agents", json={
            "name": "arr-agent", "agent_type": "department", "model_name": "glm-4-plus",
            "role": "你是助手", "task": "回答问题", "constraints": "不编造", "output": "先结论",
            "config": {"temperature": 25, "rag_enabled": 1, "kb_top_k": 6},
            "space_ids": [self.space_id],
        })
        self.assertEqual(created.status_code, 200, created.text)
        agent_id = created.json()["id"]

        detail = self.api("get", f"/agents/{agent_id}").json()
        self.assertEqual((detail["config"]["temperature"], detail["config"]["kb_top_k"], detail["space_ids"]), (25, 6, [self.space_id]))
        self.assertEqual(detail["assignment"], "unassigned")
        self.assertFalse(detail["readiness"]["ready"])

        edited = self.api("patch", f"/agents/{agent_id}", json={
            "expected_row_version": detail["row_version"], "name": "arr-agent-2",
            "config": {"temperature": 55, "kb_rerank_enabled": 1}, "space_ids": [], "constraints": "必须引用来源",
        })
        self.assertEqual(edited.status_code, 200, edited.text)
        detail = self.api("get", f"/agents/{agent_id}").json()
        self.assertEqual((detail["name"], detail["config"]["temperature"], detail["config"]["kb_rerank_enabled"], detail["space_ids"]),
                         ("arr-agent-2", 55, 1, []))
        self.assertEqual(detail["prompt"]["constraints"], "必须引用来源")

        assigned = self.api("put", f"/agents/{agent_id}/assignment", json={"target": "department", "team_id": self.team_id,
                                                                            "expected_row_version": detail["row_version"]})
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(assigned.json()["assignment"], "department")

        detail = self.api("get", f"/agents/{agent_id}").json()
        self.assertTrue(detail["readiness"]["ready"], detail["readiness"])
        published = self.api("patch", f"/agents/{agent_id}", json={"expected_row_version": detail["row_version"], "lifecycle_status": "published"})
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()["lifecycle_status"], "published")

        blocked = self.api("put", f"/agents/{agent_id}/assignment", json={"target": "enterprise"})
        self.assertEqual(blocked.status_code, 400, "已发布的要先停用才能调整划分")

    def test_options_endpoint(self):
        options = self.api("get", "/agent-options")
        self.assertEqual(options.status_code, 200, options.text)
        body = options.json()
        self.assertTrue({"models", "skills", "spaces", "teams"} <= set(body))
        self.assertIn("arr-space", {s["name"] for s in body["spaces"]})
        self.assertIn("arr-team", {t["name"] for t in body["teams"]})

    def test_invalid_input_returns_client_errors_not_500(self):
        for payload in ({"name": "arr-bad", "agent_type": "central", "config": {"temperature": 500}},
                        {"name": "arr-bad", "agent_type": "central", "model_name": "not-a-model"},
                        {"name": "arr-bad", "agent_type": "central", "space_ids": [987654321]}):
            response = self.api("post", "/agents", json=payload)
            self.assertIn(response.status_code, (400, 422), (payload, response.text))

    def test_only_admins_can_use_these_endpoints(self):
        for method, path in (("get", "/agent-options"), ("get", "/agents/1"), ("post", "/agents"), ("put", "/agents/1/assignment")):
            response = self.api(method, path, user=self.member, **({"json": {"name": "x", "agent_type": "central", "target": "enterprise"}} if method in ("post", "put") else {}))
            self.assertEqual(response.status_code, 403, (method, path, response.text))


if __name__ == "__main__":
    unittest.main()
