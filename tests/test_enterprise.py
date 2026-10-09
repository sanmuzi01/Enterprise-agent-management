"""平台只服务一个企业：企业信息、改名、新用户自动加入这个企业（不能再按名字找，名字会被改成真实的公司名）。"""
import unittest
from unittest import mock

from sqlalchemy import text

from models.init_db import Organization, SessionLocal
from service.exceptions import InvalidInput
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
class EnterpriseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = rc.create_user("ent-admin")
        cls.member = rc.create_user("ent-member")
        cls.newcomer = rc.create_user("ent-newcomer")
        db = SessionLocal()
        cls.db = db
        cls.org = Organization(name="ent-org-original", owner_user_id=cls.admin["id"])
        db.add(cls.org)
        db.commit()
        cls.org_id = cls.org.id
        cls.client = rc.make_client()
        cls.env = rc.admin_env(cls.admin["name"])
        cls.env.start()
        cls.enterprise = mock.patch("service.organization_admin_service.find_default_organization", mock.AsyncMock(return_value=cls.org))
        cls.enterprise.start()

    @classmethod
    def tearDownClass(cls):
        cls.enterprise.stop()
        cls.env.stop()
        cls.db.execute(text("DELETE FROM team_members WHERE team_id IN (SELECT id FROM teams WHERE organization_id=:o)"), {"o": cls.org_id})
        cls.db.execute(text("DELETE FROM teams WHERE organization_id=:o"), {"o": cls.org_id})
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org_id})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org_id})
        cls.db.commit()
        cls.db.close()
        rc.cleanup()

    def test_enterprise_info_and_rename_over_http(self):
        info = self.client.get("/admin/org/enterprise", headers=self.admin["headers"])
        self.assertEqual(info.status_code, 200, info.text)
        self.assertEqual((info.json()["id"], info.json()["name"]), (self.org_id, "ent-org-original"))
        renamed = self.client.patch("/admin/org/enterprise", headers=self.admin["headers"], json={"name": "  ent-org-renamed  "})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["name"], "ent-org-renamed", "名称首尾空白会被去掉")
        with SessionLocal() as db:
            self.assertEqual(db.execute(text("SELECT name FROM organizations WHERE id=:o"), {"o": self.org_id}).scalar(), "ent-org-renamed")

    def test_rename_rejects_blank_names(self):
        import service.organization_admin_service as svc
        for name in ("", "   "):
            with self.assertRaises(InvalidInput):
                _run_db(lambda db: svc.rename_enterprise(db, self.admin["id"], name))
        response = self.client.patch("/admin/org/enterprise", headers=self.admin["headers"], json={"name": ""})
        self.assertEqual(response.status_code, 422)

    def test_only_admins_can_read_or_rename(self):
        self.assertEqual(self.client.get("/admin/org/enterprise", headers=self.member["headers"]).status_code, 403)
        self.assertEqual(self.client.patch("/admin/org/enterprise", headers=self.member["headers"], json={"name": "x"}).status_code, 403)

    def test_new_users_are_enrolled_by_position_not_by_name(self):
        """改名之后，新用户仍然自动加入这个企业（以前按“默认企业”这个名字找，改名就会找不到）。"""
        from models import enterprise_dao
        self.client.patch("/admin/org/enterprise", headers=self.admin["headers"], json={"name": "完全不叫默认企业的公司"})
        # 把“id 最小的企业”限定为本测试的企业，避免被本机其他企业影响
        only_this = f"SELECT id FROM organizations WHERE id = {self.org_id}"
        with mock.patch.object(enterprise_dao, "_ENTERPRISE_ID_SQL", only_this):
            with SessionLocal() as db:
                enterprise_dao.enroll_in_default_organization(db, self.newcomer["id"])
            with SessionLocal() as db:
                joined = db.execute(text("SELECT organization_id FROM organization_members WHERE user_id=:u"), {"u": self.newcomer["id"]}).scalar()
        self.assertEqual(joined, self.org_id)

    def test_user_list_shows_departments_and_enterprise_role(self):
        """用户管理的列表直接带出所属部门（含负责人身份）和企业角色，不用再去组织架构里另外对照。"""
        from models.async_db import AsyncSessionLocal
        from service import organization_admin_service as org_svc
        from service.admin_async_service import list_users
        from tests._async_helpers import run_async

        async def _do():
            async with AsyncSessionLocal() as db:
                team = await org_svc.create_team(db, "ent-dept-list", self.admin["id"])
                await org_svc.add_org_member(db, self.admin["id"], self.member["id"], "auditor")
                await org_svc.add_team_member(db, team["id"], self.admin["id"], self.member["id"], "admin")
            async with AsyncSessionLocal() as db:
                return await list_users(db, limit=50, offset=0, search="ent-member"), team["id"]

        page, team_id = run_async(_do())
        mine = next(u for u in page["items"] if u["id"] == self.member["id"])
        self.assertEqual(mine["org_role_code"], "auditor")
        self.assertEqual(mine["org_status"], "active")
        self.assertEqual([(d["id"], d["role_code"]) for d in mine["departments"]], [(team_id, "admin")])

        # 还不是企业成员、也没有部门的人：字段齐全但为空，页面据此显示「未加入企业 / 未分配部门」
        loner = rc.create_user("ent-loner")
        page = run_async(_list_for("ent-loner"))
        other = next(u for u in page["items"] if u["id"] == loner["id"])
        self.assertIsNone(other["org_role_code"])
        self.assertEqual(other["departments"], [])


async def _list_for(search):
    from models.async_db import AsyncSessionLocal
    from service.admin_async_service import list_users
    async with AsyncSessionLocal() as db:
        return await list_users(db, limit=50, offset=0, search=search)


if __name__ == "__main__":
    unittest.main()
