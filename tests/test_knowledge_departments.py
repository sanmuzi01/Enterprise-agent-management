"""企业知识库“统一创建、再划分”：管理员把知识库划分给一个或多个部门，或划分给全企业。

规则（models/enterprise_dao.py 的“知识库按部门划分”、service/access_control.py）：
- 划分给部门（scope_type=department，knowledge_space_departments）：被划分到的部门的在职成员自动只读，部门负责人可编辑文档；
- 划分给全企业（scope_type=enterprise）：该企业全部在职成员只读；
- 还没划分（scope_type=personal）：只有所有者和被加入的成员；
- “绝密”不继承任何部门 / 全企业身份；
- 成员资格只给“读”（负责人给“编辑”），改设置 / 管成员 / 删空间不会因此获得；
- 同步和异步两条访问路径各验证一遍（各有一份实现）。
"""
import unittest

from sqlalchemy import text

from models.init_db import (EnterpriseRole, KnowledgeSpace, KnowledgeSpaceDepartment, Organization, OrganizationMember,
                            SessionLocal, SpaceMember, Team, TeamMember)
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
        cls.owner = rc.create_user("kd-owner")                # 创建者（管理员），空间所有者
        cls.member_a = rc.create_user("kd-member-a")
        cls.head_a = rc.create_user("kd-head-a")              # A 部门负责人
        cls.member_b = rc.create_user("kd-member-b")
        cls.member_c = rc.create_user("kd-member-c")          # 只在 C 部门
        cls.org_only = rc.create_user("kd-org-only")          # 是企业成员，但不在任何部门
        cls.outsider = rc.create_user("kd-outsider")          # 不在任何企业
        cls.left = rc.create_user("kd-left")                  # 已被移出 A 部门

        db = SessionLocal()
        cls.db = db
        org = Organization(name="kd-test-org", owner_user_id=cls.owner["id"])
        db.add(org)
        db.commit()
        cls.org_id = org.id
        cls.teams = {}
        for key in ("a", "b", "c"):
            team = Team(name=f"kd-team-{key}", owner_user_id=cls.owner["id"], organization_id=cls.org_id)
            db.add(team)
            db.commit()
            cls.teams[key] = team.id
        org_role = db.query(EnterpriseRole.id).filter_by(scope="organization", code="member").scalar()
        admin_role = db.query(EnterpriseRole.id).filter_by(scope="team", code="admin").scalar()
        member_role = db.query(EnterpriseRole.id).filter_by(scope="team", code="member").scalar()
        for user in (cls.owner, cls.member_a, cls.head_a, cls.member_b, cls.member_c, cls.org_only, cls.left):
            db.add(OrganizationMember(organization_id=cls.org_id, user_id=user["id"], role_id=org_role, status="active"))
        db.commit()
        db.add_all([
            TeamMember(team_id=cls.teams["a"], user_id=cls.member_a["id"], role_id=member_role, status="active"),
            TeamMember(team_id=cls.teams["a"], user_id=cls.head_a["id"], role_id=admin_role, status="active"),
            TeamMember(team_id=cls.teams["b"], user_id=cls.member_b["id"], role_id=member_role, status="active"),
            TeamMember(team_id=cls.teams["c"], user_id=cls.member_c["id"], role_id=member_role, status="active"),
            TeamMember(team_id=cls.teams["a"], user_id=cls.left["id"], role_id=member_role, status="disabled"),
        ])
        db.commit()

        def make(name, scope_type, sensitivity="internal", team_ids=(), **kw):
            space = KnowledgeSpace(user_id=cls.owner["id"], name=name, scope_type=scope_type, sensitivity=sensitivity,
                                   organization_id=cls.org_id if scope_type != "personal" else None, **kw)
            db.add(space)
            db.commit()
            for team_id in team_ids:
                db.add(KnowledgeSpaceDepartment(space_id=space.id, team_id=team_id))
            db.commit()
            return space.id

        a, b = cls.teams["a"], cls.teams["b"]
        cls.only_a = make("kd-only-a", "department", team_ids=[a])
        cls.a_and_b = make("kd-a-and-b", "department", team_ids=[a, b])
        cls.restricted = make("kd-restricted", "department", "restricted", [a])
        cls.whole_company = make("kd-company", "enterprise")
        cls.restricted_company = make("kd-company-secret", "enterprise", "restricted")
        cls.unassigned = make("kd-unassigned", "personal")
        cls.stale_grant = make("kd-stale", "personal", team_ids=[a])    # 授权记录还在，但空间已经收回成未划分
        cls.explicit = make("kd-explicit", "department", "restricted", [a])
        db.add(SpaceMember(space_id=cls.explicit, user_id=cls.member_a["id"], role="viewer"))
        db.commit()

    @classmethod
    def tearDownClass(cls):
        db = cls.db
        ids = "SELECT id FROM knowledge_spaces WHERE name LIKE 'kd-%'"
        db.execute(text(f"DELETE FROM kb_audit_log WHERE space_id IN ({ids})"))
        db.execute(text(f"DELETE FROM space_members WHERE space_id IN ({ids})"))
        db.execute(text("DELETE FROM knowledge_spaces WHERE name LIKE 'kd-%'"))
        for team_id in cls.teams.values():
            db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": team_id})
            db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": team_id})
        db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org_id})
        db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org_id})
        db.commit()
        db.close()
        rc.cleanup()

    @staticmethod
    def role(user, space_id):
        with SessionLocal() as db:
            return access_control.get_space_role(db, user["id"], space_id)

    @staticmethod
    def can_read(user, space_id):
        with SessionLocal() as db:
            return access_control.get_owned_space(db, user["id"], space_id) is not None

    @staticmethod
    def ids(user):
        with SessionLocal() as db:
            return access_control.user_space_ids(db, user["id"])

    # ---------------- 划分给部门 ----------------
    def test_members_of_the_assigned_department_read_automatically_but_cannot_write(self):
        self.assertEqual(self.role(self.member_a, self.only_a), "viewer")
        self.assertTrue(self.can_read(self.member_a, self.only_a))
        self.assertIn(self.only_a, self.ids(self.member_a))
        role = self.role(self.member_a, self.only_a)
        self.assertFalse(membership.can_write_doc(role))
        self.assertFalse(membership.can_manage_space(role))
        self.assertFalse(membership.can_delete_space(role))

    def test_department_head_can_edit_documents_but_not_manage_the_space(self):
        role = self.role(self.head_a, self.only_a)
        self.assertEqual(role, "editor")
        self.assertTrue(membership.can_write_doc(role))
        self.assertFalse(membership.can_manage_space(role))
        self.assertFalse(membership.can_manage_members(role))

    def test_a_space_assigned_to_several_departments_is_readable_by_each(self):
        for user in (self.member_a, self.member_b):
            self.assertTrue(self.can_read(user, self.a_and_b), user)
        self.assertFalse(self.can_read(self.member_c, self.a_and_b), "没被划分到的部门看不到")

    def test_other_departments_and_people_outside_get_nothing(self):
        for user in (self.member_b, self.member_c, self.org_only, self.outsider, self.left):
            self.assertFalse(self.can_read(user, self.only_a), user)
            self.assertNotIn(self.only_a, self.ids(user))

    def test_owner_keeps_full_control(self):
        self.assertEqual(self.role(self.owner, self.only_a), "owner")

    # ---------------- 划分给全企业 ----------------
    def test_enterprise_wide_space_is_readable_by_every_member_of_the_company(self):
        for user in (self.member_a, self.member_b, self.member_c, self.org_only):
            self.assertEqual(self.role(user, self.whole_company), "viewer", user)
            self.assertIn(self.whole_company, self.ids(user))

    def test_enterprise_wide_space_is_not_readable_outside_the_company(self):
        self.assertFalse(self.can_read(self.outsider, self.whole_company))

    # ---------------- 绝密 / 未划分 ----------------
    def test_restricted_never_inherits(self):
        for space in (self.restricted, self.restricted_company):
            self.assertFalse(self.can_read(self.member_a, space))
            self.assertIsNone(self.role(self.member_a, space))
            self.assertNotIn(space, self.ids(self.member_a))
        self.assertFalse(self.can_read(self.head_a, self.restricted), "绝密连部门负责人也不继承")

    def test_restricted_is_still_reachable_by_explicit_membership(self):
        self.assertEqual(self.role(self.member_a, self.explicit), "viewer")

    def test_unassigned_spaces_stay_private_even_if_a_stale_grant_row_exists(self):
        self.assertFalse(self.can_read(self.member_a, self.unassigned))
        self.assertFalse(self.can_read(self.member_a, self.stale_grant), "scope_type 不是部门时，授权记录不生效")

    def test_the_highest_source_wins(self):
        """显式是 viewer、又是被划分部门的负责人（editor）：取更高的 editor。"""
        with SessionLocal() as db:
            db.add(SpaceMember(space_id=self.only_a, user_id=self.head_a["id"], role="viewer"))
            db.commit()
        try:
            self.assertEqual(self.role(self.head_a, self.only_a), "editor")
        finally:
            with SessionLocal() as db:
                db.execute(text("DELETE FROM space_members WHERE space_id=:s AND user_id=:u"), {"s": self.only_a, "u": self.head_a["id"]})
                db.commit()

    # ---------------- 异步路径（另一份实现） ----------------
    def test_async_paths_agree_with_sync(self):
        async def check(db):
            return {
                "dept_read": await access_control.get_owned_space_async(db, self.member_a["id"], self.only_a) is not None,
                "dept_role": await access_control.get_space_role_async(db, self.member_a["id"], self.only_a),
                "head_role": await access_control.get_space_role_async(db, self.head_a["id"], self.only_a),
                "multi_b": await access_control.get_owned_space_async(db, self.member_b["id"], self.a_and_b) is not None,
                "multi_c": await access_control.get_owned_space_async(db, self.member_c["id"], self.a_and_b),
                "company": await access_control.get_space_role_async(db, self.org_only["id"], self.whole_company),
                "company_outsider": await access_control.get_owned_space_async(db, self.outsider["id"], self.whole_company),
                "restricted": await access_control.get_owned_space_async(db, self.member_a["id"], self.restricted),
                "unassigned": await access_control.get_owned_space_async(db, self.member_a["id"], self.unassigned),
                "stale": await access_control.get_owned_space_async(db, self.member_a["id"], self.stale_grant),
                "ids": await access_control.user_space_ids_async(db, self.member_a["id"]),
            }
        got = _run_db(check)
        self.assertTrue(got["dept_read"])
        self.assertEqual((got["dept_role"], got["head_role"]), ("viewer", "editor"))
        self.assertTrue(got["multi_b"])
        self.assertIsNone(got["multi_c"])
        self.assertEqual(got["company"], "viewer")
        self.assertIsNone(got["company_outsider"])
        self.assertIsNone(got["restricted"])
        self.assertIsNone(got["unassigned"])
        self.assertIsNone(got["stale"])
        self.assertTrue({self.only_a, self.a_and_b, self.whole_company} <= got["ids"])
        self.assertFalse({self.restricted, self.restricted_company, self.unassigned, self.stale_grant} & got["ids"])

    # ---------------- 用户侧的知识库中心 ----------------
    def test_list_shows_where_each_space_was_assigned(self):
        listing = _run_db(lambda db: svc.list_spaces(db, self.member_a["id"]))
        by_name = {item["name"]: item for item in listing["items"]}
        self.assertEqual(by_name["kd-only-a"]["scope"], "department")
        self.assertEqual([d["name"] for d in by_name["kd-only-a"]["departments"]], ["kd-team-a"])
        self.assertEqual([d["name"] for d in by_name["kd-a-and-b"]["departments"]], ["kd-team-a", "kd-team-b"])
        self.assertEqual(by_name["kd-company"]["scope"], "enterprise")
        self.assertEqual((by_name["kd-only-a"]["my_role"], by_name["kd-only-a"]["can_write_doc"]), ("viewer", False))
        for hidden in ("kd-restricted", "kd-company-secret", "kd-unassigned", "kd-stale"):
            self.assertNotIn(hidden, by_name)
        self.assertNotIn("publishable_departments", listing, "划分是管理员统一做的，用户侧不再提供发布到部门")

    def test_users_create_personal_spaces_only(self):
        created = _run_db(lambda db: svc.create_space(db, self.member_a["id"], {"name": "kd-mine", "team_id": self.teams["a"]}))
        self.assertEqual((created["scope"], created["departments"]), ("personal", []))
        row = self.db_row("SELECT scope_type, team_id FROM knowledge_spaces WHERE id=:i", {"i": created["id"]})
        self.assertEqual(tuple(row), ("personal", None), "即使请求里带了部门，普通用户创建的也只是个人空间")

    @staticmethod
    def db_row(sql, params):
        with SessionLocal() as db:
            return db.execute(text(sql), params).first()

    def test_sensitivity_of_an_assigned_space_is_changed_by_the_admin_not_the_owner(self):
        with self.assertRaises(PermissionDenied):
            _run_db(lambda db: svc.update_space(db, self.owner["id"], self.only_a, {"sensitivity": "public"}))
        personal = _run_db(lambda db: svc.create_space(db, self.member_a["id"], {"name": "kd-own-level"}))
        changed = _run_db(lambda db: svc.update_space(db, self.member_a["id"], personal["id"], {"sensitivity": "confidential"}))
        self.assertEqual(changed["sensitivity"], "confidential")
        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.update_space(db, self.member_a["id"], personal["id"], {"sensitivity": "nope"}))

    def test_department_members_cannot_change_settings(self):
        with self.assertRaises(PermissionDenied):
            _run_db(lambda db: svc.update_space(db, self.member_a["id"], self.only_a, {"name": "hijack"}))
        with self.assertRaises(PermissionDenied):
            _run_db(lambda db: svc.update_space(db, self.head_a["id"], self.only_a, {"name": "hijack"}))

    def test_deleting_a_space_removes_its_assignments(self):
        with SessionLocal() as db:
            space = KnowledgeSpace(user_id=self.owner["id"], name="kd-to-delete", scope_type="department", organization_id=self.org_id)
            db.add(space)
            db.commit()
            db.add(KnowledgeSpaceDepartment(space_id=space.id, team_id=self.teams["a"]))
            db.commit()
            space_id = space.id
            db.execute(text("DELETE FROM knowledge_spaces WHERE id=:i"), {"i": space_id})
            db.commit()
            left = db.execute(text("SELECT COUNT(*) FROM knowledge_space_departments WHERE space_id=:i"), {"i": space_id}).scalar()
        self.assertEqual(left, 0)


if __name__ == "__main__":
    unittest.main()
