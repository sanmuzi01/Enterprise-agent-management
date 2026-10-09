"""数据体检（service/data_health.py）：故意造出几类真实碰到过的脏数据，体检要能查出来；
--fix 只修“只补数据、不需要人判断”的几类，并写审计；需要人判断的保持原样只报告。

也覆盖产品里的修复：删除企业助手时连同它专属的技能记录和配置文件一起删掉（以前只解绑，留下孤儿）。
"""
import os
import pathlib
import unittest
from unittest import mock

from sqlalchemy import text

from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _codes(findings):
    return {f.code: f for f in findings}


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class DataHealthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from models.init_db import SessionLocal
        from service.enterprise_bootstrap import ensure_default_enterprise
        cls.SessionLocal = SessionLocal
        cls.owner = rc.create_user("dh-owner")
        with SessionLocal() as db:   # 空库（CI）上先把企业建出来，体检的大部分检查以企业为准
            result = ensure_default_enterprise(db)
        cls.created_enterprise = result["organization_id"] if result["status"] == "created" else None
        with SessionLocal() as db:
            cls.org = db.execute(text("SELECT id FROM organizations ORDER BY id LIMIT 1")).scalar()
        cls.cleanup_sql = []
        cls.cleanup_files = []

    @classmethod
    def tearDownClass(cls):
        with cls.SessionLocal() as db:
            for sql, params in cls.cleanup_sql:
                db.execute(text(sql), params)
            if cls.created_enterprise:
                db.execute(text("DELETE FROM organization_members WHERE organization_id = :o"), {"o": cls.created_enterprise})
                db.execute(text("UPDATE knowledge_spaces SET organization_id = NULL WHERE organization_id = :o"), {"o": cls.created_enterprise})
                db.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": cls.created_enterprise})
            db.commit()
        for path in cls.cleanup_files:
            pathlib.Path(path).unlink(missing_ok=True)
        rc.cleanup()

    def run_checks(self):
        from service import data_health
        with self.SessionLocal() as db:
            return data_health.run_checks(db)

    def test_finds_each_kind_of_dirty_data(self):
        from models.init_db import Agent, KnowledgeSpace, Team
        loner = rc.create_user("dh-loner")   # 没加入企业的在用账号
        with self.SessionLocal() as db:
            team = Team(name="dh-停用部门", owner_user_id=self.owner["id"], organization_id=self.org, status="disabled")
            db.add(team)
            db.flush()
            agent = Agent(user_id=self.owner["id"], name="dh-还在发布的助手", agent_type="department", team_id=team.id,
                          organization_id=self.org, lifecycle_status="published", model_name="glm-4")
            space = KnowledgeSpace(user_id=self.owner["id"], name="dh-无主空间", organization_id=None)
            db.add_all([agent, space])
            db.commit()
            ids = {"team": team.id, "agent": agent.id, "space": space.id}
        type(self).cleanup_sql += [("DELETE FROM agent WHERE id = :i", {"i": ids["agent"]}),
                                   ("DELETE FROM knowledge_spaces WHERE id = :i", {"i": ids["space"]}),
                                   ("DELETE FROM teams WHERE id = :i", {"i": ids["team"]})]
        orphan_file = ROOT / "skills" / "enterprise" / "agent_987654321.yml"
        orphan_file.parent.mkdir(parents=True, exist_ok=True)
        orphan_file.write_text("name: x\n", encoding="utf-8")
        type(self).cleanup_files.append(str(orphan_file))

        with mock.patch("service.data_health.SAMPLE_LIMIT", 10_000):   # 开发库里本来就有别的条目，放开样例数才能断言到这次造的
            found = _codes(self.run_checks())
        self.assertIn(f"#{loner['id']} {loner['name']}", found["users_not_in_enterprise"].samples)
        self.assertTrue(found["users_not_in_enterprise"].fixable)
        self.assertEqual(found["published_agents_on_disabled_teams"].severity, "error")
        self.assertTrue(any("dh-还在发布的助手" in s for s in found["published_agents_on_disabled_teams"].samples))
        self.assertFalse(found["published_agents_on_disabled_teams"].fixable, "停用部门的助手怎么办要人判断")
        self.assertTrue(found["spaces_without_enterprise"].fixable)
        self.assertIn("skills/enterprise/agent_987654321.yml", found["orphan_skill_configs"].samples)
        self.assertIn(f"#{loner['id']} {loner['name']}", found["test_accounts_left"].samples, "rt_ 开头的测试账号要被提示")

    def test_fix_only_fills_in_data_and_is_audited(self):
        from service import data_health
        loner = rc.create_user("dh-fixme")
        with self.SessionLocal() as db:
            from models.init_db import KnowledgeSpace
            space = KnowledgeSpace(user_id=self.owner["id"], name="dh-待挂企业", organization_id=None)
            db.add(space)
            db.commit()
            space_id = space.id
        type(self).cleanup_sql.append(("DELETE FROM knowledge_spaces WHERE id = :i", {"i": space_id}))
        with self.SessionLocal() as db, mock.patch("service.audit_service.record") as audit:
            fixed = data_health.apply_fixes(db, data_health.run_checks(db), operator_id=self.owner["id"])
        self.assertGreaterEqual(fixed.get("users_not_in_enterprise", 0), 1)
        self.assertGreaterEqual(fixed.get("spaces_without_enterprise", 0), 1)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[:2], (self.owner["id"], "data_health.fixed"))
        with self.SessionLocal() as db:
            member = db.execute(text("SELECT status FROM organization_members WHERE organization_id = :o AND user_id = :u"),
                                {"o": self.org, "u": loner["id"]}).scalar()
            space_org = db.execute(text("SELECT organization_id FROM knowledge_spaces WHERE id = :i"), {"i": space_id}).scalar()
        self.assertEqual((member, space_org), ("active", self.org))
        after = _codes(self.run_checks())
        self.assertNotIn("users_not_in_enterprise", after)
        self.assertNotIn("spaces_without_enterprise", after)

    def test_multiple_enterprises_is_reported_not_fixed(self):
        from service import data_health
        with self.SessionLocal() as db:
            db.execute(text("INSERT INTO organizations (name, owner_user_id, status, created_at) VALUES ('dh-多余企业', :u, 'active', NOW())"),
                       {"u": self.owner["id"]})
            extra = db.execute(text("SELECT MAX(id) FROM organizations")).scalar()
            db.commit()
        try:
            found = _codes(self.run_checks())
            self.assertEqual(found["enterprise_multiple"].severity, "error")
            self.assertFalse(found["enterprise_multiple"].fixable, "保留哪家企业要人判断")
            with self.SessionLocal() as db:
                data_health.apply_fixes(db, list(found.values()))
                self.assertEqual(db.execute(text("SELECT COUNT(*) FROM organizations WHERE id = :i"), {"i": extra}).scalar(), 1)
        finally:
            with self.SessionLocal() as db:
                db.execute(text("DELETE FROM organizations WHERE id = :i"), {"i": extra})
                db.commit()

    def test_cli_exit_code_reflects_errors(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("data_health_check", ROOT / "scripts" / "data_health_check.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        from service.data_health import Finding
        with mock.patch("service.data_health.run_checks", return_value=[Finding("x", "x", "warn", 1)]), mock.patch("builtins.print"):
            self.assertEqual(cli.main([]), 0)
        with mock.patch("service.data_health.run_checks", return_value=[Finding("x", "x", "error", 1)]), mock.patch("builtins.print"):
            self.assertEqual(cli.main([]), 1, "有严重问题时退出码为 1，定时任务据此告警")


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class DeleteAgentRemovesPrivateSkillTest(unittest.TestCase):
    def test_deleting_an_enterprise_agent_removes_its_private_skill_and_file(self):
        from models.init_db import Agent, SessionLocal
        from service import agent_admin_service, agent_service
        from service.enterprise_agent_templates import get_template
        from service.skills import loader as skill_loader
        owner = rc.create_user("dh-agentowner")
        try:
            with SessionLocal() as db:
                agent = Agent(user_id=owner["id"], name="dh-待删助手", agent_type="department", model_name="glm-4")
                db.add(agent)
                db.commit()
                agent_id = agent.id
            from tests._async_helpers import run_async
            from models.async_db import AsyncSessionLocal

            async def _bind():
                async with AsyncSessionLocal() as adb:
                    a = await adb.get(Agent, agent_id)
                    await agent_admin_service.bind_template_skill(adb, a, get_template("finance"), owner["id"])
                    await adb.commit()
            run_async(_bind())
            path = skill_loader._get_yml_path(f"enterprise/agent_{agent_id}.yml")
            self.assertTrue(os.path.exists(path))

            with SessionLocal() as db:
                from models.init_db import User
                agent_service.delete(db, db.get(User, owner["id"]), agent_id)
                left = db.execute(text("SELECT COUNT(*) FROM skill WHERE config_file = :f"),
                                  {"f": f"enterprise/agent_{agent_id}.yml"}).scalar()
            self.assertEqual(left, 0, "专属技能记录要一起删掉")
            self.assertFalse(os.path.exists(path), "专属技能配置文件要一起删掉")
        finally:
            rc.cleanup()


if __name__ == "__main__":
    unittest.main()
