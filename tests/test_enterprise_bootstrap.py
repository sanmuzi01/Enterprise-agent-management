"""全新部署第一次启动自动建企业（service/enterprise_bootstrap.py）。

在开发库（已有企业）上跑：验证幂等、不会建第二个；在空库（CI、scripts/test_fresh_db.py）上跑：
真正走一遍创建——所有者是平台管理员、所有现有用户加入、无主的知识库空间挂到企业下——测完把建出来的数据删掉。
并发：多个线程同时初始化（带锁、以及绕过锁只靠数据库哨兵行）都只建出一家企业；没拿到锁时跳过，不在没锁的情况下继续。
"""
import os
import threading
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
            self.orgs_before = {r[0] for r in db.execute(text("SELECT id FROM organizations")).all()}
            self.had_enterprise = bool(self.orgs_before)
            self.had_marker = bool(db.execute(text("SELECT 1 FROM bootstrap_marker WHERE name = 'default_enterprise'")).scalar())
            self.orphan_spaces = [r[0] for r in db.execute(text("SELECT id FROM knowledge_spaces WHERE organization_id IS NULL")).all()]
        self.created = None

    def tearDown(self):
        # 把这次建出来的东西还原（包括并发测试万一建出的多家），空库还是空库
        with self.SessionLocal() as db:
            new_orgs = [r[0] for r in db.execute(text("SELECT id FROM organizations")).all() if r[0] not in self.orgs_before]
            if new_orgs and self.orphan_spaces:
                db.execute(text("UPDATE knowledge_spaces SET organization_id = NULL WHERE id IN :ids").bindparams(
                    __import__("sqlalchemy").bindparam("ids", expanding=True)), {"ids": self.orphan_spaces})
            for org in new_orgs:
                db.execute(text("DELETE FROM organization_members WHERE organization_id = :o"), {"o": org})
                db.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org})
            if not self.had_marker:
                db.execute(text("DELETE FROM bootstrap_marker WHERE name = 'default_enterprise'"))
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

    def _in_parallel(self, fn, n=6):
        barrier, results, errors = threading.Barrier(n), [], []

        def worker():
            try:
                barrier.wait(timeout=10)
                results.append(fn())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        self.assertEqual(errors, [])
        with self.SessionLocal() as db:
            count = db.execute(text("SELECT COUNT(*) FROM organizations")).scalar()
        return results, count

    def test_concurrent_startups_create_exactly_one_enterprise(self):
        if self.had_enterprise:
            self.skipTest("库里已经有企业：并发创建只在空库上验证（CI、scripts/test_fresh_db.py）")
        from service.enterprise_bootstrap import ensure_on_startup
        with rc.admin_env(self.admin["name"]):
            results, count = self._in_parallel(ensure_on_startup)
        self.created = True
        self.assertEqual(count, 1, "多个进程同时启动只能建出一家企业")
        self.assertEqual([r["status"] for r in results].count("created"), 1, results)

    def test_without_the_lock_the_database_marker_still_allows_only_one(self):
        """命名锁失效（被绕过、超时后有人继续）时的兜底：同时直接跑初始化，只靠哨兵行的主键也只建一家。"""
        if self.had_enterprise:
            self.skipTest("库里已经有企业：并发创建只在空库上验证（CI、scripts/test_fresh_db.py）")
        with rc.admin_env(self.admin["name"]):
            results, count = self._in_parallel(self.run_once)
        self.created = True
        self.assertEqual(count, 1, "绕过锁并发初始化也只能建出一家企业")
        statuses = [r["status"] for r in results]
        self.assertEqual(statuses.count("created"), 1, results)
        self.assertTrue(all(s in ("created", "exists", "skipped") for s in statuses), results)

    def test_skips_when_the_lock_is_not_acquired(self):
        from service import enterprise_bootstrap
        for lock_result in (0, None):   # 0 = 等超时，NULL = 出错
            conn = mock.MagicMock()
            conn.execute.return_value.scalar.return_value = lock_result
            with mock.patch("models.init_db.engine") as engine, \
                    mock.patch.object(enterprise_bootstrap, "ensure_default_enterprise") as ensure:
                engine.connect.return_value = conn
                result = enterprise_bootstrap.ensure_on_startup()
            self.assertEqual(result["status"], "skipped", lock_result)
            ensure.assert_not_called()
            sqls = [str(c.args[0]) for c in conn.execute.call_args_list]
            self.assertFalse(any("RELEASE_LOCK" in s for s in sqls), "没拿到的锁不能去释放")
            conn.close.assert_called_once()

    def test_marker_left_but_enterprise_deleted_is_not_recreated(self):
        """以前自动建过、后来企业记录被人删了：不自动重建（那是人为操作），只提示手动处理。"""
        with self.SessionLocal() as db:
            if not self.had_marker:
                db.execute(text("INSERT INTO bootstrap_marker (name, done_at) VALUES ('default_enterprise', NOW())"))
                db.commit()
            before = db.execute(text("SELECT COUNT(*) FROM organizations")).scalar()
        with mock.patch("service.enterprise_bootstrap._ANY_ENTERPRISE_SQL", "SELECT id FROM organizations WHERE 1 = 0"):
            result = self.run_once()
        self.assertEqual(result["status"], "skipped")
        self.assertIn("手动", result["reason"])
        with self.SessionLocal() as db:
            self.assertEqual(db.execute(text("SELECT COUNT(*) FROM organizations")).scalar(), before)

    def test_can_be_switched_off(self):
        from service.enterprise_bootstrap import ensure_on_startup
        with mock.patch.dict(os.environ, {"AUTO_CREATE_ENTERPRISE": "0"}), \
                mock.patch("service.enterprise_bootstrap.ensure_default_enterprise") as ensure:
            self.assertIsNone(ensure_on_startup())
        ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
