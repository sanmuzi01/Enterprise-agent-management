"""管理后台“企业知识库”：统一创建，再划分给部门 / 全企业；按部门筛选；部门清单。"""
import unittest
from unittest import mock

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
        cls.member_a = rc.create_user("asd-member-a")
        cls.member_b = rc.create_user("asd-member-b")
        db = SessionLocal()
        cls.db = db
        cls.org = Organization(name="asd-org", owner_user_id=cls.admin["id"])
        cls.other_org = Organization(name="asd-other-org", owner_user_id=cls.admin["id"])
        db.add_all([cls.org, cls.other_org])
        db.commit()
        cls.org_id, cls.other_org_id = cls.org.id, cls.other_org.id
        cls.team_a = Team(name="asd-team-a", owner_user_id=cls.admin["id"], organization_id=cls.org_id)
        cls.team_b = Team(name="asd-team-b", owner_user_id=cls.admin["id"], organization_id=cls.org_id)
        cls.team_off = Team(name="asd-team-off", owner_user_id=cls.admin["id"], organization_id=cls.org_id, status="disabled")
        cls.team_x = Team(name="asd-team-x", owner_user_id=cls.admin["id"], organization_id=cls.other_org_id)
        db.add_all([cls.team_a, cls.team_b, cls.team_off, cls.team_x])
        db.commit()
        cls.a, cls.b, cls.off, cls.x = cls.team_a.id, cls.team_b.id, cls.team_off.id, cls.team_x.id
        org_role = db.query(EnterpriseRole.id).filter_by(scope="organization", code="member").scalar()
        team_role = db.query(EnterpriseRole.id).filter_by(scope="team", code="member").scalar()
        for user in (cls.member_a, cls.member_b):
            db.add(OrganizationMember(organization_id=cls.org_id, user_id=user["id"], role_id=org_role, status="active"))
        db.commit()
        db.add(TeamMember(team_id=cls.a, user_id=cls.member_a["id"], role_id=team_role, status="active"))
        db.add(TeamMember(team_id=cls.b, user_id=cls.member_b["id"], role_id=team_role, status="active"))
        db.commit()

    @classmethod
    def tearDownClass(cls):
        db = cls.db
        db.execute(text("DELETE FROM kb_audit_log WHERE space_id IN (SELECT id FROM knowledge_spaces WHERE name LIKE 'asd-%')"))
        db.execute(text("DELETE FROM knowledge_spaces WHERE name LIKE 'asd-%'"))
        for team_id in (cls.a, cls.b, cls.off, cls.x):
            db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": team_id})
            db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": team_id})
        db.execute(text("DELETE FROM organization_members WHERE organization_id IN (:a,:b)"), {"a": cls.org_id, "b": cls.other_org_id})
        db.execute(text("DELETE FROM organizations WHERE id IN (:a,:b)"), {"a": cls.org_id, "b": cls.other_org_id})
        db.commit()
        db.close()
        rc.cleanup()

    # ---------- 辅助 ----------
    def _create(self, name, **kw):
        # “全企业”划分到本测试企业，而不是本机恰好 id 最小的那个企业
        with mock.patch("service.organization_admin_service._get_default_organization", mock.AsyncMock(return_value=self.org)):
            return _run_db(lambda db: svc.admin_create_space(db, self.admin["id"], {"name": name, **kw}))

    def _update(self, space_id, patch):
        with mock.patch("service.organization_admin_service._get_default_organization", mock.AsyncMock(return_value=self.org)):
            return _run_db(lambda db: svc.admin_update_space(db, self.admin["id"], space_id, patch))

    def _list(self, scope=None, limit=500):
        with mock.patch("service.organization_admin_service._get_default_organization", mock.AsyncMock(return_value=self.org)):
            return _run_db(lambda db: svc.list_knowledge_spaces(db, limit=limit, scope=scope))

    @staticmethod
    def _names(page):
        return {item["name"] for item in page["items"] if item["name"].startswith("asd-")}

    @staticmethod
    def _can_read(user, space_id):
        with SessionLocal() as db:
            return access_control.get_owned_space(db, user["id"], space_id) is not None

    @staticmethod
    def _grants(space_id):
        with SessionLocal() as db:
            return sorted(r[0] for r in db.execute(text("SELECT team_id FROM knowledge_space_departments WHERE space_id=:i"), {"i": space_id}).all())

    # ---------- 统一创建，再划分 ----------
    def test_created_spaces_start_unassigned_and_private(self):
        created = self._create("asd-fresh")
        self.assertEqual((created["scope_type"], created["team_ids"]), ("personal", []))
        self.assertFalse(self._can_read(self.member_a, created["id"]))

    def test_assigning_to_several_departments(self):
        created = self._create("asd-multi")
        result = self._update(created["id"], {"scope": "departments", "team_ids": [self.a, self.b]})
        self.assertEqual(result["scope_type"], "department")
        self.assertEqual(sorted(d["id"] for d in result["departments"]), sorted([self.a, self.b]))
        self.assertEqual(self._grants(created["id"]), sorted([self.a, self.b]))
        self.assertTrue(self._can_read(self.member_a, created["id"]))
        self.assertTrue(self._can_read(self.member_b, created["id"]))

    def test_reassigning_replaces_the_previous_departments(self):
        created = self._create("asd-replace", scope="departments", team_ids=[self.a, self.b])
        self.assertEqual(self._grants(created["id"]), sorted([self.a, self.b]))
        self._update(created["id"], {"scope": "departments", "team_ids": [self.b]})
        self.assertEqual(self._grants(created["id"]), [self.b])
        self.assertFalse(self._can_read(self.member_a, created["id"]), "被拿掉的部门立即失去访问")
        self.assertTrue(self._can_read(self.member_b, created["id"]))

    def test_assigning_to_the_whole_company_and_taking_it_back(self):
        created = self._create("asd-company", scope="departments", team_ids=[self.a])
        result = self._update(created["id"], {"scope": "enterprise"})
        self.assertEqual((result["scope_type"], result["departments"]), ("enterprise", []))
        self.assertEqual(self._grants(created["id"]), [], "改成全企业后不再保留部门划分")
        self.assertTrue(self._can_read(self.member_b, created["id"]))
        back = self._update(created["id"], {"scope": "unassigned"})
        self.assertEqual(back["scope_type"], "personal")
        self.assertFalse(self._can_read(self.member_b, created["id"]))

    def test_creating_with_a_scope_in_one_step(self):
        created = self._create("asd-onestep", scope="departments", team_ids=[self.a], sensitivity="confidential")
        self.assertEqual((created["scope_type"], created["team_ids"], created["sensitivity"]), ("department", [self.a], "confidential"))
        self.assertTrue(self._can_read(self.member_a, created["id"]))

    def test_raising_to_restricted_takes_access_away_from_members(self):
        created = self._create("asd-secret", scope="departments", team_ids=[self.a])
        self.assertTrue(self._can_read(self.member_a, created["id"]))
        self._update(created["id"], {"sensitivity": "restricted"})
        self.assertFalse(self._can_read(self.member_a, created["id"]))
        self.assertEqual(self._grants(created["id"]), [self.a], "划分记录保留，降级后可恢复")

    def test_legacy_department_manager_link_is_cleared_when_the_department_is_no_longer_included(self):
        created = self._create("asd-legacy", scope="departments", team_ids=[self.a])
        self.db.execute(text("UPDATE knowledge_spaces SET team_id=:t WHERE id=:i"), {"t": self.a, "i": created["id"]})
        self.db.commit()
        self._update(created["id"], {"scope": "departments", "team_ids": [self.b]})
        with SessionLocal() as db:
            self.assertIsNone(db.execute(text("SELECT team_id FROM knowledge_spaces WHERE id=:i"), {"i": created["id"]}).scalar())

    # ---------- 校验 ----------
    def test_invalid_assignments_are_rejected(self):
        space = self._create("asd-valid")
        for patch in (
            {"scope": "departments", "team_ids": []},                      # 没选部门
            {"scope": "departments", "team_ids": [987654321]},             # 不存在
            {"scope": "departments", "team_ids": [self.off]},              # 已停用
            {"scope": "departments", "team_ids": [self.x]},                # 别的企业的部门
            {"scope": "everyone"},                                         # 不认识的划分方式
            {"sensitivity": "top-secret"},
        ):
            with self.assertRaises(InvalidInput, msg=str(patch)):
                self._update(space["id"], patch)
        with self.assertRaises(NotFound):
            self._update(987654321, {"scope": "unassigned"})
        with self.assertRaises(InvalidInput):
            self._create("asd-bad", scope="departments", team_ids=[])

    # ---------- 列表与部门清单 ----------
    def test_listing_filters(self):
        in_a = self._create("asd-list-a", scope="departments", team_ids=[self.a])
        both = self._create("asd-list-both", scope="departments", team_ids=[self.a, self.b])
        company = self._create("asd-list-company", scope="enterprise")
        loose = self._create("asd-list-loose")
        mine = {"asd-list-a", "asd-list-both", "asd-list-company", "asd-list-loose"}
        self.assertEqual(self._names(self._list(f"team:{self.a}")) & mine, {"asd-list-a", "asd-list-both"})
        self.assertEqual(self._names(self._list(f"team:{self.b}")) & mine, {"asd-list-both"})
        self.assertEqual(self._names(self._list("enterprise")) & mine, {"asd-list-company"})
        self.assertEqual(self._names(self._list("unassigned")) & mine, {"asd-list-loose"})
        self.assertEqual(self._names(self._list(None)) & mine, mine)
        shown = {item["name"]: item for item in self._list(None)["items"]}
        self.assertEqual(sorted(d["name"] for d in shown["asd-list-both"]["departments"]), ["asd-team-a", "asd-team-b"])
        del in_a, both, company, loose

    def test_total_follows_the_filter(self):
        self._create("asd-total-1", scope="departments", team_ids=[self.b])
        self._create("asd-total-2", scope="departments", team_ids=[self.b])
        page = self._list(f"team:{self.b}")
        self.assertEqual(page["total"], len(page["items"]))
        self.assertGreaterEqual(page["total"], 2)

    def test_overview_lists_active_departments_even_without_spaces(self):
        self._create("asd-overview", scope="departments", team_ids=[self.a])
        page = self._list(None, limit=1)
        counts = {d["name"]: d["space_count"] for d in page["departments"]}
        self.assertGreaterEqual(counts["asd-team-a"], 1)
        self.assertEqual(counts["asd-team-b"], len(self._list(f"team:{self.b}")["items"]))
        self.assertNotIn("asd-team-off", counts, "已停用的部门不出现在清单里")
        self.assertNotIn("asd-team-x", counts, "平台只服务一个企业：别的企业的部门不出现")
        self.assertTrue(all(set(d) == {"id", "name", "space_count"} for d in page["departments"]), "清单里不带企业信息")

    def test_overview_counts_add_up(self):
        self._create("asd-sum-dept", scope="departments", team_ids=[self.a])
        self._create("asd-sum-company", scope="enterprise")
        self._create("asd-sum-loose")
        page = self._list(None, limit=1)
        with SessionLocal() as db:
            by_scope = dict(db.execute(text("SELECT scope_type, COUNT(*) FROM knowledge_spaces GROUP BY scope_type")).all())
        self.assertEqual(page["all_count"], sum(by_scope.values()))
        self.assertEqual((page["enterprise_count"], page["unassigned_count"]), (by_scope.get("enterprise", 0), by_scope.get("personal", 0)))
        with self.assertRaises(InvalidInput):
            self._list("team:abc")


if __name__ == "__main__":
    unittest.main()
