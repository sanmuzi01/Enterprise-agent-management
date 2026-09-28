"""企业组织管理后台服务（service/organization_admin_service.py）的回归测试。

补的是"数据库有 Organization/Team/EnterpriseRole/OrganizationMember/TeamMember，
但没有对应管理入口"这个产品缺口，见 docs/enterprise-rbac-plan.md 第17节。跟
tests/test_admin_plan_crud.py 是同一套写法：直接调 service 函数（AsyncSessionLocal），
不经过 HTTP 层——路由层只是薄薄一层 Depends 转发，真正的行为在 service 里。
"""
import unittest

from sqlalchemy import text

from models.init_db import SessionLocal
from service.exceptions import Conflict, InvalidInput, NotFound
from tests import _route_client as rc
from tests._async_helpers import run_async as _run

_AVAILABLE, _WHY = rc.route_tests_available()


def _run_db(fn):
    """跑一个 `async def _x(db): ...` 风格的函数，自己管 AsyncSessionLocal 生命周期。"""
    from models.async_db import AsyncSessionLocal

    async def _wrapper():
        async with AsyncSessionLocal() as db:
            return await fn(db)

    return _run(_wrapper())


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class TeamCrudTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = rc.create_user("orgadmin-owner")
        cls.member1 = rc.create_user("orgadmin-member1")
        cls.member2 = rc.create_user("orgadmin-member2")

    @classmethod
    def tearDownClass(cls):
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM team_members WHERE team_id IN "
                             "(SELECT id FROM teams WHERE name LIKE 'oa-test-%')"))
            db.execute(text("DELETE FROM organization_members WHERE user_id IN (:a,:b,:c)"),
                       {"a": cls.admin["id"], "b": cls.member1["id"], "c": cls.member2["id"]})
            db.execute(text("DELETE FROM teams WHERE name LIKE 'oa-test-%'"))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        rc.cleanup()

    def test_create_list_rename_disable_team(self):
        import service.organization_admin_service as svc

        created = _run_db(lambda db: svc.create_team(db, "oa-test-procurement", self.admin["id"]))
        self.assertEqual(created["status"], "active")
        team_id = created["id"]

        teams = _run_db(lambda db: svc.list_teams(db))
        self.assertIn(team_id, [t["id"] for t in teams])

        renamed = _run_db(lambda db: svc.update_team(db, team_id, name="oa-test-procurement-2"))
        self.assertEqual(renamed["name"], "oa-test-procurement-2")

        disabled = _run_db(lambda db: svc.update_team(db, team_id, status="disabled"))
        self.assertEqual(disabled["status"], "disabled")

    def test_duplicate_team_name_raises_conflict(self):
        import service.organization_admin_service as svc

        _run_db(lambda db: svc.create_team(db, "oa-test-dup", self.admin["id"]))
        with self.assertRaises(Conflict):
            _run_db(lambda db: svc.create_team(db, "oa-test-dup", self.admin["id"]))

    def test_update_nonexistent_team_raises_not_found(self):
        import service.organization_admin_service as svc

        with self.assertRaises(NotFound):
            _run_db(lambda db: svc.update_team(db, 999_999_999, name="x"))

    def test_invalid_status_raises_invalid_input(self):
        import service.organization_admin_service as svc

        created = _run_db(lambda db: svc.create_team(db, "oa-test-badstatus", self.admin["id"]))
        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.update_team(db, created["id"], status="deleted"))

    def test_disabling_team_via_admin_backend_immediately_revokes_access(self):
        # 闭环验证：管理员在这个后台点"停用部门"之后，用之前已经在这个部门的
        # 负责人身份必须立刻失效——不能只是 teams.status 改了，鉴权层没跟上
        # （P0 修复：service/enterprise_access.py::_team_role_rank 现在会 JOIN
        # teams 检查 status='active'）。
        import service.organization_admin_service as svc
        from service.enterprise_access import is_team_admin

        team = _run_db(lambda db: svc.create_team(db, "oa-test-disable-revokes", self.admin["id"]))
        _run_db(lambda db: svc.add_team_member(db, team["id"], self.member1["id"], "admin"))

        sync_db = SessionLocal()
        try:
            self.assertTrue(is_team_admin(sync_db, self.member1["id"], team["id"]))
        finally:
            sync_db.close()

        _run_db(lambda db: svc.update_team(db, team["id"], status="disabled"))

        sync_db = SessionLocal()
        try:
            self.assertFalse(is_team_admin(sync_db, self.member1["id"], team["id"]))
        finally:
            sync_db.close()

    def test_add_member_sets_lead_and_appears_in_list(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-lead", self.admin["id"]))
        team_id = team["id"]

        _run_db(lambda db: svc.add_team_member(db, team_id, self.member1["id"], "admin"))
        _run_db(lambda db: svc.add_team_member(db, team_id, self.member2["id"], "member"))

        members = _run_db(lambda db: svc.list_team_members(db, team_id))
        by_user = {m["user_id"]: m for m in members}
        self.assertEqual(by_user[self.member1["id"]]["role_code"], "admin")
        self.assertEqual(by_user[self.member2["id"]]["role_code"], "member")

        teams = _run_db(lambda db: svc.list_teams(db))
        this_team = next(t for t in teams if t["id"] == team_id)
        self.assertEqual(this_team["member_count"], 2)
        self.assertEqual([l["user_id"] for l in this_team["leads"]], [self.member1["id"]])

    def test_add_member_auto_creates_org_membership(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-autoorg", self.admin["id"]))
        _run_db(lambda db: svc.add_team_member(db, team["id"], self.member1["id"], "member"))

        org_members = _run_db(lambda db: svc.list_org_members(db))
        self.assertIn(self.member1["id"], [m["user_id"] for m in org_members])

    def test_update_and_remove_team_member(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-update-member", self.admin["id"]))
        team_id = team["id"]
        _run_db(lambda db: svc.add_team_member(db, team_id, self.member1["id"], "member"))

        _run_db(lambda db: svc.update_team_member_role(db, team_id, self.member1["id"], "admin"))
        members = _run_db(lambda db: svc.list_team_members(db, team_id))
        self.assertEqual(members[0]["role_code"], "admin")

        _run_db(lambda db: svc.remove_team_member(db, team_id, self.member1["id"]))
        members_after = _run_db(lambda db: svc.list_team_members(db, team_id))
        self.assertEqual(members_after, [])

    def test_remove_nonmember_raises_not_found(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-notamember", self.admin["id"]))
        with self.assertRaises(NotFound):
            _run_db(lambda db: svc.remove_team_member(db, team["id"], self.member2["id"]))

    def test_invalid_role_code_raises_invalid_input(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-badrole", self.admin["id"]))
        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.add_team_member(db, team["id"], self.member1["id"], "superboss"))

    def test_team_permissions_view(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-permissions", self.admin["id"]))
        _run_db(lambda db: svc.add_team_member(db, team["id"], self.member1["id"], "admin"))

        perms = _run_db(lambda db: svc.team_permissions(db, team["id"]))
        self.assertEqual(perms["team"]["id"], team["id"])
        self.assertEqual(len(perms["members"]), 1)
        self.assertEqual(perms["knowledge_spaces"], [])
        self.assertEqual(perms["agents"], [])

    def test_list_enterprise_roles_shape(self):
        import service.organization_admin_service as svc

        roles = _run_db(lambda db: svc.list_enterprise_roles(db))
        self.assertIn("organization", roles)
        self.assertIn("team", roles)
        org_codes = {r["code"] for r in roles["organization"]}
        self.assertTrue({"owner", "admin", "member"}.issubset(org_codes))
        team_codes = {r["code"] for r in roles["team"]}
        self.assertTrue({"admin", "member"}.issubset(team_codes))


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class OrgMemberCrudTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = rc.create_user("orgadmin-member-owner")
        cls.user = rc.create_user("orgadmin-member-target")

    @classmethod
    def tearDownClass(cls):
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM team_members WHERE team_id IN "
                             "(SELECT id FROM teams WHERE name LIKE 'oa-test-member-%')"))
            db.execute(text("DELETE FROM teams WHERE name LIKE 'oa-test-member-%'"))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        rc.cleanup()

    def test_add_update_remove_org_member(self):
        import service.organization_admin_service as svc

        _run_db(lambda db: svc.add_org_member(db, self.user["id"], "member"))
        members = _run_db(lambda db: svc.list_org_members(db))
        self.assertIn(self.user["id"], [m["user_id"] for m in members])

        _run_db(lambda db: svc.update_org_member(db, self.user["id"], role_code="admin"))
        members = _run_db(lambda db: svc.list_org_members(db))
        by_user = {m["user_id"]: m for m in members}
        self.assertEqual(by_user[self.user["id"]]["role_code"], "admin")

        _run_db(lambda db: svc.update_org_member(db, self.user["id"], status="disabled"))
        members = _run_db(lambda db: svc.list_org_members(db))
        by_user = {m["user_id"]: m for m in members}
        self.assertEqual(by_user[self.user["id"]]["status"], "disabled")

        _run_db(lambda db: svc.remove_org_member(db, self.user["id"]))
        members = _run_db(lambda db: svc.list_org_members(db))
        self.assertNotIn(self.user["id"], [m["user_id"] for m in members])

    def test_remove_org_member_cascades_team_membership(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-member-cascade", self.admin["id"]))
        _run_db(lambda db: svc.add_team_member(db, team["id"], self.user["id"], "member"))

        _run_db(lambda db: svc.remove_org_member(db, self.user["id"]))

        team_members = _run_db(lambda db: svc.list_team_members(db, team["id"]))
        self.assertEqual(team_members, [])

    def test_update_nonmember_raises_not_found(self):
        import service.organization_admin_service as svc

        with self.assertRaises(NotFound):
            _run_db(lambda db: svc.update_org_member(db, 999_999_999, role_code="admin"))


if __name__ == "__main__":
    unittest.main()
