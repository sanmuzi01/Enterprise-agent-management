"""Phase 3D 阶段4：`service/approval_service.py` + `require_org_role_async`
（docs/enterprise-rbac-plan.md 9.5）。真实 DB，全异步。

分两块：
  1. `ApprovalServiceTest`：审批单本身的生命周期（申请去重/审批/拒绝/过期/消费），
     用一个不存在的 resource_type="test_resource" 测，不依赖任何真实业务资源。
  2. `SpaceDeleteApprovalIntegrationTest`：证明 `space_async_service.delete_space`
     真的接上了这条审批链——第一次删除只建审批单不删数据，管理员批准后第二次删除
     才真的执行。这是唯一一个已经接了审批的真实高风险操作（docs 9.5 表格里其余几项
     还没有对应的真实路由，见文档"仍未收口"的说明）。
"""
import unittest

from sqlalchemy import text

from models.init_db import EnterpriseRole, KnowledgeSpace, Organization, OrganizationMember, SessionLocal
from service import approval_service
from service.enterprise_access import require_org_role_async
from service.exceptions import InvalidInput, NotFound
from tests import _route_client as rc
from tests._async_helpers import run_async as _run

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class ApprovalServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.applicant = rc.create_user("appr-applicant")
        cls.approver = rc.create_user("appr-approver")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()

    def test_request_is_idempotent_while_pending(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                first = await approval_service.request_or_get_pending(
                    db, self.applicant["id"], "test_resource.delete", "test_resource", 9001,
                    reason="第一次申请",
                )
                second = await approval_service.request_or_get_pending(
                    db, self.applicant["id"], "test_resource.delete", "test_resource", 9001,
                    reason="第二次申请，应该复用同一条",
                )
                self.assertEqual(first["id"], second["id"])
                self.assertEqual(second["reason"], "第一次申请")  # 没有被第二次调用覆盖
        _run(_do())

    def test_consume_before_approval_returns_none(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                pending = await approval_service.request_or_get_pending(
                    db, self.applicant["id"], "test_resource.delete", "test_resource", 9002,
                )
                self.assertEqual(pending["status"], "pending")
                consumed = await approval_service.try_consume_approved(
                    db, "test_resource.delete", "test_resource", 9002,
                )
                self.assertIsNone(consumed)
        _run(_do())

    def test_approve_then_consume_once(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                pending = await approval_service.request_or_get_pending(
                    db, self.applicant["id"], "test_resource.delete", "test_resource", 9003,
                )
                decided = await approval_service.decide(db, pending["id"], self.approver["id"], approve=True)
                self.assertEqual(decided["status"], "approved")
                self.assertEqual(decided["approver_id"], self.approver["id"])

                consumed_id = await approval_service.try_consume_approved(
                    db, "test_resource.delete", "test_resource", 9003,
                )
                self.assertEqual(consumed_id, pending["id"])

                # 消费过一次之后不能再消费第二次——防止同一张审批单被拿去执行两次操作
                consumed_again = await approval_service.try_consume_approved(
                    db, "test_resource.delete", "test_resource", 9003,
                )
                self.assertIsNone(consumed_again)
        _run(_do())

    def test_reject_cannot_be_consumed(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                pending = await approval_service.request_or_get_pending(
                    db, self.applicant["id"], "test_resource.delete", "test_resource", 9004,
                )
                decided = await approval_service.decide(db, pending["id"], self.approver["id"], approve=False)
                self.assertEqual(decided["status"], "rejected")
                self.assertIsNone(
                    await approval_service.try_consume_approved(db, "test_resource.delete", "test_resource", 9004)
                )
        _run(_do())

    def test_decide_twice_raises(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                pending = await approval_service.request_or_get_pending(
                    db, self.applicant["id"], "test_resource.delete", "test_resource", 9005,
                )
                await approval_service.decide(db, pending["id"], self.approver["id"], approve=True)
                with self.assertRaises(InvalidInput):
                    await approval_service.decide(db, pending["id"], self.approver["id"], approve=True)
        _run(_do())

    def test_cannot_approve_own_request(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            from service.exceptions import PermissionDenied
            async with AsyncSessionLocal() as db:
                pending = await approval_service.request_or_get_pending(
                    db, self.applicant["id"], "test_resource.delete", "test_resource", 9007,
                )
                with self.assertRaises(PermissionDenied):
                    await approval_service.decide(db, pending["id"], self.applicant["id"], approve=True)
                # 拒绝之后单子还是 pending，没有被"尝试自审批"这次调用带偏状态
                from models.init_db import ApprovalRequest
                still_pending = await db.get(ApprovalRequest, pending["id"])
                self.assertEqual(still_pending.status, "pending")
        _run(_do())

    def test_concurrent_consume_only_one_wins(self):
        """真实并发：两个独立会话同时对同一条已批准审批单调用 try_consume_approved，
        只能有一个拿到非 None——这是"先查后改"改成条件 UPDATE 之后要保证的行为，
        用真正的 asyncio.gather 并发调用测，不是顺序调两次。"""
        async def _do():
            import asyncio
            from models.async_db import AsyncSessionLocal

            async with AsyncSessionLocal() as setup_db:
                pending = await approval_service.request_or_get_pending(
                    setup_db, self.applicant["id"], "test_resource.delete", "test_resource", 9008,
                )
                await approval_service.decide(setup_db, pending["id"], self.approver["id"], approve=True)

            async def _consume():
                async with AsyncSessionLocal() as db:
                    return await approval_service.try_consume_approved(
                        db, "test_resource.delete", "test_resource", 9008,
                    )

            results = await asyncio.gather(_consume(), _consume())
            winners = [r for r in results if r is not None]
            losers = [r for r in results if r is None]
            self.assertEqual(len(winners), 1)
            self.assertEqual(len(losers), 1)
            self.assertEqual(winners[0], pending["id"])
        _run(_do())

    def test_decide_unknown_id_raises_not_found(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                with self.assertRaises(NotFound):
                    await approval_service.decide(db, 9999999, self.approver["id"], approve=True)
        _run(_do())

    def test_expired_request_cannot_be_decided_or_consumed(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                pending = await approval_service.request_or_get_pending(
                    db, self.applicant["id"], "test_resource.delete", "test_resource", 9006,
                )
                # 直接把过期时间改到过去，不用真的等 72 小时
                from models.init_db import ApprovalRequest
                from utils.timeutil import utcnow
                from datetime import timedelta
                row = await db.get(ApprovalRequest, pending["id"])
                row.expires_at = utcnow() - timedelta(hours=1)
                await db.commit()

                with self.assertRaises(InvalidInput):
                    await approval_service.decide(db, pending["id"], self.approver["id"], approve=True)

                # decide() 会把过期的单子标成 expired，新申请不能复用它，得建一条新的
                fresh = await approval_service.request_or_get_pending(
                    db, self.applicant["id"], "test_resource.delete", "test_resource", 9006,
                )
                self.assertNotEqual(fresh["id"], pending["id"])
        _run(_do())


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class RequireOrgRoleAsyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = rc.create_user("appr-org-owner")
        cls.member = rc.create_user("appr-org-member")
        cls.outsider = rc.create_user("appr-org-outsider")

        db = SessionLocal()
        try:
            org = Organization(name="appr-test-org", owner_user_id=cls.owner["id"])
            db.add(org)
            db.commit()
            cls.org_id = org.id
            owner_role_id = db.query(EnterpriseRole.id).filter_by(scope="organization", code="owner").scalar()
            member_role_id = db.query(EnterpriseRole.id).filter_by(scope="organization", code="member").scalar()
            db.add(OrganizationMember(organization_id=cls.org_id, user_id=cls.owner["id"],
                                       role_id=owner_role_id, status="active"))
            db.add(OrganizationMember(organization_id=cls.org_id, user_id=cls.member["id"],
                                       role_id=member_role_id, status="active"))
            db.commit()
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org_id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org_id})
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        rc.cleanup()

    def test_owner_passes_admin_requirement(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            from models.init_db import User
            async with AsyncSessionLocal() as db:
                user = await db.get(User, self.owner["id"])
                dep = require_org_role_async("admin")
                result = await dep(current_user=user, db=db)
                self.assertEqual(result.id, self.owner["id"])
        _run(_do())

    def test_plain_member_fails_admin_requirement(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            from models.init_db import User
            from service.exceptions import PermissionDenied
            async with AsyncSessionLocal() as db:
                user = await db.get(User, self.member["id"])
                dep = require_org_role_async("admin")
                with self.assertRaises(PermissionDenied):
                    await dep(current_user=user, db=db)
        _run(_do())

    def test_outsider_is_not_found(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            from models.init_db import User
            async with AsyncSessionLocal() as db:
                user = await db.get(User, self.outsider["id"])
                dep = require_org_role_async("member")
                with self.assertRaises(NotFound):
                    await dep(current_user=user, db=db)
        _run(_do())


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class SpaceDeleteApprovalIntegrationTest(unittest.TestCase):
    """证明 knowledge_space.delete_space 真的接上了审批：空文档空间第一次删除
    只建审批单不删数据，管理员批准后第二次删除才真的执行。"""

    @classmethod
    def setUpClass(cls):
        cls.owner = rc.create_user("appr-space-owner")
        cls.admin = rc.create_user("appr-space-admin")

        db = SessionLocal()
        try:
            org = Organization(name="appr-space-org", owner_user_id=cls.admin["id"])
            db.add(org)
            db.commit()
            cls.org_id = org.id
            admin_role_id = db.query(EnterpriseRole.id).filter_by(scope="organization", code="admin").scalar()
            db.add(OrganizationMember(organization_id=cls.org_id, user_id=cls.admin["id"],
                                       role_id=admin_role_id, status="active"))
            db.commit()

            space = KnowledgeSpace(user_id=cls.owner["id"], name="appr-test-space")
            db.add(space)
            db.commit()
            cls.space_id = space.id
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM knowledge_spaces WHERE id=:s"), {"s": cls.space_id})
            db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org_id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org_id})
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        rc.cleanup()

    def test_delete_requires_approval_then_actually_deletes(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            from models.init_db import User
            from service.knowledge_space import space_async_service

            async with AsyncSessionLocal() as db:
                first = await space_async_service.delete_space(db, self.owner["id"], self.space_id)
                self.assertIn("approval", first)
                self.assertEqual(first["approval"]["status"], "pending")
                approval_id = first["approval"]["id"]

            # 空间应该还在——第一次调用只是建了审批单，没有真的删
            check_db = SessionLocal()
            try:
                self.assertIsNotNone(check_db.get(KnowledgeSpace, self.space_id))
            finally:
                check_db.close()

            async with AsyncSessionLocal() as db:
                admin_user = await db.get(User, self.admin["id"])
                await approval_service.decide(db, approval_id, admin_user.id, approve=True)

            async with AsyncSessionLocal() as db:
                second = await space_async_service.delete_space(db, self.owner["id"], self.space_id)
                self.assertEqual(second.get("message"), "已删除")

            check_db = SessionLocal()
            try:
                self.assertIsNone(check_db.get(KnowledgeSpace, self.space_id))
            finally:
                check_db.close()
        _run(_do())


if __name__ == "__main__":
    unittest.main()
