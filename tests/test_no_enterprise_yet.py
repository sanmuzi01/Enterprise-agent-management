"""全新部署、还没跑 scripts/backfill_default_organization.py 时（库里没有企业记录），后台的只读列表照常能打开。

回归：用户管理列表加了“所属部门 / 企业角色”、企业知识库列表加了“部门划分概览”之后，它们在没有企业记录时直接抛
“企业尚未初始化”，用户管理和企业知识库两个页面都打不开（CI 用的空库就是这种状态）。现在这些只读列表在没有企业时
照常返回，只是没有部门；需要企业的写操作（划分给部门 / 全企业、建企业智能体）仍会明确提示先初始化企业。
"""
import unittest
from unittest import mock

from tests import _route_client as rc
from tests._async_helpers import run_async

_AVAILABLE, _WHY = rc.route_tests_available()
NO_ENTERPRISE = mock.patch("service.organization_admin_service.find_default_organization", mock.AsyncMock(return_value=None))


async def _with_db(fn):
    from models.async_db import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        return await fn(db)


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class NoEnterpriseYetTest(unittest.TestCase):
    PREFIX = "rt-noent"

    @classmethod
    def setUpClass(cls):
        cls.user = rc.create_user(cls.PREFIX)

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()

    def test_user_list_still_loads_without_departments(self):
        from service.admin_async_service import list_users
        with NO_ENTERPRISE:
            page = run_async(_with_db(lambda db: list_users(db, limit=10, search=self.PREFIX)))
        me = next(u for u in page["items"] if u["id"] == self.user["id"])
        self.assertEqual((me["departments"], me["org_role_code"]), ([], None))

    def test_knowledge_space_list_still_loads_without_departments(self):
        from service.admin_async_service import list_knowledge_spaces
        with NO_ENTERPRISE:
            result = run_async(_with_db(lambda db: list_knowledge_spaces(db, limit=5)))
        self.assertIn("items", result)
        self.assertEqual(result["departments"], [])

    def test_enterprise_agent_page_still_loads(self):
        from service.agent_admin_service import agent_options, list_managed_agents
        with NO_ENTERPRISE:
            agents = run_async(_with_db(list_managed_agents))
            options = run_async(_with_db(lambda db: agent_options(db, self.user["id"])))
        self.assertEqual(agents, [])
        self.assertEqual(options["teams"], [])

    def test_writes_that_need_the_enterprise_still_say_so(self):
        from service.admin_async_service import _scope_fields
        from service.exceptions import NotFound
        with NO_ENTERPRISE, self.assertRaises(NotFound) as caught:
            run_async(_with_db(lambda db: _scope_fields(db, "enterprise", [])))
        self.assertIn("企业尚未初始化", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
