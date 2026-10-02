"""部门专业 Agent 自动配置（service/department_agent_service.py）。"""
import unittest
import uuid

import yaml
from sqlalchemy import text

from models.init_db import SessionLocal
from service import department_agent_service as svc
from service.enterprise_agent_templates import TEMPLATES
from service.exceptions import InvalidInput
from tests import _route_client as rc
from tests._async_helpers import run_async
from tests._dept_agent_cleanup import purge_team_agents
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


def run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as db:
            return await fn(db)
    return run_async(go())


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class DepartmentAgentProvisioningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.admin = rc.create_user("da-admin")
        cls.org = _create_org(cls.db, "da-org-" + uuid.uuid4().hex[:6], cls.admin["id"])
        cls.teams = []

    @classmethod
    def tearDownClass(cls):
        purge_team_agents(cls.db, cls.teams)
        if cls.teams:
            ids = ",".join(map(str, cls.teams))
            cls.db.execute(text(f"DELETE FROM team_members WHERE team_id IN ({ids})"))
            cls.db.execute(text(f"DELETE FROM teams WHERE id IN ({ids})"))
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        rc.cleanup()
        cls.db.close()

    def setUp(self):
        self.db.commit()

    def new_team(self, code=None):
        team = _create_team(self.db, self.org, "da-team-" + uuid.uuid4().hex[:6], self.admin["id"])
        if code:
            self.db.execute(text("UPDATE teams SET department_code=:c WHERE id=:t"), {"c": code, "t": team})
            self.db.commit()
        self.teams.append(team)
        status = run_db(lambda db: svc.on_team_saved(db, team, self.admin["id"], created=True))
        return team, status

    def set_team(self, team, **values):
        for column, value in values.items():
            self.db.execute(text(f"UPDATE teams SET {column}=:v WHERE id=:t"), {"v": value, "t": team})
        self.db.commit()

    def agents(self, team):
        self.db.commit()
        return self.db.execute(text(
            "SELECT id, department_code, lifecycle_status FROM agent WHERE team_id=:t ORDER BY id"), {"t": team}).fetchall()

    def skill_tools(self, agent_id):
        import service.skills.loader as loader
        with open(loader._get_yml_path(f"enterprise/agent_{agent_id}.yml"), encoding="utf-8") as f:
            return [t["name"] for t in yaml.safe_load(f)["tools"]]

    def test_team_without_business_type_gets_office_agent_draft(self):
        team, status = self.new_team()
        self.assertEqual((status["state"], status["template_id"]), ("pending_publish", "office"))
        agent_id = status["agent"]["id"]
        self.assertEqual(self.skill_tools(agent_id), TEMPLATES["office"]["tools"])
        from prompt.prompt_manager import read_prompt_file
        self.assertEqual(read_prompt_file(agent_id)["role"], TEMPLATES["office"]["role"])
        self.assertEqual(self.agents(team)[0][1:], (None, "draft"))

    def test_sales_team_gets_crm_agent_with_crm_tools(self):
        team, status = self.new_team("sales")
        self.assertEqual(status["template_id"], "crm")
        self.assertEqual(self.skill_tools(status["agent"]["id"]), TEMPLATES["crm"]["tools"])
        self.assertTrue(status["agent"]["name"].endswith(TEMPLATES["crm"]["name"]))
        self.assertEqual(self.agents(team)[0][1], "sales")

    def test_repair_is_idempotent(self):
        team, _ = self.new_team("hr")
        run_db(lambda db: svc.repair(db, team, self.admin["id"]))
        run_db(lambda db: svc.repair(db, team, self.admin["id"]))
        self.assertEqual(len(self.agents(team)), 1)

    def test_one_click_publish_makes_agent_ready(self):
        team, _ = self.new_team("finance")
        status = run_db(lambda db: svc.publish(db, team, self.admin["id"]))
        self.assertEqual(status["state"], "ready")
        again = run_db(lambda db: svc.publish(db, team, self.admin["id"]))
        self.assertEqual(again["agent"]["id"], status["agent"]["id"])

    def test_changing_business_type_retires_conflicting_agent_and_provisions_new(self):
        team, _ = self.new_team("sales")
        run_db(lambda db: svc.publish(db, team, self.admin["id"]))
        self.set_team(team, department_code="finance")
        status = run_db(lambda db: svc.on_team_saved(db, team, self.admin["id"], department_code_changed=True))
        self.assertEqual((status["state"], status["template_id"]), ("pending_publish", "finance"))
        rows = self.agents(team)
        self.assertEqual([(r[1], r[2]) for r in rows], [("sales", "retired"), ("finance", "draft")])

    def test_disable_retires_and_reenable_reuses_same_agent(self):
        team, status = self.new_team("procurement")
        agent_id = run_db(lambda db: svc.publish(db, team, self.admin["id"]))["agent"]["id"]
        self.set_team(team, status="disabled")
        disabled = run_db(lambda db: svc.on_team_saved(db, team, self.admin["id"], status="disabled"))
        self.assertEqual(disabled["state"], "team_disabled")
        self.assertEqual(self.agents(team)[0][2], "retired")
        with self.assertRaises(InvalidInput):
            run_db(lambda db: svc.repair(db, team, self.admin["id"]))
        self.set_team(team, status="active")
        enabled = run_db(lambda db: svc.on_team_saved(db, team, self.admin["id"], status="active"))
        self.assertEqual((enabled["state"], enabled["agent"]["id"]), ("pending_publish", agent_id))
        self.assertEqual(len(self.agents(team)), 1)

    def test_missing_skill_is_diagnosed_and_repaired(self):
        team, status = self.new_team("hr")
        agent_id = status["agent"]["id"]
        self.db.execute(text("DELETE FROM agent_skill WHERE agent_id=:a"), {"a": agent_id})
        self.db.commit()
        broken = run_db(lambda db: svc.agent_status(db, team))
        self.assertEqual(broken["state"], "needs_repair")
        self.assertTrue(any("专业技能" in i for i in broken["issues"]))
        with self.assertRaises(InvalidInput):
            run_db(lambda db: svc.publish(db, team, self.admin["id"]))
        fixed = run_db(lambda db: svc.repair(db, team, self.admin["id"]))
        self.assertEqual(fixed["state"], "pending_publish")

    def test_central_router_picks_department_agent_of_users_own_team(self):
        from service.runtime.central_router import _find_department_agent_async
        team_a, _ = self.new_team("sales")
        team_b, _ = self.new_team("sales")
        agent_a = run_db(lambda db: svc.publish(db, team_a, self.admin["id"]))["agent"]["id"]
        agent_b = run_db(lambda db: svc.publish(db, team_b, self.admin["id"]))["agent"]["id"]
        member = rc.create_user("da-member")
        _add_org_member(self.db, self.org, member["id"], "member")
        _add_team_member(self.db, team_b, member["id"], "member")
        self.assertNotEqual(agent_a, agent_b)
        self.assertEqual(run_db(lambda db: _find_department_agent_async(db, member["id"], "sales")), agent_b)


if __name__ == "__main__":
    unittest.main()
