"""中央/部门 Agent 管理后台服务（service/agent_admin_service.py）的回归测试。

补的是"中央 Agent 管理部门 Agent"目标下最后一块缺口：之前只能直接改数据库才能
建出一个真正的中央/部门 Agent。见 docs/enterprise-rbac-plan.md 第20节。跟
tests/test_organization_admin_service.py 同一套写法：直接调 service 函数。
"""
import os
import unittest
from unittest.mock import AsyncMock, patch

import yaml
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

    def test_create_department_agent_without_team_id_is_created_unassigned(self):
        """统一创建、再划分：不选部门也能创建，先是未划分的草稿；划分见 tests/test_agent_assignment.py。"""
        import service.agent_admin_service as svc

        created = _run_db(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-test-noteam", "department", department_code="hr",
        ))
        self.assertEqual((created["assignment"], created["team_id"], created["lifecycle_status"]), ("unassigned", None, "draft"))

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

    def test_prompt_file_restored_when_db_commit_fails(self):
        """第五轮审计 P1-7：Prompt 文件和 DB 是两个不同的存储，没法用一个事务
        同时保证两边都成功。file 先原子替换、DB 最后才 commit——commit 失败时
        必须把文件写回编辑前的内容，不能留下一个内容比 DB 记录的版本还新的
        文件（否则下次 build_prompt 读到的是从没被数据库确认过的内容）。"""
        import service.agent_admin_service as svc
        from prompt.prompt_manager import read_prompt_file

        created = _run_db(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-test-restore", "central",
            role="旧角色", task="旧任务", constraints="旧约束", output="旧输出",
        ))
        agent_id = created["id"]

        async def _do():
            from models.async_db import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                with patch.object(db, "commit", AsyncMock(side_effect=RuntimeError("模拟提交失败"))):
                    with self.assertRaises(RuntimeError):
                        await svc.update_managed_agent(db, agent_id, self.admin["id"], role="新角色")
        _run(_do())

        prompt_data = read_prompt_file(agent_id)
        self.assertEqual(prompt_data["role"], "旧角色")
        self.assertEqual(prompt_data["task"], "旧任务")

        # DB 那一侧的 row_version 也确实没被提交上去（跟文件恢复成旧内容一致）。
        check_db = SessionLocal()
        try:
            from models.init_db import Agent
            row = check_db.get(Agent, agent_id)
            self.assertEqual(row.row_version, 0)
        finally:
            check_db.close()

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


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class DepartmentPublishUniquenessTest(unittest.TestCase):
    """同一个部门（team）同时只能有一个 published 的部门 Agent；不同部门可以各自发布
    同一业务方向的 Agent。直接 patch `_get_default_organization_id` 指向本测试建的
    专属企业，任何机器上都能跑，不用 skip。"""

    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.admin = rc.create_user("aa-deptpub")
        cls.org_id = _create_org(cls.db, "aa-deptpub-org", cls.admin["id"])
        cls.team_id = _create_team(cls.db, cls.org_id, "aa-deptpub-team", cls.admin["id"])
        cls.team2_id = _create_team(cls.db, cls.org_id, "aa-deptpub-team2", cls.admin["id"])

    @classmethod
    def tearDownClass(cls):
        cls.db.execute(text("DELETE FROM agent WHERE name LIKE 'aa-dp-%'"))
        cls.db.execute(text("DELETE FROM teams WHERE id IN (:a, :b)"), {"a": cls.team_id, "b": cls.team2_id})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": cls.org_id})
        cls.db.commit()
        rc.cleanup()
        cls.db.close()

    def tearDown(self):
        # 每个测试方法自己清干净，不依赖 unittest 的方法执行顺序——不然一个
        # 测试方法故意留下的 published Agent 会跟另一个测试方法用的
        # department_code 打架。
        self.db.execute(text("DELETE FROM agent WHERE name LIKE 'aa-dp-%'"))
        self.db.commit()

    def _run_with_org(self, fn):
        from unittest.mock import AsyncMock, patch
        import service.agent_admin_service as svc

        async def _wrapper():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                with patch.object(svc, "_get_default_organization_id", AsyncMock(return_value=self.org_id)):
                    return await fn(db)

        return _run(_wrapper())

    def test_publishing_second_agent_with_same_department_code_is_rejected(self):
        import service.agent_admin_service as svc

        first = self._run_with_org(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-dp-first", "department", department_code="hr", team_id=self.team_id,
        ))
        second = self._run_with_org(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-dp-second", "department", department_code="hr", team_id=self.team_id,
        ))

        published_first = self._run_with_org(lambda db: svc.update_managed_agent(
            db, first["id"], self.admin["id"], lifecycle_status="published",
        ))
        self.assertEqual(published_first["lifecycle_status"], "published")

        with self.assertRaises(InvalidInput):
            self._run_with_org(lambda db: svc.update_managed_agent(
                db, second["id"], self.admin["id"], lifecycle_status="published",
            ))

        # 第一个退役之后，第二个才能发布——不是"这个部门永远只能有一个"，而是
        # "同时只能有一个"。
        self._run_with_org(lambda db: svc.update_managed_agent(
            db, first["id"], self.admin["id"], lifecycle_status="retired",
        ))
        published_second = self._run_with_org(lambda db: svc.update_managed_agent(
            db, second["id"], self.admin["id"], lifecycle_status="published",
        ))
        self.assertEqual(published_second["lifecycle_status"], "published")

    def test_same_department_code_in_two_teams_can_both_be_published(self):
        # 销售一部、销售二部各自发布自己的 CRM Agent。
        import service.agent_admin_service as svc

        first = self._run_with_org(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-dp-sales1", "department", department_code="sales", team_id=self.team_id,
        ))
        second = self._run_with_org(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-dp-sales2", "department", department_code="sales", team_id=self.team2_id,
        ))
        for agent in (first, second):
            result = self._run_with_org(lambda db: svc.update_managed_agent(
                db, agent["id"], self.admin["id"], lifecycle_status="published",
            ))
            self.assertEqual(result["lifecycle_status"], "published")

    def test_different_codes_in_same_team_conflict(self):
        import service.agent_admin_service as svc

        finance_agent = self._run_with_org(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-dp-finance", "department", department_code="finance", team_id=self.team2_id,
        ))
        sales_agent = self._run_with_org(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-dp-other", "department", department_code="procurement", team_id=self.team2_id,
        ))
        self._run_with_org(lambda db: svc.update_managed_agent(
            db, finance_agent["id"], self.admin["id"], lifecycle_status="published",
        ))
        with self.assertRaises(InvalidInput):
            self._run_with_org(lambda db: svc.update_managed_agent(
                db, sales_agent["id"], self.admin["id"], lifecycle_status="published",
            ))

    def test_publish_rejects_agent_not_matching_team_business_type(self):
        import service.agent_admin_service as svc

        self.db.execute(text("UPDATE teams SET department_code='sales' WHERE id=:t"), {"t": self.team_id})
        self.db.commit()
        try:
            agent = self._run_with_org(lambda db: svc.create_managed_agent(
                db, self.admin["id"], "aa-dp-wrongtype", "department", department_code="finance", team_id=self.team_id,
            ))
            with self.assertRaises(InvalidInput):
                self._run_with_org(lambda db: svc.update_managed_agent(
                    db, agent["id"], self.admin["id"], lifecycle_status="published",
                ))
        finally:
            self.db.execute(text("UPDATE teams SET department_code=NULL WHERE id=:t"), {"t": self.team_id})
            self.db.commit()

    def test_non_chat_models_are_rejected_when_creating(self):
        import service.agent_admin_service as svc

        with self.assertRaises(InvalidInput):
            self._run_with_org(lambda db: svc.create_managed_agent(
                db, self.admin["id"], "aa-dp-badmodel-create", "department", department_code="hr", team_id=self.team2_id,
                model_name="BAAI/bge-small-zh-v1.5",
            ))

    def test_publish_rejects_non_chat_model(self):
        """创建 / 编辑时已经拦了；这里是库里本来就留着旧的非聊天模型的情况（发布前再拦一次）。"""
        import service.agent_admin_service as svc

        agent = self._run_with_org(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-dp-badmodel", "department", department_code="hr", team_id=self.team2_id,
        ))
        self.db.execute(text("UPDATE agent SET model_name='BAAI/bge-small-zh-v1.5' WHERE id=:i"), {"i": agent["id"]})
        self.db.commit()
        with self.assertRaises(InvalidInput):
            self._run_with_org(lambda db: svc.update_managed_agent(
                db, agent["id"], self.admin["id"], lifecycle_status="published",
            ))


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class ManagedAgentTemplateTest(unittest.TestCase):
    """部门工作台里程碑1新增的 `template_id` 支持（service/enterprise_agent_templates.py）：
    用企业预置模板（比如 "oa"）一步建出带 Prompt、带专属 Skill 绑定的部门/中央 Agent，
    管理员不用手写 role/task/constraints，也不用单独再去配工具。跟
    DepartmentPublishUniquenessTest 用同一个 `_run_with_org` 模式，但用不同的 name 前缀
    （aa-tpl- 而不是 aa-dp-）避免两个测试类的 tearDown 互相清错对方的数据。"""

    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.admin = rc.create_user("aa-tpl")
        cls.org_id = _create_org(cls.db, "aa-tpl-org", cls.admin["id"])
        cls.team_id = _create_team(cls.db, cls.org_id, "aa-tpl-team", cls.admin["id"])

    @classmethod
    def tearDownClass(cls):
        cls.db.execute(text("DELETE FROM agent WHERE name LIKE 'aa-tpl-%'"))
        cls.db.execute(text("DELETE FROM teams WHERE id=:i"), {"i": cls.team_id})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": cls.org_id})
        cls.db.commit()
        rc.cleanup()
        cls.db.close()

    def setUp(self):
        # self.db 是整个测试类共享、从 setUpClass 就开始存活的同步 session——MySQL
        # 默认隔离级别（REPEATABLE READ）下，它的普通 SELECT 用的是自己事务开始那
        # 一刻的快照，看不到 `_run_with_org` 里另开的 AsyncSessionLocal 随后提交的
        # 新数据。每个测试方法开始前先 commit 一次（没有待提交的写操作，纯粹是为了
        # 结束旧事务、让下一次查询重新开一个事务），不然本方法自己创建的数据，
        # 本方法自己拿 self.db 验证的时候都可能查不到（试出来的真实坑，不是猜的）。
        self.db.commit()

    def tearDown(self):
        # 用模板建的 Agent 会关联一条 Skill 记录（agent_skill 外键表），必须先删
        # agent_skill 关联、再删 skill 本身，最后才能删 agent——顺序反了会撞
        # `agent_skill_ibfk_1` 外键约束，MySQL 直接拒绝删除还被引用的父行。磁盘上
        # 的 skills/enterprise/agent_<id>.yml 同理要跟着清掉，不然一直留在文件系统里。
        self.db.commit()
        import service.skills.loader as skill_loader

        rows = self.db.execute(
            text("SELECT id FROM agent WHERE name LIKE 'aa-tpl-%'")
        ).fetchall()
        agent_ids = [r[0] for r in rows]
        if agent_ids:
            ids_sql = ",".join(str(i) for i in agent_ids)
            self.db.execute(text(f"DELETE FROM agent_skill WHERE agent_id IN ({ids_sql})"))
            self.db.execute(text(
                "DELETE FROM skill WHERE user_id=:u AND config_file LIKE 'enterprise/agent_%.yml'"
            ), {"u": self.admin["id"]})
            for agent_id in agent_ids:
                config_path = skill_loader._get_yml_path(f"enterprise/agent_{agent_id}.yml")
                if os.path.exists(config_path):
                    os.remove(config_path)
        self.db.execute(text("DELETE FROM agent WHERE name LIKE 'aa-tpl-%'"))
        self.db.commit()

    def _run_with_org(self, fn):
        from unittest.mock import AsyncMock, patch
        import service.agent_admin_service as svc

        async def _wrapper():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                with patch.object(svc, "_get_default_organization_id", AsyncMock(return_value=self.org_id)):
                    return await fn(db)

        return _run(_wrapper())

    def test_oa_template_fills_prompt_and_binds_skill_with_its_tools(self):
        import service.agent_admin_service as svc
        from service.enterprise_agent_templates import TEMPLATES

        created = self._run_with_org(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-tpl-oa", "department",
            department_code="hr", team_id=self.team_id, template_id="oa",
        ))

        self.assertEqual(created["prompt"]["role"], TEMPLATES["oa"]["role"])
        self.assertEqual(created["prompt"]["task"], TEMPLATES["oa"]["task"])

        skill_row = self.db.execute(
            text("SELECT id, config_file FROM skill WHERE config_file=:f"),
            {"f": f"enterprise/agent_{created['id']}.yml"},
        ).fetchone()
        self.assertIsNotNone(skill_row, "模板应该给新建的 Agent 生成一个专属 Skill 记录")
        linked = self.db.execute(
            text("SELECT 1 FROM agent_skill WHERE agent_id=:a AND skill_id=:s"),
            {"a": created["id"], "s": skill_row.id},
        ).fetchone()
        self.assertIsNotNone(linked, "生成的 Skill 必须真的关联到这个 Agent 上，不能只是插了一行孤儿记录")

        import service.skills.loader as skill_loader
        config_path = skill_loader._get_yml_path(f"enterprise/agent_{created['id']}.yml")
        with open(config_path, encoding="utf-8") as f:
            written = yaml.safe_load(f)
        self.assertEqual(
            [t["name"] for t in written["tools"]], TEMPLATES["oa"]["tools"],
            "落盘的 Skill 配置里的工具列表要跟模板定义的完全一致（包括这次新增的两个只读列表工具）",
        )

    def test_template_department_code_mismatch_is_rejected(self):
        import service.agent_admin_service as svc

        with self.assertRaises(InvalidInput):
            self._run_with_org(lambda db: svc.create_managed_agent(
                db, self.admin["id"], "aa-tpl-mismatch", "department",
                department_code="procurement", team_id=self.team_id, template_id="oa",
            ))

    def test_unknown_template_id_is_rejected(self):
        import service.agent_admin_service as svc

        with self.assertRaises(InvalidInput):
            self._run_with_org(lambda db: svc.create_managed_agent(
                db, self.admin["id"], "aa-tpl-unknown", "department",
                department_code="hr", team_id=self.team_id, template_id="not-a-real-template",
            ))

    def test_explicit_role_overrides_template_default(self):
        # 模板只负责"没填的时候兜底"，用户自己填了 role/task 应该原样保留，
        # 不能被模板悄悄覆盖掉。
        import service.agent_admin_service as svc

        created = self._run_with_org(lambda db: svc.create_managed_agent(
            db, self.admin["id"], "aa-tpl-override", "department",
            department_code="hr", team_id=self.team_id, template_id="oa",
            role="自定义角色描述",
        ))
        self.assertEqual(created["prompt"]["role"], "自定义角色描述")


if __name__ == "__main__":
    unittest.main()
