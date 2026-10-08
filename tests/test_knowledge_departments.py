"""企业知识库按部门划分：部门空间对本部门成员自动可读，管理和发布只属于部门管理员。

规则（见 models/enterprise_dao.py 的“知识库按部门划分”和 service/access_control.py）：
- scope_type=department 的空间：本部门在职成员自动只读（viewer）；绝密（restricted）不继承；
- 成员资格只给“读”，写 / 管理仍需要空间角色或部门管理员身份；
- 只有该部门的部门管理员能把空间发布到这个部门，也只有他们能调整它的归属和密级；
- 同步和异步两条访问路径都要验证（各有一份实现）。
"""
import unittest

from sqlalchemy import text

from models.init_db import (EnterpriseRole, KnowledgeSpace, Organization, OrganizationMember, SessionLocal,
                            SpaceMember, Team, TeamMember)
from service import access_control
from service.exceptions import InvalidInput, PermissionDenied
from service.knowledge_space import membership, space_async_service as svc
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
class DepartmentKnowledgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = rc.create_user("kd-admin")
        cls.member = rc.create_user("kd-member")
        cls.other_member = rc.create_user("kd-other-member")      # 另一个部门的成员
        cls.outsider = rc.create_user("kd-outsider")
        cls.left = rc.create_user("kd-left")                      # 已被移出部门

        db = SessionLocal()
        cls.db = db
        org = Organization(name="kd-test-org", owner_user_id=cls.admin["id"])
        db.add(org)
        db.commit()
        cls.org_id = org.id
        cls.team = Team(name="kd-team-a", owner_user_id=cls.admin["id"], organization_id=cls.org_id)
        cls.team_b = Team(name="kd-team-b", owner_user_id=cls.admin["id"], organization_id=cls.org_id)
        db.add_all([cls.team, cls.team_b])
        db.commit()
        cls.team_id, cls.team_b_id = cls.team.id, cls.team_b.id
        org_role = db.query(EnterpriseRole.id).filter_by(scope="organization", code="member").scalar()
        admin_role = db.query(EnterpriseRole.id).filter_by(scope="team", code="admin").scalar()
        member_role = db.query(EnterpriseRole.id).filter_by(scope="team", code="member").scalar()
        for user in (cls.admin, cls.member, cls.other_member, cls.left):
            db.add(OrganizationMember(organization_id=cls.org_id, user_id=user["id"], role_id=org_role, status="active"))
        db.commit()
        db.add_all([
            TeamMember(team_id=cls.team_id, user_id=cls.admin["id"], role_id=admin_role, status="active"),
            TeamMember(team_id=cls.team_id, user_id=cls.member["id"], role_id=member_role, status="active"),
            TeamMember(team_id=cls.team_b_id, user_id=cls.other_member["id"], role_id=member_role, status="active"),
            TeamMember(team_id=cls.team_id, user_id=cls.left["id"], role_id=member_role, status="disabled"),
        ])
        db.commit()

        def make(name, **kw):
            space = KnowledgeSpace(user_id=cls.admin["id"], name=name, **kw)
            db.add(space)
            db.commit()
            return space.id

        cls.dept_space = make("kd-dept", team_id=cls.team_id, organization_id=cls.org_id, scope_type="department", sensitivity="internal")
        cls.restricted_space = make("kd-restricted", team_id=cls.team_id, organization_id=cls.org_id, scope_type="department", sensitivity="restricted")
        cls.personal_with_team = make("kd-personal", team_id=cls.team_id, scope_type="personal")
        cls.explicit_member_space = make("kd-explicit", team_id=cls.team_id, organization_id=cls.org_id, scope_type="department", sensitivity="restricted")
        db.add(SpaceMember(space_id=cls.explicit_member_space, user_id=cls.member["id"], role="viewer"))
        db.commit()

    @classmethod
    def tearDownClass(cls):
        db = cls.db
        db.execute(text("DELETE FROM kb_audit_log WHERE space_id IN (SELECT id FROM knowledge_spaces WHERE name LIKE 'kd-%')"))
        db.execute(text("DELETE FROM space_members WHERE space_id IN (SELECT id FROM knowledge_spaces WHERE name LIKE 'kd-%')"))
        db.execute(text("DELETE FROM knowledge_spaces WHERE name LIKE 'kd-%'"))
        db.execute(text("DELETE FROM team_members WHERE team_id IN (:a,:b)"), {"a": cls.team_id, "b": cls.team_b_id})
        db.execute(text("DELETE FROM teams WHERE id IN (:a,:b)"), {"a": cls.team_id, "b": cls.team_b_id})
        db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org_id})
        db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org_id})
        db.commit()
        db.close()
        rc.cleanup()

    @staticmethod
    def can_read(user, space_id):
        with SessionLocal() as db:
            return access_control.get_owned_space(db, user["id"], space_id) is not None

    @staticmethod
    def row(sql, params):
        with SessionLocal() as db:
            return db.execute(text(sql), params).first()

    # ---------------- 访问规则（同步） ----------------
    def test_members_read_department_spaces_automatically(self):
        self.assertIsNotNone(access_control.get_owned_space(self.db, self.member["id"], self.dept_space))
        self.assertEqual(access_control.get_space_role(self.db, self.member["id"], self.dept_space), "viewer")
        self.assertIn(self.dept_space, access_control.user_space_ids(self.db, self.member["id"]))

    def test_membership_gives_read_only(self):
        role = access_control.get_space_role(self.db, self.member["id"], self.dept_space)
        self.assertFalse(membership.can_write_doc(role))
        self.assertFalse(membership.can_manage_space(role))
        self.assertFalse(membership.can_delete_space(role))

    def test_team_admin_manages_it(self):
        self.assertEqual(access_control.get_space_role(self.db, self.admin["id"], self.dept_space), "owner")
        self.assertIn(self.dept_space, access_control.user_space_ids(self.db, self.admin["id"]))

    def test_restricted_does_not_inherit_membership(self):
        self.assertIsNone(access_control.get_owned_space(self.db, self.member["id"], self.restricted_space))
        self.assertIsNone(access_control.get_space_role(self.db, self.member["id"], self.restricted_space))
        self.assertNotIn(self.restricted_space, access_control.user_space_ids(self.db, self.member["id"]))

    def test_restricted_is_still_reachable_by_explicit_membership(self):
        self.assertEqual(access_control.get_space_role(self.db, self.member["id"], self.explicit_member_space), "viewer")

    def test_other_departments_and_outsiders_get_nothing(self):
        for user in (self.other_member, self.outsider, self.left):
            self.assertIsNone(access_control.get_owned_space(self.db, user["id"], self.dept_space), user)
            self.assertNotIn(self.dept_space, access_control.user_space_ids(self.db, user["id"]))

    def test_a_space_that_is_not_published_to_the_department_stays_private(self):
        """有 team_id 但 scope_type 还是 personal（旧数据）：不因为部门成员身份就开放。"""
        self.assertIsNone(access_control.get_owned_space(self.db, self.member["id"], self.personal_with_team))

    # ---------------- 访问规则（异步，另一份实现） ----------------
    def test_async_paths_agree_with_sync(self):
        async def check(db):
            member, outsider = self.member["id"], self.outsider["id"]
            return {
                "read": await access_control.get_owned_space_async(db, member, self.dept_space) is not None,
                "role": await access_control.get_space_role_async(db, member, self.dept_space),
                "ids": await access_control.user_space_ids_async(db, member),
                "restricted": await access_control.get_owned_space_async(db, member, self.restricted_space),
                "restricted_role": await access_control.get_space_role_async(db, member, self.restricted_space),
                "outsider": await access_control.get_owned_space_async(db, outsider, self.dept_space),
                "personal": await access_control.get_owned_space_async(db, member, self.personal_with_team),
            }
        got = _run_db(check)
        self.assertTrue(got["read"])
        self.assertEqual(got["role"], "viewer")
        self.assertIn(self.dept_space, got["ids"])
        self.assertNotIn(self.restricted_space, got["ids"])
        self.assertIsNone(got["restricted"])
        self.assertIsNone(got["restricted_role"])
        self.assertIsNone(got["outsider"])
        self.assertIsNone(got["personal"])

    # ---------------- 列表 ----------------
    def test_list_shows_department_spaces_with_their_department(self):
        listing = _run_db(lambda db: svc.list_spaces(db, self.member["id"]))
        by_name = {item["name"]: item for item in listing["items"]}
        self.assertIn("kd-dept", by_name)
        item = by_name["kd-dept"]
        self.assertEqual((item["scope"], item["team_name"], item["my_role"], item["can_write_doc"]), ("department", "kd-team-a", "viewer", False))
        self.assertEqual(item["sensitivity_label"], "内部")
        self.assertNotIn("kd-restricted", by_name)
        self.assertNotIn("kd-personal", by_name)
        self.assertEqual(listing["publishable_departments"], [], "普通成员不能发布到部门")

    def test_list_offers_only_the_departments_the_user_administers(self):
        listing = _run_db(lambda db: svc.list_spaces(db, self.admin["id"]))
        self.assertEqual([d["id"] for d in listing["publishable_departments"]], [self.team_id])
        self.assertEqual([s["key"] for s in listing["sensitivities"]], ["public", "internal", "confidential", "restricted"])

    # ---------------- 发布 / 调整 ----------------
    def test_only_the_department_admin_can_publish_to_a_department(self):
        created = _run_db(lambda db: svc.create_space(db, self.admin["id"], {"name": "kd-created", "team_id": self.team_id, "sensitivity": "confidential"}))
        self.assertEqual((created["scope"], created["team_name"], created["sensitivity"]), ("department", "kd-team-a", "confidential"))
        row = self.row("SELECT scope_type, team_id, organization_id FROM knowledge_spaces WHERE id=:i", {"i": created["id"]})
        self.assertEqual(tuple(row), ("department", self.team_id, self.org_id))
        for user in (self.member, self.outsider, self.other_member):
            with self.assertRaises(PermissionDenied, msg=user):
                _run_db(lambda db: svc.create_space(db, user["id"], {"name": "kd-nope", "team_id": self.team_id}))
        with self.assertRaises(PermissionDenied):
            _run_db(lambda db: svc.create_space(db, self.admin["id"], {"name": "kd-nope", "team_id": self.team_b_id}))   # 管理员只能发布到自己管理的部门

    def test_invalid_sensitivity_is_rejected_and_default_is_internal(self):
        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.create_space(db, self.member["id"], {"name": "kd-bad", "sensitivity": "top-secret"}))
        plain = _run_db(lambda db: svc.create_space(db, self.member["id"], {"name": "kd-plain"}))
        self.assertEqual((plain["scope"], plain["sensitivity"], plain["team_id"]), ("personal", "internal", None))

    def test_owner_who_is_not_department_admin_cannot_move_or_reclassify_a_department_space(self):
        # 把一个部门空间的所有权交给普通成员（模拟“成员创建、后来才被发布到部门”之类的历史数据）
        self.db.execute(text("UPDATE knowledge_spaces SET user_id=:u WHERE id=:s"), {"u": self.member["id"], "s": self.dept_space})
        self.db.commit()
        try:
            with self.assertRaises(PermissionDenied):
                _run_db(lambda db: svc.update_space(db, self.member["id"], self.dept_space, {"team_id": None}))
            with self.assertRaises(PermissionDenied):
                _run_db(lambda db: svc.update_space(db, self.member["id"], self.dept_space, {"sensitivity": "public"}))
        finally:
            self.db.execute(text("UPDATE knowledge_spaces SET user_id=:u WHERE id=:s"), {"u": self.admin["id"], "s": self.dept_space})
            self.db.commit()

    def test_department_admin_can_unshare_and_reshare(self):
        space = _run_db(lambda db: svc.create_space(db, self.admin["id"], {"name": "kd-toggle", "team_id": self.team_id}))
        back = _run_db(lambda db: svc.update_space(db, self.admin["id"], space["id"], {"team_id": None}))
        self.assertEqual((back["scope"], back["team_id"]), ("personal", None))
        self.assertFalse(self.can_read(self.member, space["id"]), "收回后成员立即失去访问")
        again = _run_db(lambda db: svc.update_space(db, self.admin["id"], space["id"], {"team_id": self.team_id}))
        self.assertEqual(again["scope"], "department")
        self.assertTrue(self.can_read(self.member, space["id"]))

    def test_raising_sensitivity_to_restricted_takes_access_away_from_members(self):
        space = _run_db(lambda db: svc.create_space(db, self.admin["id"], {"name": "kd-raise", "team_id": self.team_id}))
        self.assertTrue(self.can_read(self.member, space["id"]))
        _run_db(lambda db: svc.update_space(db, self.admin["id"], space["id"], {"sensitivity": "restricted"}))
        self.assertFalse(self.can_read(self.member, space["id"]))

    def test_department_member_can_read_but_not_write_through_the_service(self):
        with self.assertRaises(PermissionDenied):
            _run_db(lambda db: svc.update_space(db, self.member["id"], self.dept_space, {"name": "hijack"}))


if __name__ == "__main__":
    unittest.main()
