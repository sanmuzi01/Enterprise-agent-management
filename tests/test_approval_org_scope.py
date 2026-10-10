"""平台审批（删除知识库空间等）只属于资源所在的企业：别的企业的管理员看不到、批不了。
此前 `/approvals/pending` 和 `/approvals/{id}/decide` 只要求“是任何一个企业的管理员”，企业 B 的管理员能看到并批准企业 A 的空间删除申请。"""
import unittest
import uuid

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import KnowledgeSpace, SessionLocal
from tests._async_helpers import run_async
from tests.test_enterprise_access import _add_org_member, _create_org

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, _WHY)
class ApprovalOrgScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.db = SessionLocal()
        cls.admin_a = rc.create_user("apr-aa")
        cls.admin_b = rc.create_user("apr-ab")
        cls.applicant = rc.create_user("apr-ap")
        cls.org_a = _create_org(cls.db, "apr-a-" + uuid.uuid4().hex[:6], cls.admin_a["id"])
        cls.org_b = _create_org(cls.db, "apr-b-" + uuid.uuid4().hex[:6], cls.admin_b["id"])
        _add_org_member(cls.db, cls.org_a, cls.admin_a["id"], "admin")
        _add_org_member(cls.db, cls.org_b, cls.admin_b["id"], "admin")
        _add_org_member(cls.db, cls.org_a, cls.applicant["id"], "member")
        cls.space = KnowledgeSpace(user_id=cls.applicant["id"], name="待删除空间", organization_id=cls.org_a)
        cls.db.add(cls.space)
        cls.db.commit()
        cls.space_id = cls.space.id
        cls.ids = []

    @classmethod
    def tearDownClass(cls):
        cls.db.commit()
        for approval_id in cls.ids:
            cls.db.execute(text("DELETE FROM approval_request WHERE id=:i"), {"i": approval_id})
        cls.db.execute(text("DELETE FROM knowledge_spaces WHERE id=:i"), {"i": cls.space_id})
        for org in (cls.org_a, cls.org_b):
            cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": org})
            cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org})
        cls.db.commit()
        cls.db.close()
        rc.cleanup()

    def request(self, resource_type="space", resource_id=None):
        from models.async_db import AsyncSessionLocal
        from service import approval_service

        async def go():
            async with AsyncSessionLocal() as session:
                return await approval_service.request_or_get_pending(session, self.applicant["id"], "space.delete", resource_type,
                                                                      resource_id if resource_id is not None else self.space_id, reason="删除测试")
        approval = run_async(go())
        self.ids.append(approval["id"])
        return approval

    def pending_ids(self, who):
        response = self.client.get("/approvals/pending", headers=who["headers"])
        self.assertEqual(response.status_code, 200, response.text)
        return {item["id"] for item in response.json()}

    def test_an_admin_sees_and_decides_only_their_own_organizations_approvals(self):
        approval = self.request()
        self.assertIn(approval["id"], self.pending_ids(self.admin_a))
        self.assertNotIn(approval["id"], self.pending_ids(self.admin_b))                  # 别的企业的管理员看不到
        denied = self.client.post(f"/approvals/{approval['id']}/decide", json={"approve": True}, headers=self.admin_b["headers"])
        self.assertEqual(denied.status_code, 404, denied.text)                            # 也批不了，且不泄露它存在
        self.db.commit()
        status = self.db.execute(text("SELECT status FROM approval_request WHERE id=:i"), {"i": approval["id"]}).scalar()
        self.assertEqual(status, "pending")
        decided = self.client.post(f"/approvals/{approval['id']}/decide", json={"approve": False}, headers=self.admin_a["headers"])
        self.assertEqual(decided.status_code, 200, decided.text)

    def test_approvals_for_unregistered_resource_types_are_invisible_over_http(self):
        """未登记所属企业的审批类型一律不可见（新增审批类型必须在 approval_service.resource_org_id 里登记）。"""
        approval = self.request(resource_type="mystery", resource_id=424242)
        for admin in (self.admin_a, self.admin_b):
            self.assertNotIn(approval["id"], self.pending_ids(admin))
            self.assertEqual(self.client.post(f"/approvals/{approval['id']}/decide", json={"approve": True}, headers=admin["headers"]).status_code, 404)

    def test_non_admins_and_anonymous_are_rejected(self):
        self.assertIn(self.client.get("/approvals/pending", headers=self.applicant["headers"]).status_code, (403, 404))
        self.assertEqual(self.client.get("/approvals/pending").status_code, 401)

    def test_service_level_calls_without_scoping_keep_working(self):
        from models.async_db import AsyncSessionLocal
        from service import approval_service

        async def go():
            async with AsyncSessionLocal() as session:
                return await approval_service.list_pending(session, limit=5)
        self.assertIsInstance(run_async(go()), list)


if __name__ == "__main__":
    unittest.main()
