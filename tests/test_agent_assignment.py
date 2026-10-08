"""智能体“统一创建，再划分”：先创建（未划分的草稿），管理员再划分给部门 / 全企业；
划分后绑定的资料对使用者读不到时给出提醒。"""
import unittest
from unittest import mock

from sqlalchemy import text

from models.init_db import (EnterpriseRole, KnowledgeSpace, KnowledgeSpaceDepartment, Organization, OrganizationMember,
                            SessionLocal, Team, TeamMember)
from service.exceptions import Conflict, InvalidInput, NotFound
from tests import _route_client as rc
from tests._async_helpers import run_async as _run

_AVAILABLE, _WHY = rc.route_tests_available()


def _run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def _wrapper():
        async with AsyncSessionLocal() as db:
            return await fn(db)

    return _run(_wrapper())


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class AgentAssignmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = rc.create_user("asg-admin")
        cls.member_a = rc.create_user("asg-member-a")
        db = SessionLocal()
        cls.db = db
        cls.org = Organization(name="asg-org", owner_user_id=cls.admin["id"])
        db.add(cls.org)
        db.commit()
        cls.org_id = cls.org.id
        cls.team_a = Team(name="asg-team-a", owner_user_id=cls.admin["id"], organization_id=cls.org_id)
        cls.team_b = Team(name="asg-team-b", owner_user_id=cls.admin["id"], organization_id=cls.org_id)
        cls.team_sales = Team(name="asg-team-sales", owner_user_id=cls.admin["id"], organization_id=cls.org_id, department_code="sales")
        cls.team_off = Team(name="asg-team-off", owner_user_id=cls.admin["id"], organization_id=cls.org_id, status="disabled")
        db.add_all([cls.team_a, cls.team_b, cls.team_sales, cls.team_off])
        db.commit()
        cls.a, cls.b, cls.sales, cls.off = cls.team_a.id, cls.team_b.id, cls.team_sales.id, cls.team_off.id
        org_role = db.query(EnterpriseRole.id).filter_by(scope="organization", code="member").scalar()
        team_role = db.query(EnterpriseRole.id).filter_by(scope="team", code="member").scalar()
        db.add(OrganizationMember(organization_id=cls.org_id, user_id=cls.member_a["id"], role_id=org_role, status="active"))
        db.commit()
        db.add(TeamMember(team_id=cls.a, user_id=cls.member_a["id"], role_id=team_role, status="active"))
        db.commit()

    @classmethod
    def tearDownClass(cls):
        db = cls.db
        db.execute(text("DELETE FROM agent_knowledge_space WHERE agent_id IN (SELECT id FROM agent WHERE name LIKE 'asg-%')"))
        db.execute(text("DELETE FROM agent WHERE name LIKE 'asg-%'"))
        db.execute(text("DELETE FROM knowledge_spaces WHERE name LIKE 'asg-%'"))
        for team_id in (cls.a, cls.b, cls.sales, cls.off):
            db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": team_id})
            db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": team_id})
        db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org_id})
        db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org_id})
        db.commit()
        db.close()
        rc.cleanup()

    # ---------- 辅助 ----------
    def _svc(self):
        import service.agent_admin_service as svc
        return svc

    def _create(self, name, agent_type="department", **kw):
        with mock.patch("service.organization_admin_service._get_default_organization", mock.AsyncMock(return_value=self.org)):
            return _run_db(lambda db: self._svc().create_managed_agent(db, self.admin["id"], name, agent_type, **kw))

    def _assign(self, agent_id, target, **kw):
        with mock.patch("service.organization_admin_service._get_default_organization", mock.AsyncMock(return_value=self.org)):
            return _run_db(lambda db: self._svc().assign_managed_agent(db, agent_id, self.admin["id"], target, **kw))

    def _row(self, agent_id):
        with SessionLocal() as db:
            return db.execute(text("SELECT agent_type, team_id, department_code, scope_type, organization_id, lifecycle_status, "
                                   "department_publish_key FROM agent WHERE id=:i"), {"i": agent_id}).first()

    def _bind(self, agent_id, space_id):
        with SessionLocal() as db:
            db.execute(text("INSERT INTO agent_knowledge_space (agent_id, space_id, created_at) VALUES (:a,:s,NOW())"),
                       {"a": agent_id, "s": space_id})
            db.commit()

    def _space(self, name, scope_type, team_ids=(), sensitivity="internal"):
        with SessionLocal() as db:
            space = KnowledgeSpace(user_id=self.admin["id"], name=name, scope_type=scope_type, sensitivity=sensitivity,
                                   organization_id=self.org_id if scope_type != "personal" else None)
            db.add(space)
            db.commit()
            for team_id in team_ids:
                db.add(KnowledgeSpaceDepartment(space_id=space.id, team_id=team_id))
            db.commit()
            return space.id

    def _listed(self, agent_id):
        with mock.patch("service.organization_admin_service._get_default_organization", mock.AsyncMock(return_value=self.org)):
            agents = _run_db(lambda db: self._svc().list_managed_agents(db))
        return next(a for a in agents if a["id"] == agent_id)

    # ---------- 先创建，再划分 ----------
    def test_created_agents_start_unassigned(self):
        agent = self._create("asg-fresh")
        self.assertEqual((agent["assignment"], agent["team_id"], agent["lifecycle_status"]), ("unassigned", None, "draft"))
        self.assertEqual(tuple(self._row(agent["id"]))[:4], ("department", None, None, "personal"))

    def test_central_agents_are_enterprise_wide_from_the_start(self):
        agent = self._create("asg-central", "central")
        self.assertEqual(agent["assignment"], "enterprise")

    def test_assign_to_a_department(self):
        agent = self._create("asg-to-dept")
        result = self._assign(agent["id"], "department", team_id=self.a, department_code="hr")
        self.assertEqual((result["assignment"], result["team_id"], result["team_name"]), ("department", self.a, "asg-team-a"))
        row = self._row(agent["id"])
        self.assertEqual((row[0], row[1], row[2], row[3], row[4]), ("department", self.a, "hr", "department", self.org_id))

    def test_department_code_defaults_to_the_departments_business_type(self):
        agent = self._create("asg-default-code")
        result = self._assign(agent["id"], "department", team_id=self.sales)
        self.assertEqual(result["department_code"], "sales")

    def test_business_direction_must_match_the_department(self):
        agent = self._create("asg-mismatch")
        with self.assertRaises(InvalidInput):
            self._assign(agent["id"], "department", team_id=self.sales, department_code="hr")

    def test_assign_to_the_whole_company_and_back(self):
        agent = self._create("asg-wide")
        wide = self._assign(agent["id"], "enterprise")
        self.assertEqual((wide["assignment"], wide["agent_type"], wide["team_id"]), ("enterprise", "central", None))
        self.assertEqual(self._row(agent["id"])[3], "enterprise")
        back = self._assign(agent["id"], "unassigned")
        self.assertEqual((back["assignment"], back["agent_type"]), ("unassigned", "department"))

    def test_moving_between_departments(self):
        agent = self._create("asg-move", team_id=self.a, department_code="hr")
        moved = self._assign(agent["id"], "department", team_id=self.b, department_code="hr")
        self.assertEqual(moved["team_id"], self.b)

    def test_invalid_targets_are_rejected(self):
        agent = self._create("asg-invalid")
        for kwargs in ({"target": "department"}, {"target": "department", "team_id": self.off}, {"target": "department", "team_id": 987654321},
                       {"target": "everyone"}):
            target = kwargs.pop("target")
            with self.assertRaises(InvalidInput, msg=str(kwargs)):
                self._assign(agent["id"], target, **kwargs)
        with self.assertRaises(NotFound):
            self._assign(987654321, "enterprise")

    # ---------- 发布 ----------
    def test_unassigned_agents_cannot_be_published(self):
        agent = self._create("asg-publish-unassigned")
        with self.assertRaises(InvalidInput) as ctx:
            _run_db(lambda db: self._svc().update_managed_agent(db, agent["id"], self.admin["id"], lifecycle_status="published"))
        self.assertIn("划分", str(ctx.exception))

    def test_published_agents_must_be_retired_before_reassigning(self):
        agent = self._create("asg-published", team_id=self.b, department_code="hr")
        _run_db(lambda db: self._svc().update_managed_agent(db, agent["id"], self.admin["id"], lifecycle_status="published"))
        with self.assertRaises(InvalidInput) as ctx:
            self._assign(agent["id"], "department", team_id=self.a, department_code="hr")
        self.assertIn("停用", str(ctx.exception))
        _run_db(lambda db: self._svc().update_managed_agent(db, agent["id"], self.admin["id"], lifecycle_status="retired"))
        self.assertEqual(self._assign(agent["id"], "department", team_id=self.a, department_code="hr")["team_id"], self.a)
        self.assertIsNone(self._row(agent["id"])[6], "划分变化后不保留已发布的唯一键")

    def test_assigning_uses_the_optimistic_lock(self):
        agent = self._create("asg-lock")
        self._assign(agent["id"], "enterprise")
        with self.assertRaises(Conflict):
            self._assign(agent["id"], "unassigned", expected_row_version=agent["row_version"])

    # ---------- 资料提醒 ----------
    def test_warns_when_bound_knowledge_is_not_assigned_to_the_agents_department(self):
        agent = self._create("asg-gap", team_id=self.a, department_code="hr")
        mine = self._space("asg-space-a", "department", [self.a])
        other = self._space("asg-space-b", "department", [self.b])
        loose = self._space("asg-space-loose", "personal")
        company = self._space("asg-space-company", "enterprise")
        secret = self._space("asg-space-secret", "department", [self.a], "restricted")
        for space_id in (mine, other, loose, company, secret):
            self._bind(agent["id"], space_id)
        gaps = {g["name"]: g["reason"] for g in self._listed(agent["id"])["knowledge_gaps"]}
        self.assertEqual(set(gaps), {"asg-space-b", "asg-space-loose", "asg-space-secret"})
        self.assertIn("绝密", gaps["asg-space-secret"])
        self.assertIn("部门", gaps["asg-space-b"])

    def test_assigning_the_knowledge_clears_the_warning(self):
        agent = self._create("asg-gap-fix", team_id=self.a, department_code="hr")
        space = self._space("asg-space-fix", "department", [self.b])
        self._bind(agent["id"], space)
        self.assertEqual(len(self._listed(agent["id"])["knowledge_gaps"]), 1)
        with SessionLocal() as db:
            db.add(KnowledgeSpaceDepartment(space_id=space, team_id=self.a))
            db.commit()
        self.assertEqual(self._listed(agent["id"])["knowledge_gaps"], [])

    def test_company_wide_agents_need_company_wide_knowledge(self):
        agent = self._create("asg-wide-gap", "central")
        in_dept = self._space("asg-wide-dept", "department", [self.a])
        company = self._space("asg-wide-company", "enterprise")
        self._bind(agent["id"], in_dept)
        self._bind(agent["id"], company)
        names = [g["name"] for g in self._listed(agent["id"])["knowledge_gaps"]]
        self.assertEqual(names, ["asg-wide-dept"])

    def test_unassigned_agents_have_no_audience_so_no_warnings(self):
        agent = self._create("asg-no-audience")
        self._bind(agent["id"], self._space("asg-space-x", "personal"))
        self.assertEqual(self._listed(agent["id"])["knowledge_gaps"], [])


if __name__ == "__main__":
    unittest.main()
