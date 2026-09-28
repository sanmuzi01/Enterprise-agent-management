"""models/enterprise_dao.py 的单测：is_team_admin_of_team / is_team_member_of_team /
is_org_member / list_space_ids_where_team_admin。

这几个函数是 service/access_control.py 判断"部门管理员也能看部门知识库空间"这条
可见性规则的唯一数据源。P0 修复要证明的是：部门/企业被停用后，这几个函数必须
立刻返回 False/空——之前它们只检查 team_members.status/organization_members.status，
不检查 teams.status/organizations.status，停用部门在鉴权层面形同虚设。
"""
import unittest

from sqlalchemy import text

from models.enterprise_dao import (
    is_org_member,
    is_team_admin_of_team,
    is_team_member_of_team,
    list_space_ids_where_team_admin,
)
from models.init_db import KnowledgeSpace, SessionLocal
from tests import _route_client as rc
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class EnterpriseDaoDisabledStatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.admin_user = rc.create_user("edao-team-admin")
        cls.member_user = rc.create_user("edao-team-member")

        cls.org_id = _create_org(cls.db, "edao-org", cls.admin_user["id"])
        _add_org_member(cls.db, cls.org_id, cls.admin_user["id"], "member")
        _add_org_member(cls.db, cls.org_id, cls.member_user["id"], "member")

        cls.team_id = _create_team(cls.db, cls.org_id, "edao-team", cls.admin_user["id"])
        _add_team_member(cls.db, cls.team_id, cls.admin_user["id"], "admin")
        _add_team_member(cls.db, cls.team_id, cls.member_user["id"], "member")

        space = KnowledgeSpace(user_id=cls.admin_user["id"], name="edao-space", team_id=cls.team_id)
        cls.db.add(space)
        cls.db.commit()
        cls.space_id = space.id

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.execute(text("DELETE FROM knowledge_spaces WHERE id=:i"), {"i": cls.space_id})
        cls.db.execute(text("DELETE FROM teams WHERE id=:i"), {"i": cls.team_id})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": cls.org_id})
        cls.db.commit()
        cls.db.close()

    def test_active_team_grants_expected_visibility(self):
        self.assertTrue(is_team_admin_of_team(self.db, self.admin_user["id"], self.team_id))
        self.assertFalse(is_team_admin_of_team(self.db, self.member_user["id"], self.team_id))
        self.assertTrue(is_team_member_of_team(self.db, self.member_user["id"], self.team_id))
        self.assertTrue(is_org_member(self.db, self.member_user["id"]))
        self.assertIn(self.space_id, list_space_ids_where_team_admin(self.db, self.admin_user["id"]))

    def test_disabled_team_revokes_all_team_derived_checks(self):
        self.db.execute(text("UPDATE teams SET status='disabled' WHERE id=:i"), {"i": self.team_id})
        self.db.commit()
        try:
            self.assertFalse(is_team_admin_of_team(self.db, self.admin_user["id"], self.team_id))
            self.assertFalse(is_team_member_of_team(self.db, self.member_user["id"], self.team_id))
            self.assertNotIn(self.space_id, list_space_ids_where_team_admin(self.db, self.admin_user["id"]))
        finally:
            self.db.execute(text("UPDATE teams SET status='active' WHERE id=:i"), {"i": self.team_id})
            self.db.commit()

    def test_disabled_organization_revokes_org_member_check(self):
        self.db.execute(text("UPDATE organizations SET status='disabled' WHERE id=:i"), {"i": self.org_id})
        self.db.commit()
        try:
            self.assertFalse(is_org_member(self.db, self.member_user["id"]))
        finally:
            self.db.execute(text("UPDATE organizations SET status='active' WHERE id=:i"), {"i": self.org_id})
            self.db.commit()


if __name__ == "__main__":
    unittest.main()
