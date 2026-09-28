"""中央/部门 Agent 管理后台服务（service/agent_admin_service.py）的回归测试。

补的是"中央 Agent 管理部门 Agent"目标下最后一块缺口：之前只能直接改数据库才能
建出一个真正的中央/部门 Agent。见 docs/enterprise-rbac-plan.md 第20节。跟
tests/test_organization_admin_service.py 同一套写法：直接调 service 函数。
"""
import unittest

from sqlalchemy import text

from models.init_db import SessionLocal
from service.exceptions import Conflict, InvalidInput, NotFound
from tests import _route_client as rc
from tests._async_helpers import run_async as _run
from tests.test_enterprise_access import _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


def _run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def _wrapper():
        async with AsyncSessionLocal() as db:
            return await fn(db)

    return _run(_wrapper())


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class ManagedAgentCrudTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.admin = rc.create_user("agentadmin-owner")
        cls.org_id = _create_org(cls.db, "agentadmin-org", cls.admin["id"])
        cls.team_id = _create_team(cls.db, cls.org_id, "agentadmin-team", cls.admin["id"])

    @classmethod
    def tearDownClass(cls):
        cls.db.execute(text("DELETE FROM agent WHERE name LIKE 'aa-test-%'"))
        cls.db.execute(text("DELETE FROM teams WHERE id=:i"), {"i": cls.team_id})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": cls.org_id})
        cls.db.commit()
        rc.cleanup()
        cls.db.close()

    def _default_org_is_our_org(self):
        # service.organization_admin_service._get_default_organization 取的是
        # "id 最小的那个 Organization"——不一定是本测试建的这个。只有当它确实是
        # 本测试建的这个（比如本机就一个默认企业）时，department Agent 的创建
        # 才能拿到本测试建的 team_id。用这个 helper 判断要不要 skip。
        import service.organization_admin_service as org_svc

        async def _check(db):
            org = await org_svc._get_default_organization(db)
            return org.id == self.org_id

        return _run_db(_check)

    def test_create_central_agent(self):
        import service.agent_admin_service as svc

        created = _run_db(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-test-central", "central",
        ))
        self.assertEqual(created["agent_type"], "central")
        self.assertEqual(created["lifecycle_status"], "draft")
        self.assertIsNone(created["team_id"])

    def test_create_agent_invalid_type_raises(self):
        import service.agent_admin_service as svc

        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.create_managed_agent(db, self.admin["id"], "aa-test-bad", "personal"))

    def test_create_department_agent_without_team_id_raises(self):
        import service.agent_admin_service as svc

        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.create_managed_agent(
                db, self.admin["id"], "aa-test-noteam", "department", department_code="hr",
            ))

    def test_create_department_agent_invalid_department_code_raises(self):
        import service.agent_admin_service as svc

        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.create_managed_agent(
                db, self.admin["id"], "aa-test-badcode", "department",
                department_code="not_a_real_department", team_id=1,
            ))

    def test_create_department_agent_nonexistent_team_raises_not_found(self):
        import service.agent_admin_service as svc

        with self.assertRaises(NotFound):
            _run_db(lambda db: svc.create_managed_agent(
                db, self.admin["id"], "aa-test-noteam2", "department",
                department_code="hr", team_id=999_999_999,
            ))

    def test_create_and_list_and_update_department_agent(self):
        if not self._default_org_is_our_org():
            self.skipTest("本机默认企业不是这个测试建的那个，department_code 场景需要真实默认企业环境")
        import service.agent_admin_service as svc

        created = _run_db(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-test-hrdept", "department",
            department_code="hr", team_id=self.team_id, role="HR助手",
        ))
        self.assertEqual(created["agent_type"], "department")
        self.assertEqual(created["department_code"], "hr")
        self.assertEqual(created["team_id"], self.team_id)
        self.assertEqual(created["team_name"], "agentadmin-team")
        agent_id = created["id"]

        listed = _run_db(lambda db: svc.list_managed_agents(db))
        self.assertIn(agent_id, [a["id"] for a in listed])

        published = _run_db(lambda db: svc.update_managed_agent(
                db, agent_id, self.admin["id"],
                lifecycle_status="published",
        ))
        self.assertEqual(published["lifecycle_status"], "published")
        self.assertEqual(published["row_version"], 1)

        from prompt.prompt_manager import read_prompt_file
        prompt_data = read_prompt_file(agent_id)
        self.assertEqual(prompt_data["role"], "HR助手")

        # 只改 task，不该把 role 冲掉（update_prompt_file 是整份覆盖写，
        # service 层必须先读旧值再合并）。
        _run_db(lambda db: svc.update_managed_agent(
                db, agent_id, self.admin["id"],
                task="处理请假审批"))
        prompt_data2 = read_prompt_file(agent_id)
        self.assertEqual(prompt_data2["role"], "HR助手")
        self.assertEqual(prompt_data2["task"], "处理请假审批")

    def test_update_nonexistent_agent_raises_not_found(self):
        import service.agent_admin_service as svc

        with self.assertRaises(NotFound):
            _run_db(lambda db: svc.update_managed_agent(
                db, 999_999_999, self.admin["id"],
                name="x"))

    def test_optimistic_lock_stale_version_raises_conflict(self):
        import service.agent_admin_service as svc

        created = _run_db(lambda db: svc.create_managed_agent(db, self.admin["id"], "aa-test-lock", "central"))
        agent_id = created["id"]

        _run_db(lambda db: svc.update_managed_agent(
                db, agent_id, self.admin["id"],
                expected_row_version=0, name="aa-test-lock-2"))
        with self.assertRaises(Conflict):
            _run_db(lambda db: svc.update_managed_agent(
                db, agent_id, self.admin["id"],
                expected_row_version=0, name="aa-test-lock-stale",
            ))

    def test_prompt_only_edit_bumps_row_version_and_respects_optimistic_lock(self):
        # P1：之前只有 name/model_name/lifecycle_status/department_code/team_id
        # 这些 DB 字段变化才会碰 row_version，只改 role/task/constraints/output
        # （Prompt 文件）完全不会递增/比对——两个管理员并发改同一个 Agent 的
        # Prompt 会互相覆盖都不知道。
        import service.agent_admin_service as svc

        created = _run_db(lambda db: svc.create_managed_agent(db, self.admin["id"], "aa-test-promptver", "central"))
        agent_id = created["id"]
        self.assertEqual(created["row_version"], 0)

        updated = _run_db(lambda db: svc.update_managed_agent(
            db, agent_id, self.admin["id"], expected_row_version=0, task="第一次改任务",
        ))
        self.assertEqual(updated["row_version"], 1)

        with self.assertRaises(Conflict):
            _run_db(lambda db: svc.update_managed_agent(
                db, agent_id, self.admin["id"], expected_row_version=0, task="第二次改任务，过期版本",
            ))

    def test_invalid_lifecycle_status_raises_invalid_input(self):
        import service.agent_admin_service as svc

        created = _run_db(lambda db: svc.create_managed_agent(db, self.admin["id"], "aa-test-badstatus", "central"))
        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.update_managed_agent(
                db, created["id"], self.admin["id"],
                lifecycle_status="not_a_real_status"))

    def test_create_writes_audit_event(self):
        import service.agent_admin_service as svc
        from models import audit_dao

        created = _run_db(lambda db: svc.create_managed_agent(db, self.admin["id"], "aa-test-audit-create", "central"))
        events = _run_db(lambda db: audit_dao.list_by_resource_async(db, "agent", created["id"]))
        self.assertIn("org.managed_agent_created", [e.action for e in events])

    def test_publish_writes_audit_event_with_specific_action_name(self):
        import service.agent_admin_service as svc
        from models import audit_dao

        created = _run_db(lambda db: svc.create_managed_agent(db, self.admin["id"], "aa-test-audit-publish", "central"))
        _run_db(lambda db: svc.update_managed_agent(
            db, created["id"], self.admin["id"], lifecycle_status="published",
        ))
        events = _run_db(lambda db: audit_dao.list_by_resource_async(db, "agent", created["id"]))
        actions = [e.action for e in events]
        # 发布这个动作专门用带状态名的 action（org.managed_agent_published），
        # 不是笼统的 org.managed_agent_updated——审计记录里一眼能看出"谁在什么时候
        # 发布了这个 Agent"，不用再去 detail 字段里找。
        self.assertIn("org.managed_agent_published", actions)


if __name__ == "__main__":
    unittest.main()
