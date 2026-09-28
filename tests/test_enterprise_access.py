"""service/enterprise_access.py 的单元测试：真实 DB，直接调用依赖工厂返回的函数
（FastAPI 的 Depends(...) 只是默认值标记，显式传参调用完全等价于走真实请求）。

不经过 HTTP 层——这一步（Phase 3B 第 2 步）本来就还没往任何路由上接，见
docs/enterprise-rbac-plan.md 第 3 节的说明。

知识库空间的权限判断不在这个文件里：那部分复用的是已有的
service/access_control.py（见 tests/test_access_control_team_admin.py），
不是 enterprise_access.py 自己的逻辑——这里只测真正新增的 require_org_role/
require_team_role。
"""
import unittest

from sqlalchemy import text

from models.init_db import SessionLocal
from service.enterprise_access import is_org_admin, is_team_admin, require_org_role, require_team_role
from service.exceptions import NotFound, PermissionDenied
from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()


def _role_id(db, scope: str, code: str) -> int:
    return db.execute(
        text("SELECT id FROM enterprise_role WHERE scope=:s AND code=:c"),
        {"s": scope, "c": code},
    ).scalar()


def _add_org_member(db, org_id: int, user_id: int, code: str) -> None:
    db.execute(
        text(
            "INSERT INTO organization_members (organization_id, user_id, role_id, status, created_at, updated_at) "
            "VALUES (:org, :uid, :role, 'active', NOW(), NOW())"
        ),
        {"org": org_id, "uid": user_id, "role": _role_id(db, "organization", code)},
    )
    db.commit()


def _create_org(db, name: str, owner_user_id: int) -> int:
    db.execute(
        text("INSERT INTO organizations (name, owner_user_id, status, created_at) VALUES (:n, :o, 'active', NOW())"),
        {"n": name, "o": owner_user_id},
    )
    db.commit()
    return db.execute(text("SELECT id FROM organizations WHERE name=:n ORDER BY id DESC LIMIT 1"), {"n": name}).scalar()


def _create_team(db, org_id: int, name: str, owner_user_id: int) -> int:
    db.execute(
        text(
            "INSERT INTO teams (organization_id, name, owner_user_id, status, created_at) "
            "VALUES (:org, :n, :o, 'active', NOW())"
        ),
        {"org": org_id, "n": name, "o": owner_user_id},
    )
    db.commit()
    return db.execute(text("SELECT id FROM teams WHERE name=:n ORDER BY id DESC LIMIT 1"), {"n": name}).scalar()


