"""管理后台“企业知识库”按部门划分：按部门筛选、部门清单（含还没有空间的部门）、管理员新建 / 调整归属与密级。"""
import unittest

from sqlalchemy import text

from models.init_db import (EnterpriseRole, Organization, OrganizationMember, SessionLocal, Team, TeamMember)
from service import access_control, admin_async_service as svc
from service.exceptions import InvalidInput, NotFound
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
class AdminSpaceDepartmentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = rc.create_user("asd-admin")
        cls.member = rc.create_user("asd-member")
        db = SessionLocal()
        cls.db = db
        org = Organization(name="asd-org", owner_user_id=cls.admin["id"])
        db.add(org)
        db.commit()
        cls.org_id = org.id
        cls.team_a = Team(name="asd-team-a", owner_user_id=cls.admin["id"], organization_id=cls.org_id)
        cls.team_b = Team(name="asd-team-b", owner_user_id=cls.admin["id"], organization_id=cls.org_id)
        cls.team_off = Team(name="asd-team-off", owner_user_id=cls.admin["id"], organization_id=cls.org_id, status="disabled")
        db.add_all([cls.team_a, cls.team_b, cls.team_off])
        db.commit()
        cls.a, cls.b, cls.off = cls.team_a.id, cls.team_b.id, cls.team_off.id
        org_role = db.query(EnterpriseRole.id).filter_by(scope="organization", code="member").scalar()
        team_role = db.query(EnterpriseRole.id).filter_by(scope="team", code="member").scalar()
        db.add(OrganizationMember(organization_id=cls.org_id, user_id=cls.member["id"], role_id=org_role, status="active"))
        db.commit()
        db.add(TeamMember(team_id=cls.a, user_id=cls.member["id"], role_id=team_role, status="active"))
        db.commit()

    @classmethod
    def tearDownClass(cls):
        db = cls.db
        db.execute(text("DELETE FROM kb_audit_log WHERE space_id IN (SELECT id FROM knowledge_spaces WHERE name LIKE 'asd-%')"))
        db.execute(text("DELETE FROM knowledge_spaces WHERE name LIKE 'asd-%'"))
        db.execute(text("DELETE FROM team_members WHERE team_id IN (:a,:b,:c)"), {"a": cls.a, "b": cls.b, "c": cls.off})
        db.execute(text("DELETE FROM teams WHERE id IN (:a,:b,:c)"), {"a": cls.a, "b": cls.b, "c": cls.off})
        db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org_id})
        db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org_id})
        db.commit()
        db.close()
        rc.cleanup()

    def _create(self, name, **kw):
        return _run_db(lambda db: svc.admin_create_space(db, self.admin["id"], {"name": name, **kw}))

    def _list(self, scope=None, limit=500):
        return _run_db(lambda db: svc.list_knowledge_spaces(db, limit=limit, scope=scope))

    @staticmethod
    def _names(page):
        return {item["name"] for item in page["items"] if item["name"].startswith("asd-")}

    def test_admin_creates_a_department_space_and_members_can_read_it(self):
        created = self._create("asd-dept-space", team_id=self.a, sensitivity="confidential")
        self.assertEqual((created["team_id"], created["sensitivity"]), (self.a, "confidential"))
        with SessionLocal() as db:
            self.assertIsNotNone(access_control.get_owned_space(db, self.member["id"], created["id"]))
            row = db.execute(text("SELECT scope_type, organization_id FROM knowledge_spaces WHERE id=:i"), {"i": created["id"]}).first()
        self.assertEqual(tuple(row), ("department", self.org_id))

    def test_listing_filters_by_department_and_personal(self):
        self._create("asd-in-a", team_id=self.a)
        self._create("asd-in-b", team_id=self.b)
        self._create("asd-personal")
        self.assertEqual(self._names(self._list(f"team:{self.a}")) & {"asd-in-a", "asd-in-b", "asd-personal"}, {"asd-in-a"})
        self.assertEqual(self._names(self._list(f"team:{self.b}")) & {"asd-in-a", "asd-in-b", "asd-personal"}, {"asd-in-b"})
        self.assertEqual(self._names(self._list("personal")) & {"asd-in-a", "asd-in-b", "asd-personal"}, {"asd-personal"})
        everything = self._names(self._list(None)) & {"asd-in-a", "asd-in-b", "asd-personal"}
        self.assertEqual(everything, {"asd-in-a", "asd-in-b", "asd-personal"})

    def test_overview_lists_every_active_department_even_without_spaces(self):
        self._create("asd-overview", team_id=self.a)
        page = self._list(None, limit=1)
        counts = {d["name"]: d["space_count"] for d in page["departments"]}
        self.assertGreaterEqual(counts["asd-team-a"], 1)
        self.assertEqual(counts["asd-team-b"], len(self._list(f"team:{self.b}")["items"]),
                         "还没有空间的部门也要出现在清单里，数量与该部门的列表一致")
        self.assertNotIn("asd-team-off", counts, "已停用的部门不出现在清单里")

    def test_overview_counts_add_up(self):
        self._create("asd-sum-dept", team_id=self.a)
        self._create("asd-sum-personal")
        page = self._list(None, limit=1)
        with SessionLocal() as db:
            department_spaces = db.execute(text("SELECT COUNT(*) FROM knowledge_spaces WHERE scope_type='department'")).scalar()
        self.assertEqual(page["all_count"], page["personal_count"] + department_spaces)

    def test_total_follows_the_filter(self):
        self._create("asd-total-1", team_id=self.b)
        self._create("asd-total-2", team_id=self.b)
        page = self._list(f"team:{self.b}")
        self.assertEqual(page["total"], len(page["items"]))
        self.assertGreaterEqual(page["total"], 2)

    def test_admin_moves_a_space_between_departments_and_back_to_personal(self):
        space = self._create("asd-mover", team_id=self.a)
        moved = _run_db(lambda db: svc.admin_update_space(db, self.admin["id"], space["id"], {"team_id": self.b, "sensitivity": "public"}))
        self.assertEqual((moved["team_id"], moved["scope_type"], moved["sensitivity"]), (self.b, "department", "public"))
        with SessionLocal() as db:
            self.assertIsNone(access_control.get_owned_space(db, self.member["id"], space["id"]), "换到别的部门后原部门成员不再可读")
        back = _run_db(lambda db: svc.admin_update_space(db, self.admin["id"], space["id"], {"team_id": None}))
        self.assertEqual((back["team_id"], back["scope_type"]), (None, "personal"))

    def test_restricted_in_a_department_is_not_readable_by_members(self):
        space = self._create("asd-secret", team_id=self.a, sensitivity="restricted")
        with SessionLocal() as db:
            self.assertIsNone(access_control.get_owned_space(db, self.member["id"], space["id"]))

    def test_invalid_input_is_rejected(self):
        with self.assertRaises(InvalidInput):
            self._create("asd-bad-team", team_id=987654321)
        with self.assertRaises(InvalidInput):
            self._create("asd-off-team", team_id=self.off)
        with self.assertRaises(InvalidInput):
            self._create("asd-bad-level", sensitivity="top-secret")
        with self.assertRaises(InvalidInput):
            self._list("team:abc")
        space = self._create("asd-valid")
        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.admin_update_space(db, self.admin["id"], space["id"], {"team_id": self.off}))
        with self.assertRaises(NotFound):
            _run_db(lambda db: svc.admin_update_space(db, self.admin["id"], 987654321, {"team_id": self.a}))


if __name__ == "__main__":
    unittest.main()
