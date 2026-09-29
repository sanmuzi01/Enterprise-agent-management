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
from unittest.mock import patch

from sqlalchemy import text

from models.init_db import EnterpriseRole, KnowledgeSpace, Organization, OrganizationMember, SessionLocal
from service import approval_service
from service.enterprise_access import require_org_role_async
from service.exceptions import Conflict, InvalidInput, NotFound
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
        用真正的 asyncio.gather 并发调用测，不是顺序调两次。

        第五轮审计 P1-5 之后 try_consume_approved 本身不再 commit（必须跟调用方
        真正的业务操作一起提交，见该函数文档），这里模拟真实调用方的用法——拿到
        非 None 就当作"业务操作也做完了"紧接着 commit，不commit的那一侧（loser）
        必须能安全丢弃：拿到 None 之后什么都不用做，事务被 AsyncSessionLocal 的
        __aexit__ 自动回滚，不影响下次重新消费。"""
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
                    consumed = await approval_service.try_consume_approved(
                        db, "test_resource.delete", "test_resource", 9008,
                    )
                    if consumed is not None:
                        await db.commit()  # 模拟"消费+真正的业务操作"一起提交
                    return consumed

            results = await asyncio.gather(_consume(), _consume())
            winners = [r for r in results if r is not None]
            losers = [r for r in results if r is None]
            self.assertEqual(len(winners), 1)
            self.assertEqual(len(losers), 1)
            self.assertEqual(winners[0], pending["id"])
        _run(_do())

    def test_uncommitted_consume_can_be_retried(self):
        """第五轮审计 P1-5 的核心场景：consume 成功但调用方没有提交（模拟真正的业务
        操作失败），审批必须恢复成可以重新消费的状态，不能卡死。"""
        async def _do():
            from models.async_db import AsyncSessionLocal

            async with AsyncSessionLocal() as setup_db:
                pending = await approval_service.request_or_get_pending(
                    setup_db, self.applicant["id"], "test_resource.delete", "test_resource", 9014,
                )
                await approval_service.decide(setup_db, pending["id"], self.approver["id"], approve=True)

            # 第一次：消费成功，但模拟真正的删除操作失败——不 commit，直接让 session 关闭。
            async with AsyncSessionLocal() as db:
                consumed = await approval_service.try_consume_approved(
                    db, "test_resource.delete", "test_resource", 9014,
                )
                self.assertEqual(consumed, pending["id"])
                # 故意不 commit，模拟真正的业务操作抛异常——session 关闭时自动回滚。

            # 第二次：换一个全新 session 重试，应该能重新消费到同一条审批单
            # （证明上面那次没有真正提交的消费，被完整回滚了，不是卡死状态）。
            async with AsyncSessionLocal() as db2:
                retried = await approval_service.try_consume_approved(
                    db2, "test_resource.delete", "test_resource", 9014,
                )
                self.assertEqual(retried, pending["id"])
                await db2.commit()
        _run(_do())

    def test_concurrent_request_only_creates_one_row(self):
        """P1 并发修复：两个并发请求同时给同一个 (action, resource_type, resource_id)
        申请审批，用真正的 asyncio.gather，不是顺序调两次——之前"先查一遍没有活跃单
        才插入"两个都能通过检查，都会各自插一条，堆出重复审批单。现在数据库唯一约束
        （active_dedupe_key）兜底：一个真的插入成功，另一个撞唯一键后回滚重查，两边
        拿到的必须是同一条记录，且数据库里最终只有一行。"""
        async def _do():
            import asyncio
            from models.async_db import AsyncSessionLocal
            from sqlalchemy import select as sa_select
            from models.init_db import ApprovalRequest

            async def _request():
                async with AsyncSessionLocal() as db:
                    return await approval_service.request_or_get_pending(
                        db, self.applicant["id"], "test_resource.delete", "test_resource", 9009,
                    )

            results = await asyncio.gather(_request(), _request())
            self.assertEqual(results[0]["id"], results[1]["id"], "两边必须拿到同一条审批单")

            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    sa_select(ApprovalRequest).where(
                        ApprovalRequest.action == "test_resource.delete",
                        ApprovalRequest.resource_type == "test_resource",
                        ApprovalRequest.resource_id == 9009,
                    )
                )).scalars().all()
            self.assertEqual(len(rows), 1, "数据库里必须只有一条，不能是两条重复审批单")
        _run(_do())

    def test_concurrent_decide_only_one_wins(self):
        """P1 并发修复：两个管理员同时对同一条 pending 单一个批准一个拒绝，用真正的
        asyncio.gather——之前"读一次、判断、逐字段赋值、再 commit"不是原子的，两边都
        能读到 pending，最后提交的会悄悄覆盖先提交的。现在改成条件 UPDATE，只能有
        一个成功，另一个必须报 Conflict，不能出现"两个都成功但状态互相矛盾"的情况。
        """
        async def _do():
            import asyncio
            from models.async_db import AsyncSessionLocal

            async with AsyncSessionLocal() as setup_db:
                pending = await approval_service.request_or_get_pending(
                    setup_db, self.applicant["id"], "test_resource.delete", "test_resource", 9010,
                )

            async def _decide(approve):
                async with AsyncSessionLocal() as db:
                    try:
                        return await approval_service.decide(db, pending["id"], self.approver["id"], approve=approve)
                    except (Conflict, InvalidInput):
                        # 具体报哪个取决于两边真实的执行时序：输家的初始 SELECT 如果
                        # 发生在赢家提交之后，会在最前面的 `status != "pending"` 检查
                        # 就直接被拒（InvalidInput）；如果发生在赢家提交之前、只是
                        # 写的时候慢了一步，会在条件 UPDATE 那里报 Conflict。两种都是
                        # "正确识别出自己没抢到、没有覆盖对方"的安全结果，不区分。
                        return None

            results = await asyncio.gather(_decide(True), _decide(False))
            winners = [r for r in results if r is not None]
            losers = [r for r in results if r is None]
            self.assertEqual(len(winners), 1, "两个人同时决定同一条单，必须只有一个成功")
            self.assertEqual(len(losers), 1, "另一个必须报 Conflict，不能悄悄把前一个的结果覆盖掉")

            async with AsyncSessionLocal() as db:
                from models.init_db import ApprovalRequest
                row = await db.get(ApprovalRequest, pending["id"])
                self.assertEqual(row.status, winners[0]["status"], "数据库最终状态必须跟胜出者返回的状态一致")
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

    def test_delete_failure_after_consume_can_be_retried(self):
        """第五轮审计 P1-5 核心场景：审批被消费之后，真正的删除操作失败——修复前
        `try_consume_approved` 会自己单独 commit，这里模拟的失败会导致"审批用掉了
        但空间没删，还申请不了新审批（dedupe key 还占着）"的卡死状态。修复后：
        消费和真正删除在同一个事务里，删除失败时消费也跟着回滚，重试不需要重新
        申请审批，直接就能重新消费、真的删掉。"""
        async def _do():
            from models.async_db import AsyncSessionLocal
            from models.init_db import User
            from service.knowledge_space import space_async_service

            db = SessionLocal()
            try:
                space = KnowledgeSpace(user_id=self.owner["id"], name="appr-retry-space")
                db.add(space)
                db.commit()
                space_id = space.id
            finally:
                db.close()

            try:
                async with AsyncSessionLocal() as db1:
                    first = await space_async_service.delete_space(db1, self.owner["id"], space_id)
                    approval_id = first["approval"]["id"]

                async with AsyncSessionLocal() as db2:
                    admin_user = await db2.get(User, self.admin["id"])
                    await approval_service.decide(db2, approval_id, admin_user.id, approve=True)

                # 模拟真正删除那一步失败（比如数据库故障、进程崩溃）。
                with patch(
                    "models.knowledge_space_async_dao.delete_space_async",
                    side_effect=RuntimeError("模拟数据库故障"),
                ):
                    async with AsyncSessionLocal() as db3:
                        with self.assertRaises(RuntimeError):
                            await space_async_service.delete_space(db3, self.owner["id"], space_id)

                # 空间还在——没有真的删掉。
                check_db = SessionLocal()
                try:
                    self.assertIsNotNone(check_db.get(KnowledgeSpace, space_id))
                finally:
                    check_db.close()

                # 重试：如果上次失败的消费没有被回滚（旧 bug），这里会因为审批已经被
                # "用掉"、又申请不了新单（dedupe key 还占着）而卡死在待审批分支，
                # 拿不到"已删除"。
                async with AsyncSessionLocal() as db4:
                    retried = await space_async_service.delete_space(db4, self.owner["id"], space_id)
                    self.assertEqual(retried.get("message"), "已删除")

                check_db = SessionLocal()
                try:
                    self.assertIsNone(check_db.get(KnowledgeSpace, space_id))
                finally:
                    check_db.close()
            finally:
                cleanup_db = SessionLocal()
                try:
                    cleanup_db.execute(text("DELETE FROM knowledge_spaces WHERE id=:s"), {"s": space_id})
                    cleanup_db.commit()
                except Exception:  # noqa: BLE001
                    cleanup_db.rollback()
                finally:
                    cleanup_db.close()
        _run(_do())


if __name__ == "__main__":
    unittest.main()