def _add_team_member(db, team_id: int, user_id: int, code: str) -> None:
    db.execute(
        text(
            "INSERT INTO team_members (team_id, user_id, role_id, status, created_at, updated_at) "
            "VALUES (:t, :uid, :role, 'active', NOW(), NOW())"
        ),
        {"t": team_id, "uid": user_id, "role": _role_id(db, "team", code)},
    )
    db.commit()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class RequireOrgRoleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.owner = rc.create_user("ea-org-owner")
        cls.member = rc.create_user("ea-org-member")
        cls.outsider = rc.create_user("ea-org-outsider")
        cls.org_id = _create_org(cls.db, "ea-test-org", cls.owner["id"])
        _add_org_member(cls.db, cls.org_id, cls.owner["id"], "owner")
        _add_org_member(cls.db, cls.org_id, cls.member["id"], "member")

    @classmethod
    def tearDownClass(cls):
        # 先让 rc.cleanup() 删掉这几个测试用户（连带级联删掉 organization_members），
        # organizations 这行才没有子行引用，才能真的删掉——反过来会撞 fk_om_org。
        rc.cleanup()
        cls.db.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": cls.org_id})
        cls.db.commit()
        cls.db.close()

    def _user_obj(self, uid):
        from models.init_db import User
        return self.db.get(User, uid)

    def test_non_member_is_not_found(self):
        dep = require_org_role("member")
        with self.assertRaises(NotFound):
            dep(current_user=self._user_obj(self.outsider["id"]), db=self.db)

    def test_member_role_passes_member_requirement(self):
        dep = require_org_role("member")
        result = dep(current_user=self._user_obj(self.member["id"]), db=self.db)
        self.assertEqual(result.id, self.member["id"])

    def test_member_role_fails_admin_requirement(self):
        dep = require_org_role("admin")
        with self.assertRaises(PermissionDenied):
            dep(current_user=self._user_obj(self.member["id"]), db=self.db)

    def test_higher_rank_satisfies_lower_requirement(self):
        # owner 的 rank(3) 应该满足只要求 member(0) 的接口——不用在 roles 里把上级角色都列一遍
        dep = require_org_role("member")
        result = dep(current_user=self._user_obj(self.owner["id"]), db=self.db)
        self.assertEqual(result.id, self.owner["id"])


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class RequireTeamRoleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.admin_user = rc.create_user("ea-team-admin")
        cls.member_user = rc.create_user("ea-team-member")
        cls.other_org_user = rc.create_user("ea-team-otherorg")

        cls.org_id = _create_org(cls.db, "ea-team-test-org", cls.admin_user["id"])
        _add_org_member(cls.db, cls.org_id, cls.admin_user["id"], "member")
        _add_org_member(cls.db, cls.org_id, cls.member_user["id"], "member")

        cls.other_org_id = _create_org(cls.db, "ea-team-other-org", cls.other_org_user["id"])
        _add_org_member(cls.db, cls.other_org_id, cls.other_org_user["id"], "member")

        cls.team_id = _create_team(cls.db, cls.org_id, "ea-test-team", cls.admin_user["id"])
        _add_team_member(cls.db, cls.team_id, cls.admin_user["id"], "admin")
        _add_team_member(cls.db, cls.team_id, cls.member_user["id"], "member")

    @classmethod
    def tearDownClass(cls):
        # 同上：先 cleanup() 删掉测试用户（连带 team_members/organization_members），
        # teams/organizations 才没有子行引用。
        rc.cleanup()
        cls.db.execute(text("DELETE FROM teams WHERE id=:i"), {"i": cls.team_id})
        cls.db.execute(text("DELETE FROM organizations WHERE id IN (:a, :b)"),
                        {"a": cls.org_id, "b": cls.other_org_id})
        cls.db.commit()
        cls.db.close()

    def _user_obj(self, uid):
        from models.init_db import User
        return self.db.get(User, uid)

    def test_team_admin_passes(self):
        dep = require_team_role("admin")
        result = dep(self.team_id, current_user=self._user_obj(self.admin_user["id"]), db=self.db)
        self.assertEqual(result.id, self.admin_user["id"])

    def test_team_member_fails_admin_requirement(self):
        dep = require_team_role("admin")
        with self.assertRaises(PermissionDenied):
            dep(self.team_id, current_user=self._user_obj(self.member_user["id"]), db=self.db)

    def test_user_from_different_organization_is_not_found(self):
        # other_org_user 在自己企业里是成员，但这个 team 属于另一个企业——应该 404，
        # 不应该因为"至少是某个企业的成员"就放行。
        dep = require_team_role("member")
        with self.assertRaises(NotFound):
            dep(self.team_id, current_user=self._user_obj(self.other_org_user["id"]), db=self.db)


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class IsOrgAdminIsTeamAdminTest(unittest.TestCase):
    """`is_org_admin`/`is_team_admin` 是企业业务中心（Java）判断"能不能跨部门审批/查看"
    的唯一权限来源（service/enterprise_hub_client.py::resolve_caller_context），
    修复"Agent 工具能自行签发审批权限"那个漏洞——工具不能自己断言角色，只能靠这两个
    函数按真实的 enterprise_role 算。"""

    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.org_admin_user = rc.create_user("ea-isadmin-orgadmin")
        cls.team_admin_user = rc.create_user("ea-isadmin-teamadmin")
        cls.regular_user = rc.create_user("ea-isadmin-regular")
        cls.outsider = rc.create_user("ea-isadmin-outsider")

        cls.org_id = _create_org(cls.db, "ea-isadmin-org", cls.org_admin_user["id"])
        _add_org_member(cls.db, cls.org_id, cls.org_admin_user["id"], "admin")
        _add_org_member(cls.db, cls.org_id, cls.team_admin_user["id"], "member")
        _add_org_member(cls.db, cls.org_id, cls.regular_user["id"], "member")

        cls.team_id = _create_team(cls.db, cls.org_id, "ea-isadmin-team", cls.team_admin_user["id"])
        _add_team_member(cls.db, cls.team_id, cls.team_admin_user["id"], "admin")
        _add_team_member(cls.db, cls.team_id, cls.regular_user["id"], "member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.execute(text("DELETE FROM teams WHERE id=:i"), {"i": cls.team_id})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": cls.org_id})
        cls.db.commit()
        cls.db.close()

    def test_org_admin_is_org_admin(self):
        self.assertTrue(is_org_admin(self.db, self.org_admin_user["id"]))

    def test_team_admin_is_not_org_admin(self):
        self.assertFalse(is_org_admin(self.db, self.team_admin_user["id"]))

    def test_non_member_is_not_org_admin(self):
        self.assertFalse(is_org_admin(self.db, self.outsider["id"]))

    def test_team_admin_is_team_admin_for_own_team(self):
        self.assertTrue(is_team_admin(self.db, self.team_admin_user["id"], self.team_id))

    def test_regular_team_member_is_not_team_admin(self):
        self.assertFalse(is_team_admin(self.db, self.regular_user["id"], self.team_id))

    def test_outsider_is_not_team_admin(self):
        self.assertFalse(is_team_admin(self.db, self.outsider["id"], self.team_id))

    def test_is_team_admin_with_none_team_id_is_false(self):
        self.assertFalse(is_team_admin(self.db, self.team_admin_user["id"], None))


if __name__ == "__main__":
    unittest.main()
