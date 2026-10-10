"""管理员给企业智能体配置接口工具（/admin/org/agents/{id}/api-connectors）。

以前接口工具只能在用户端“我的助手 → 接口工具”里配，而且要求助手是操作人自己建的：企业智能体、部门助手不在管理员名下，
管理员打开只会看到“不存在或无权限”，后台也没有入口——结果谁都配不了。
"""
import unittest
from unittest import mock

from sqlalchemy import text

from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()
NO_SSRF_DNS = mock.patch("service.tools.http_connector_service.validate_crawl_url", lambda u: u)


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class ManagedApiConnectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from models.init_db import Agent, SessionLocal
        cls.admin = rc.create_user("mac-admin")
        cls.owner = rc.create_user("mac-owner")       # 部门助手挂在别人名下：管理员不是它的主人
        cls.member = rc.create_user("mac-member")
        with SessionLocal() as db:
            dept = Agent(user_id=cls.owner["id"], name="mac-部门助手", agent_type="department", model_name="glm-4")
            personal = Agent(user_id=cls.owner["id"], name="mac-个人助手", model_name="glm-4")
            db.add_all([dept, personal])
            db.commit()
            cls.dept_id, cls.personal_id = dept.id, personal.id
        cls.env = rc.admin_env(cls.admin["name"])
        cls.env.start()
        cls.client = rc.make_client()
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        from models.init_db import SessionLocal
        from tests._dept_agent_cleanup import purge_agents
        cls.client.__exit__(None, None, None)
        cls.env.stop()
        with SessionLocal() as db:
            db.execute(text("DELETE FROM agent_api_connector WHERE agent_id IN (:a, :b)"), {"a": cls.dept_id, "b": cls.personal_id})
            db.commit()
            purge_agents(db, [cls.dept_id, cls.personal_id])
        rc.cleanup()

    def url(self, agent_id, connector_id=None):
        base = f"/admin/org/agents/{agent_id}/api-connectors"
        return base if connector_id is None else f"{base}/{connector_id}"

    def payload(self, name="query_order"):
        return {"name": name, "description": "按订单号查物流状态", "url": "https://erp.example.com/orders?token=SECRET",
                "method": "GET", "headers": {"Authorization": "Bearer SECRET"},
                "param_schema": {"type": "object", "properties": {"order_no": {"type": "string"}}, "required": ["order_no"]}}

    def test_admin_manages_tools_on_a_department_agent_they_do_not_own(self):
        h = self.admin["headers"]
        # 用户端那套接口：管理员不是主人，看不到（这就是以前“管理员也配不了”的原因）
        self.assertEqual(self.client.get(f"/agent/{self.dept_id}/api-connectors", headers=h).status_code, 404)

        with NO_SSRF_DNS, mock.patch("service.audit_service.record") as audit:
            r = self.client.post(self.url(self.dept_id), headers=h, json=self.payload())
        self.assertEqual(r.status_code, 200, r.text)
        created = r.json()
        self.assertTrue(created["has_headers"])
        detail = audit.call_args.kwargs["detail"]
        self.assertEqual(audit.call_args.args[1], "agent.api_connector_created")
        self.assertEqual(detail["url"], "https://erp.example.com/orders", "审计里不记查询串（可能带令牌）")
        self.assertNotIn("SECRET", str(audit.call_args), "审计里不能出现认证头或令牌")

        listed = self.client.get(self.url(self.dept_id), headers=h).json()
        self.assertEqual([c["id"] for c in listed], [created["id"]])

        # 运行时按智能体加载，不管是谁配的：部门助手对话时就能用上
        from models.agent_api_connector_dao import list_connectors_by_agent
        from models.init_db import SessionLocal
        with SessionLocal() as db:
            self.assertEqual([c.id for c in list_connectors_by_agent(db, self.dept_id, enabled_only=True)], [created["id"]])

        with mock.patch("service.audit_service.record") as audit:
            r = self.client.patch(self.url(self.dept_id, created["id"]), headers=h, json={"is_enabled": False})
        self.assertEqual((r.status_code, r.json()["is_enabled"]), (200, False))
        self.assertEqual(audit.call_args.args[1], "agent.api_connector_disabled")
        with SessionLocal() as db:
            self.assertEqual(list_connectors_by_agent(db, self.dept_id, enabled_only=True), [], "停用后运行时不再加载")

        with mock.patch("service.audit_service.record") as audit:
            self.assertEqual(self.client.delete(self.url(self.dept_id, created["id"]), headers=h).status_code, 200)
        self.assertEqual(audit.call_args.args[1], "agent.api_connector_deleted")
        self.assertEqual(self.client.get(self.url(self.dept_id), headers=h).json(), [])

    def test_duplicate_tool_names_are_rejected(self):
        with NO_SSRF_DNS, mock.patch("service.audit_service.record"):
            first = self.client.post(self.url(self.dept_id), headers=self.admin["headers"], json=self.payload("dup_tool"))
            second = self.client.post(self.url(self.dept_id), headers=self.admin["headers"], json=self.payload("dup_tool"))
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 400, "工具名会原样交给大模型，同一个智能体里不能重复")
        with mock.patch("service.audit_service.record"):
            self.client.delete(self.url(self.dept_id, first.json()["id"]), headers=self.admin["headers"])

    def test_only_admins_and_only_enterprise_agents(self):
        with NO_SSRF_DNS:
            self.assertEqual(self.client.post(self.url(self.dept_id), headers=self.member["headers"], json=self.payload()).status_code, 403)
            self.assertEqual(self.client.get(self.url(self.dept_id), headers=self.owner["headers"]).status_code, 403,
                             "助手的主人不是管理员，也不能走后台接口")
            r = self.client.post(self.url(self.personal_id), headers=self.admin["headers"], json=self.payload())
        self.assertEqual(r.status_code, 404, "个人助手不归后台管")
        self.assertEqual(self.client.patch(self.url(self.dept_id, 999999999), headers=self.admin["headers"],
                                           json={"is_enabled": True}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
