"""service/access_control.py 新增的"部门 team admin 也能看部门下的知识库空间"这条来源
（Phase 3B，docs/enterprise-rbac-plan.md）：真实 DB，同步 + 异步两条路径都测，因为
get_owned_space/get_space_role/user_space_ids 各自有一份同步实现和一份异步实现，
两边都要单独验证，不能假设改一边另一边也对。

也顺手回归一遍 owner / SpaceMember 这两条本来就有的来源，确认新加的第三条来源没有
把旧的挤掉或者改变优先级。
"""
import unittest

from models.init_db import (EnterpriseRole, KnowledgeSpace, Organization, OrganizationMember, SessionLocal,
                            SpaceMember, Team, TeamMember)
from service import access_control
from tests import _route_client as rc
from tests._async_helpers import run_async as _run

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class AccessControlTeamAdminTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = rc.create_user("ac-space-owner")
        cls.team_admin = rc.create_user("ac-team-admin")
        cls.team_member = rc.create_user("ac-team-member")  # 部门普通成员，不该因此拿到空间权限
        cls.viewer = rc.create_user("ac-space-viewer")       # 直接是 SpaceMember，跟部门无关
        cls.outsider = rc.create_user("ac-space-outsider")

        db = SessionLocal()
        try:
            org = Organization(name="ac-test-org", owner_user_id=cls.owner["id"])
            db.add(org)
            db.commit()
            cls.org_id = org.id
            team = Team(name="ac-test-team", owner_user_id=cls.owner["id"], organization_id=cls.org_id)
            db.add(team)
            db.commit()
            cls.team_id = team.id
            org_member_role_id = db.query(EnterpriseRole.id).filter_by(scope="organization", code="member").scalar()
            for user in (cls.team_admin, cls.team_member):   # 部门权限以有效企业成员身份为前提
                db.add(OrganizationMember(organization_id=cls.org_id, user_id=user["id"],
                                           role_id=org_member_role_id, status="active"))
            db.commit()

            admin_role_id = db.query(EnterpriseRole.id).filter_by(scope="team", code="admin").scalar()
            member_role_id = db.query(EnterpriseRole.id).filter_by(scope="team", code="member").scalar()
            db.add(TeamMember(team_id=cls.team_id, user_id=cls.team_admin["id"],
                               role_id=admin_role_id, status="active"))
            db.add(TeamMember(team_id=cls.team_id, user_id=cls.team_member["id"],
                               role_id=member_role_id, status="active"))
            db.commit()

            space = KnowledgeSpace(user_id=cls.owner["id"], name="ac-test-space", team_id=cls.team_id)
            db.add(space)
            db.commit()
            cls.space_id = space.id

            db.add(SpaceMember(space_id=cls.space_id, user_id=cls.viewer["id"], role="viewer"))
            db.commit()
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        from sqlalchemy import text
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM space_members WHERE space_id=:s"), {"s": cls.space_id})
            db.execute(text("DELETE FROM knowledge_spaces WHERE id=:s"), {"s": cls.space_id})
            db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": cls.team_id})
            db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team_id})
            db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org_id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org_id})
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        rc.cleanup()

    # ---------------- 同步 ----------------

    def test_sync_team_admin_can_read_space_without_being_a_space_member(self):
        db = SessionLocal()
        try:
            space = access_control.get_owned_space(db, self.team_admin["id"], self.space_id)
            self.assertIsNotNone(space)
            self.assertEqual(access_control.get_space_role(db, self.team_admin["id"], self.space_id), "admin")
            self.assertIn(self.space_id, access_control.user_space_ids(db, self.team_admin["id"]))
        finally:
            db.close()

    def test_sync_plain_team_member_gets_no_space_access(self):
        # 部门普通成员（不是 team admin）不应该因为在同一个部门就拿到空间权限
        db = SessionLocal()
        try:
            self.assertIsNone(access_control.get_owned_space(db, self.team_member["id"], self.space_id))
            self.assertIsNone(access_control.get_space_role(db, self.team_member["id"], self.space_id))
            self.assertNotIn(self.space_id, access_control.user_space_ids(db, self.team_member["id"]))
        finally:
            db.close()

    def test_sync_owner_and_space_member_still_work(self):
        # 回归：新加的第三条来源不应该影响原有两条
        db = SessionLocal()
        try:
            self.assertEqual(access_control.get_space_role(db, self.owner["id"], self.space_id), "owner")
            self.assertEqual(access_control.get_space_role(db, self.viewer["id"], self.space_id), "viewer")
            self.assertIsNone(access_control.get_owned_space(db, self.outsider["id"], self.space_id))
        finally:
            db.close()

    # ---------------- 异步 ----------------

    def test_async_team_admin_can_read_space_without_being_a_space_member(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                space = await access_control.get_owned_space_async(db, self.team_admin["id"], self.space_id)
                self.assertIsNotNone(space)
                role = await access_control.get_space_role_async(db, self.team_admin["id"], self.space_id)
                self.assertEqual(role, "admin")
                ids = await access_control.user_space_ids_async(db, self.team_admin["id"])
                self.assertIn(self.space_id, ids)
        _run(_do())

    def test_async_plain_team_member_gets_no_space_access(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                space = await access_control.get_owned_space_async(db, self.team_member["id"], self.space_id)
                self.assertIsNone(space)
                role = await access_control.get_space_role_async(db, self.team_member["id"], self.space_id)
                self.assertIsNone(role)
        _run(_do())

    def test_async_owner_and_space_member_still_work(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                owner_role = await access_control.get_space_role_async(db, self.owner["id"], self.space_id)
                self.assertEqual(owner_role, "owner")
                viewer_role = await access_control.get_space_role_async(db, self.viewer["id"], self.space_id)
                self.assertEqual(viewer_role, "viewer")
                outsider_space = await access_control.get_owned_space_async(db, self.outsider["id"], self.space_id)
                self.assertIsNone(outsider_space)
        _run(_do())


if __name__ == "__main__":
    unittest.main()
