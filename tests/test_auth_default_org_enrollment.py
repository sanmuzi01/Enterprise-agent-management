"""新用户注册自动加入默认企业（Phase 3B，docs/enterprise-rbac-plan.md）。

真实 DB，SMS 验证码校验打桩（跟 tests/test_auth_service.py 一样的做法），其余全部
走真实的 service.auth_service.register / auth_async_service.register + 真实
models/enterprise_dao.py，验证的是"注册成功后 organization_members 里真的多了一条"，
不是这个函数本身的纯逻辑（那部分见下面 EnrollInDefaultOrganizationDirectTest）。

之所以要单独补这个测试：本地开发库已经用 scripts/backfill_default_organization.py
回填过一次，那一步只覆盖了"回填时已存在的用户"，新注册的用户如果不在 register()
里自动入会，会悄悄漏掉——route 级测试的 create_user 是直接落库，不走 register()，
测不出这个问题。
"""
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import text

from models.init_db import SessionLocal
from service import auth_service
from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()


def _cleanup_user(name: str) -> None:
    db = SessionLocal()
    try:
        uid = db.execute(text("SELECT id FROM `user` WHERE name=:n"), {"n": name}).scalar()
        if uid is not None:
            db.execute(text("DELETE FROM organization_members WHERE user_id=:u"), {"u": uid})
            db.execute(text("DELETE FROM `user` WHERE id=:u"), {"u": uid})
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


class _DefaultOrg:
    """保证“默认企业”存在：本地开发库跑过回填脚本所以有，全新的库（CI）没有——没有它，注册时的自动入会是静默跳过的，这些测试就测不到东西。
    只有这里建了它才会在 close() 里删掉，不动本来就存在的。"""

    def __init__(self):
        from models.enterprise_dao import DEFAULT_ORG_NAME
        self.created_id = None
        self.owner = None
        db = SessionLocal()
        try:
            if db.execute(text("SELECT id FROM organizations WHERE name=:n LIMIT 1"), {"n": DEFAULT_ORG_NAME}).scalar() is None:
                self.owner = rc.create_user("enroll-owner")
                db.execute(text("INSERT INTO organizations (name, owner_user_id, status, created_at) VALUES (:n, :o, 'active', NOW())"),
                           {"n": DEFAULT_ORG_NAME, "o": self.owner["id"]})
                db.commit()
                self.created_id = db.execute(text("SELECT id FROM organizations WHERE name=:n ORDER BY id DESC LIMIT 1"), {"n": DEFAULT_ORG_NAME}).scalar()
        finally:
            db.close()

    def close(self):
        if self.created_id is None:
            return
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": self.created_id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": self.created_id})
            db.commit()
        finally:
            db.close()
        rc.cleanup()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class RegisterEnrollsDefaultOrganizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.default_org = _DefaultOrg()

    @classmethod
    def tearDownClass(cls):
        cls.default_org.close()

    def test_sync_register_adds_organization_members_row(self):
        name = f"rt_enroll_{uuid.uuid4().hex[:10]}"
        phone = f"138{uuid.uuid4().int % 10**8:08d}"
        db = SessionLocal()
        try:
            with patch("service.auth_service.verify_register_code", return_value=phone):
                result = auth_service.register(
                    db, name=name, password="Passw0rd!Enroll", age=30,
                    phone=phone, sms_code="000000", accepted_terms=True,
                )
            self.assertEqual(result.get("message"), "注册成功", result)
            user_id = result["user_id"]

            row = db.execute(
                text(
                    "SELECT er.scope, er.code FROM organization_members om "
                    "JOIN enterprise_role er ON om.role_id = er.id WHERE om.user_id=:u"
                ),
                {"u": user_id},
            ).first()
            self.assertEqual(tuple(row) if row else None, ("organization", "member"))
        finally:
            db.close()
            _cleanup_user(name)

    def test_async_register_adds_organization_members_row(self):
        from models.async_db import AsyncSessionLocal
        from service import auth_async_service
        from tests._async_helpers import run_async

        name = f"rt_enroll_async_{uuid.uuid4().hex[:10]}"
        phone = f"139{uuid.uuid4().int % 10**8:08d}"

        async def _do():
            async with AsyncSessionLocal() as db:
                with patch("service.auth_async_service.verify_register_code", return_value=phone):
                    result = await auth_async_service.register(
                        db, name=name, password="Passw0rd!Enroll", age=30,
                        phone=phone, sms_code="000000", accepted_terms=True,
                    )
                self.assertEqual(result.get("message"), "注册成功", result)
                user_id = result["user_id"]
                row = (
                    await db.execute(
                        text(
                            "SELECT er.scope, er.code FROM organization_members om "
                            "JOIN enterprise_role er ON om.role_id = er.id WHERE om.user_id=:u"
                        ),
                        {"u": user_id},
                    )
                ).first()
                self.assertEqual(tuple(row) if row else None, ("organization", "member"))

        try:
            run_async(_do())
        finally:
            _cleanup_user(name)


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class EnrollInDefaultOrganizationDirectTest(unittest.TestCase):
    """直接测 models/enterprise_dao.py 的两个函数本身，不经过 register()。"""

    @classmethod
    def setUpClass(cls):
        cls.default_org = _DefaultOrg()
        cls.user = rc.create_user("enroll-direct")

    @classmethod
    def tearDownClass(cls):
        cls.default_org.close()
        rc.cleanup()

    def test_missing_default_org_is_a_silent_noop_not_an_error(self):
        from models.enterprise_dao import enroll_in_default_organization

        db = SessionLocal()
        try:
            with patch("models.enterprise_dao.DEFAULT_ORG_NAME", "这个名字的企业不会存在-探测用"):
                enroll_in_default_organization(db, self.user["id"])  # 不应该抛异常
            row = db.execute(
                text("SELECT 1 FROM organization_members WHERE user_id=:u"), {"u": self.user["id"]}
            ).first()
            self.assertIsNone(row, "找不到默认企业时不应该插出任何行")
        finally:
            db.close()

    def test_calling_twice_does_not_raise(self):
        from models.enterprise_dao import enroll_in_default_organization

        db = SessionLocal()
        try:
            enroll_in_default_organization(db, self.user["id"])
            enroll_in_default_organization(db, self.user["id"])  # 第二次应该被唯一索引挡住但不抛异常
            count = db.execute(
                text("SELECT COUNT(*) FROM organization_members WHERE user_id=:u"), {"u": self.user["id"]}
            ).scalar()
            self.assertEqual(count, 1)
        finally:
            db.execute(text("DELETE FROM organization_members WHERE user_id=:u"), {"u": self.user["id"]})
            db.commit()
            db.close()


if __name__ == "__main__":
    unittest.main()
