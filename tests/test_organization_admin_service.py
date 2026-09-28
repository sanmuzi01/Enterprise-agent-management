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
from tests.test_enterprise_access import _add_org_member, _create_org

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
        cls.admin = rc.create_user("oa-owner")
        cls.member1 = rc.create_user("oa-mem1")
        cls.member2 = rc.create_user("oa-mem2")

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

        renamed = _run_db(lambda db: svc.update_team(db, team_id, self.admin["id"], name="oa-test-procurement-2"))
        self.assertEqual(renamed["name"], "oa-test-procurement-2")

        disabled = _run_db(lambda db: svc.update_team(db, team_id, self.admin["id"], status="disabled"))
        self.assertEqual(disabled["status"], "disabled")

    def test_duplicate_team_name_raises_conflict(self):
        import service.organization_admin_service as svc

        _run_db(lambda db: svc.create_team(db, "oa-test-dup", self.admin["id"]))
        with self.assertRaises(Conflict):
            _run_db(lambda db: svc.create_team(db, "oa-test-dup", self.admin["id"]))

    def test_update_nonexistent_team_raises_not_found(self):
        import service.organization_admin_service as svc

        with self.assertRaises(NotFound):
            _run_db(lambda db: svc.update_team(db, 999_999_999, self.admin["id"], name="x"))

    def test_invalid_status_raises_invalid_input(self):
        import service.organization_admin_service as svc

        created = _run_db(lambda db: svc.create_team(db, "oa-test-badstatus", self.admin["id"]))
        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.update_team(db, created["id"], self.admin["id"], status="deleted"))

    def test_disabling_team_via_admin_backend_immediately_revokes_access(self):
        # 闭环验证：管理员在这个后台点"停用部门"之后，用之前已经在这个部门的
        # 负责人身份必须立刻失效——不能只是 teams.status 改了，鉴权层没跟上
        # （P0 修复：service/enterprise_access.py::_team_role_rank 现在会 JOIN
        # teams 检查 status='active'）。
        import service.organization_admin_service as svc
        from service.enterprise_access import is_team_admin

        team = _run_db(lambda db: svc.create_team(db, "oa-test-disable-revokes", self.admin["id"]))
        _run_db(lambda db: svc.add_team_member(db, team["id"], self.admin["id"], self.member1["id"], "admin"))

        sync_db = SessionLocal()
        try:
            self.assertTrue(is_team_admin(sync_db, self.member1["id"], team["id"]))
        finally:
            sync_db.close()

        _run_db(lambda db: svc.update_team(db, team["id"], self.admin["id"], status="disabled"))

        sync_db = SessionLocal()
        try:
            self.assertFalse(is_team_admin(sync_db, self.member1["id"], team["id"]))
        finally:
            sync_db.close()

    def test_add_member_sets_lead_and_appears_in_list(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-lead", self.admin["id"]))
        team_id = team["id"]

        _run_db(lambda db: svc.add_team_member(db, team_id, self.admin["id"], self.member1["id"], "admin"))
        _run_db(lambda db: svc.add_team_member(db, team_id, self.admin["id"], self.member2["id"], "member"))

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
        _run_db(lambda db: svc.add_team_member(db, team["id"], self.admin["id"], self.member1["id"], "member"))

        org_members = _run_db(lambda db: svc.list_org_members(db))
        self.assertIn(self.member1["id"], [m["user_id"] for m in org_members])

    def test_update_and_remove_team_member(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-update-member", self.admin["id"]))
        team_id = team["id"]
        _run_db(lambda db: svc.add_team_member(db, team_id, self.admin["id"], self.member1["id"], "member"))

        _run_db(lambda db: svc.update_team_member_role(db, team_id, self.admin["id"], self.member1["id"], "admin"))
        members = _run_db(lambda db: svc.list_team_members(db, team_id))
        self.assertEqual(members[0]["role_code"], "admin")

        _run_db(lambda db: svc.remove_team_member(db, team_id, self.admin["id"], self.member1["id"]))
        members_after = _run_db(lambda db: svc.list_team_members(db, team_id))
        self.assertEqual(members_after, [])

    def test_remove_nonmember_raises_not_found(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-notamember", self.admin["id"]))
        with self.assertRaises(NotFound):
            _run_db(lambda db: svc.remove_team_member(db, team["id"], self.admin["id"], self.member2["id"]))

    def test_invalid_role_code_raises_invalid_input(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-badrole", self.admin["id"]))
        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.add_team_member(db, team["id"], self.admin["id"], self.member1["id"], "superboss"))

    def test_team_permissions_view(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-permissions", self.admin["id"]))
        _run_db(lambda db: svc.add_team_member(db, team["id"], self.admin["id"], self.member1["id"], "admin"))

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
        cls.admin = rc.create_user("oa-memown")
        cls.user = rc.create_user("oa-memtgt")

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

        _run_db(lambda db: svc.add_org_member(db, self.admin["id"], self.user["id"], "member"))
        members = _run_db(lambda db: svc.list_org_members(db))
        self.assertIn(self.user["id"], [m["user_id"] for m in members])

        _run_db(lambda db: svc.update_org_member(db, self.admin["id"], self.user["id"], role_code="admin"))
        members = _run_db(lambda db: svc.list_org_members(db))
        by_user = {m["user_id"]: m for m in members}
        self.assertEqual(by_user[self.user["id"]]["role_code"], "admin")

        _run_db(lambda db: svc.update_org_member(db, self.admin["id"], self.user["id"], status="disabled"))
        members = _run_db(lambda db: svc.list_org_members(db))
        by_user = {m["user_id"]: m for m in members}
        self.assertEqual(by_user[self.user["id"]]["status"], "disabled")

        _run_db(lambda db: svc.remove_org_member(db, self.admin["id"], self.user["id"]))
        members = _run_db(lambda db: svc.list_org_members(db))
        self.assertNotIn(self.user["id"], [m["user_id"] for m in members])

    def test_remove_org_member_cascades_team_membership(self):
        import service.organization_admin_service as svc

        team = _run_db(lambda db: svc.create_team(db, "oa-test-member-cascade", self.admin["id"]))
        _run_db(lambda db: svc.add_team_member(db, team["id"], self.admin["id"], self.user["id"], "member"))

        _run_db(lambda db: svc.remove_org_member(db, self.admin["id"], self.user["id"]))

        team_members = _run_db(lambda db: svc.list_team_members(db, team["id"]))
        self.assertEqual(team_members, [])

    def test_update_nonmember_raises_not_found(self):
        import service.organization_admin_service as svc

        with self.assertRaises(NotFound):
            _run_db(lambda db: svc.update_org_member(db, self.admin["id"], 999_999_999, role_code="admin"))


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class AuditTrailTest(unittest.TestCase):
    """组织变更（建部门、分配成员、改角色等）都要写审计——之前这些操作完全没有
    留痕，见 docs/enterprise-rbac-plan.md 第21节。"""

    @classmethod
    def setUpClass(cls):
        cls.admin = rc.create_user("oa-audadm")
        cls.member = rc.create_user("oa-audmem")

    @classmethod
    def tearDownClass(cls):
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM team_members WHERE team_id IN "
                             "(SELECT id FROM teams WHERE name LIKE 'oa-audit-%')"))
            db.execute(text("DELETE FROM teams WHERE name LIKE 'oa-audit-%'"))
            db.execute(text("DELETE FROM audit_event WHERE user_id=:u"), {"u": cls.admin["id"]})
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        rc.cleanup()

    def test_create_team_writes_audit_event(self):
        import service.organization_admin_service as svc
        from models import audit_dao

        team = _run_db(lambda db: svc.create_team(db, "oa-audit-create", self.admin["id"]))
        events = _run_db(lambda db: audit_dao.list_by_resource_async(db, "team", team["id"]))
        actions = [e.action for e in events]
        self.assertIn("org.team_created", actions)

    def test_add_team_member_writes_audit_event(self):
        import service.organization_admin_service as svc
        from models import audit_dao

        team = _run_db(lambda db: svc.create_team(db, "oa-audit-member", self.admin["id"]))
        _run_db(lambda db: svc.add_team_member(db, team["id"], self.admin["id"], self.member["id"], "admin"))
        events = _run_db(lambda db: audit_dao.list_by_resource_async(db, "team", team["id"]))
        actions = [e.action for e in events]
        self.assertIn("org.team_member_added", actions)
        # 记录的操作人是管理员本人，不是被分配的那个成员。
        add_event = next(e for e in events if e.action == "org.team_member_added")
        self.assertEqual(add_event.user_id, self.admin["id"])

    def test_disable_team_writes_audit_event_with_status_change(self):
        import json

        import service.organization_admin_service as svc
        from models import audit_dao

        team = _run_db(lambda db: svc.create_team(db, "oa-audit-disable", self.admin["id"]))
        _run_db(lambda db: svc.update_team(db, team["id"], self.admin["id"], status="disabled"))
        events = _run_db(lambda db: audit_dao.list_by_resource_async(db, "team", team["id"]))
        update_event = next(e for e in events if e.action == "org.team_updated")
        detail = json.loads(update_event.detail)
        self.assertEqual(detail["status"], {"from": "active", "to": "disabled"})

    def test_noop_update_does_not_write_audit_event(self):
        # 传了 name/status 但值跟原来一样——不算真的改动，不该写一条空审计。
        import service.organization_admin_service as svc
        from models import audit_dao

        team = _run_db(lambda db: svc.create_team(db, "oa-audit-noop", self.admin["id"]))
        _run_db(lambda db: svc.update_team(db, team["id"], self.admin["id"], name="oa-audit-noop"))
        events = _run_db(lambda db: audit_dao.list_by_resource_async(db, "team", team["id"]))
        self.assertNotIn("org.team_updated", [e.action for e in events])


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class LastOwnerProtectionTest(unittest.TestCase):
    """企业不能被误操作成"没有任何 owner"——建一个专属测试企业（只有一个 owner），
    只有当它恰好是这台机器上的默认企业时才跑断言本身（`_get_default_organization`
    取的是 id 最小的那个 Organization，测试库和真实开发机不一定是同一行，这条
    跟 test_agent_admin_service.py 的 `_default_org_is_our_org` 是同一个限制）。"""

    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.owner = rc.create_user("lastown-o")
        cls.other = rc.create_user("lastown-x")
        cls.org_id = _create_org(cls.db, "lastowner-org", cls.owner["id"])
        _add_org_member(cls.db, cls.org_id, cls.owner["id"], "owner")

    @classmethod
    def tearDownClass(cls):
        # 先 cleanup() 删掉测试用户（连带级联删掉 organization_members），
        # organizations 这行才没有子行引用，才能真的删掉——反过来会撞 fk_om_org。
        rc.cleanup()
        cls.db.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": cls.org_id})
        cls.db.commit()
        cls.db.close()

    def _is_default_org(self) -> bool:
        import service.organization_admin_service as org_svc

        async def _check(db):
            org = await org_svc._get_default_organization(db)
            return org.id == self.org_id

        return _run_db(_check)

    def test_cannot_demote_last_owner(self):
        if not self._is_default_org():
            self.skipTest("本机默认企业不是这个测试建的那个")
        import service.organization_admin_service as svc

        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.update_org_member(
                db, self.owner["id"], self.owner["id"], role_code="member",
            ))

    def test_cannot_disable_last_owner(self):
        if not self._is_default_org():
            self.skipTest("本机默认企业不是这个测试建的那个")
        import service.organization_admin_service as svc

        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.update_org_member(
                db, self.owner["id"], self.owner["id"], status="disabled",
            ))

    def test_cannot_remove_last_owner(self):
        if not self._is_default_org():
            self.skipTest("本机默认企业不是这个测试建的那个")
        import service.organization_admin_service as svc

        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.remove_org_member(db, self.owner["id"], self.owner["id"]))

    def test_can_demote_owner_when_another_owner_exists(self):
        if not self._is_default_org():
            self.skipTest("本机默认企业不是这个测试建的那个")
        import service.organization_admin_service as svc

        _run_db(lambda db: svc.add_org_member(db, self.owner["id"], self.other["id"], "owner"))
        try:
            result = _run_db(lambda db: svc.update_org_member(
                db, self.owner["id"], self.owner["id"], role_code="member",
            ))
            self.assertEqual(result["user_id"], self.owner["id"])
        finally:
            # 恢复成唯一 owner，不影响其它可能在这个类之后跑的测试方法。
            _run_db(lambda db: svc.update_org_member(
                db, self.other["id"], self.owner["id"], role_code="owner",
            ))
            _run_db(lambda db: svc.remove_org_member(db, self.owner["id"], self.other["id"]))


if __name__ == "__main__":
    unittest.main()
