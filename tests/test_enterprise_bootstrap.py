"""全新部署第一次启动自动建企业（service/enterprise_bootstrap.py）。

在开发库（已有企业）上跑：验证幂等、不会建第二个；在空库（CI、scripts/test_fresh_db.py）上跑：
真正走一遍创建——所有者是平台管理员、所有现有用户加入、无主的知识库空间挂到企业下——测完把建出来的数据删掉。
"""
import os
import unittest
from unittest import mock

from sqlalchemy import text

from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class EnterpriseBootstrapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = rc.create_user("eb-admin")
        cls.member = rc.create_user("eb-member")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()

    def setUp(self):
        from models.init_db import SessionLocal
        self.SessionLocal = SessionLocal
        with SessionLocal() as db:
            self.had_enterprise = db.execute(text("SELECT COUNT(*) FROM organizations")).scalar() > 0
            self.orphan_spaces = [r[0] for r in db.execute(text("SELECT id FROM knowledge_spaces WHERE organization_id IS NULL")).all()]
        self.created = None

    def tearDown(self):
        if not self.created:
            return
        org = self.created["organization_id"]
        with self.SessionLocal() as db:   # 把这次建出来的东西还原，空库还是空库
            if self.orphan_spaces:
                db.execute(text("UPDATE knowledge_spaces SET organization_id = NULL WHERE id IN :ids").bindparams(
                    __import__("sqlalchemy").bindparam("ids", expanding=True)), {"ids": self.orphan_spaces})
            db.execute(text("DELETE FROM organization_members WHERE organization_id = :o"), {"o": org})
            db.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org})
            db.commit()

    def run_once(self):
        from service.enterprise_bootstrap import ensure_default_enterprise
        with self.SessionLocal() as db:
            return ensure_default_enterprise(db)

    def test_idempotent_and_never_creates_a_second_enterprise(self):
        first = self.run_once()
        if first["status"] == "created":
            self.created = first
        second = self.run_once()
        self.assertEqual(second["status"], "exists")
        self.assertEqual(second["organization_id"], first["organization_id"])
        with self.SessionLocal() as db:
            if not self.had_enterprise:
                self.assertEqual(db.execute(text("SELECT COUNT(*) FROM organizations")).scalar(), 1)

    def test_creation_on_an_empty_database(self):
        if self.had_enterprise:
            self.skipTest("库里已经有企业：创建路径只在空库上验证（CI、scripts/test_fresh_db.py）")
        with rc.admin_env(self.admin["name"]):   # 平台管理员按角色或 ADMIN_USER_NAMES 认定
            result = self.run_once()
        self.created = result
        self.assertEqual(result["status"], "created")
        with self.SessionLocal() as db:
            owner = db.execute(text("SELECT owner_user_id FROM organizations WHERE id = :o"), {"o": result["organization_id"]}).scalar()
            roles = dict(db.execute(text(
                "SELECT om.user_id, er.code FROM organization_members om JOIN enterprise_role er ON er.id = om.role_id "
                "WHERE om.organization_id = :o"), {"o": result["organization_id"]}).all())
            orphans = db.execute(text("SELECT COUNT(*) FROM knowledge_spaces WHERE organization_id IS NULL")).scalar()
        self.assertEqual(roles.get(self.admin["id"]), "owner", "平台管理员是企业所有者")
        self.assertEqual(roles.get(self.member["id"]), "member")
        self.assertIsNotNone(owner)
        self.assertEqual(orphans, 0, "无主的知识库空间都挂到了企业下")

    def test_no_users_yet_skips_without_writing(self):
        with mock.patch("service.enterprise_bootstrap._ANY_ENTERPRISE_SQL", "SELECT id FROM organizations WHERE 1 = 0"), \
                mock.patch("service.enterprise_bootstrap._pick_owner", return_value=None):
            result = self.run_once()
        self.assertEqual(result["status"], "skipped")

    def test_can_be_switched_off(self):
        from service.enterprise_bootstrap import ensure_on_startup
        with mock.patch.dict(os.environ, {"AUTO_CREATE_ENTERPRISE": "0"}), \
                mock.patch("service.enterprise_bootstrap.ensure_default_enterprise") as ensure:
            self.assertIsNone(ensure_on_startup())
        ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
