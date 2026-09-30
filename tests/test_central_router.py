"""Phase 3D 阶段3：中央 Agent 受控路由（service/runtime/central_router.py）。

`match_department` 是纯函数，直接测。`resolve_target_agent[_async]` 需要真实 DB
（要查 Agent 表 + access_control 的可用性判断），真实 DB，同步+异步都测。

回归重点：现存 Agent 全部是 agent_type="personal"，路由函数对它们必须是原样
返回 agent_id 的空操作——这是这次改造"不影响现有行为"的核心承诺，两个
test_non_central_agent_* 就是专门盯着这条。
"""
import unittest

from models.init_db import Agent, Conversation, SessionLocal
from service.runtime import central_router
from tests import _route_client as rc
from tests._async_helpers import run_async as _run

_AVAILABLE, _WHY = rc.route_tests_available()


class MatchDepartmentTest(unittest.TestCase):
    def test_hr_keywords(self):
        self.assertEqual(central_router.match_department("我想请假三天"), "hr")
        self.assertEqual(central_router.match_department("新员工入职流程是什么"), "hr")

    def test_procurement_keywords(self):
        self.assertEqual(central_router.match_department("这个供应商的库存够吗"), "procurement")

    def test_sales_keywords(self):
        self.assertEqual(central_router.match_department("帮我查一下这个客户的商机"), "sales")

    def test_department_without_template_has_no_keywords(self):
        # finance/it 只在 VALID_DEPARTMENT_CODES 里占了个位置，没有模板、没有
        # routing_keywords，命中不了——这是能力目录化改造要验证的新行为。
        self.assertIsNone(central_router.match_department("这笔报销预算超了吗"))
        self.assertIsNone(central_router.match_department("我的账号登不上去，报故障"))

    def test_no_match_returns_none(self):
        self.assertIsNone(central_router.match_department("今天天气怎么样"))

    def test_empty_message_returns_none(self):
        self.assertIsNone(central_router.match_department(""))
        self.assertIsNone(central_router.match_department(None))


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class ResolveTargetAgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = rc.create_user("router-owner")
        cls.outsider = rc.create_user("router-outsider")

        db = SessionLocal()
        try:
            # 路由只认已发布（published）的中央/部门 Agent——草稿状态的不参与路由，
            # 见 docs/enterprise-rbac-plan.md 第20节。这里的 central/hr_dept/
            # unowned_hr_dept 都显式发布，好让"该不该路由"这条独立于"发没发布"
            # 这条来测；draft_dept 专门测发布状态门禁本身。
            central = Agent(user_id=cls.owner["id"], name="router-central", agent_type="central",
                             lifecycle_status="published")
            personal = Agent(user_id=cls.owner["id"], name="router-personal")  # 默认 personal
            hr_dept = Agent(user_id=cls.owner["id"], name="router-hr-dept",
                             agent_type="department", department_code="hr", lifecycle_status="published")
            unowned_procurement_dept = Agent(user_id=cls.outsider["id"], name="router-procurement-dept-other-owner",
                                              agent_type="department", department_code="procurement",
                                              lifecycle_status="published")
            draft_sales_dept = Agent(user_id=cls.owner["id"], name="router-sales-draft",
                                      agent_type="department", department_code="sales", lifecycle_status="draft")
            db.add_all([central, personal, hr_dept, unowned_procurement_dept, draft_sales_dept])
            db.commit()
            cls.central_id = central.id
            cls.personal_id = personal.id
            cls.hr_dept_id = hr_dept.id
            cls.unowned_procurement_dept_id = unowned_procurement_dept.id
            cls.draft_sales_dept_id = draft_sales_dept.id

            conv = Conversation(user_id=cls.owner["id"], agent_id=cls.central_id, title="existing")
            db.add(conv)
            db.commit()
            cls.existing_conversation_id = conv.id
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        from sqlalchemy import text
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM conversation WHERE id=:i"), {"i": cls.existing_conversation_id})
            db.execute(text("DELETE FROM agent WHERE id IN (:a,:b,:c,:d,:e)"),
                       {"a": cls.central_id, "b": cls.personal_id, "c": cls.hr_dept_id,
                        "d": cls.unowned_procurement_dept_id, "e": cls.draft_sales_dept_id})
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        rc.cleanup()

    # ---------------- 同步 ----------------

    def test_central_agent_routes_to_owned_department_agent(self):
        db = SessionLocal()
        try:
            target = central_router.resolve_target_agent(
                db, self.owner["id"], self.central_id, "我想请假两天", None,
            )
            self.assertEqual(target, self.hr_dept_id)
        finally:
            db.close()

    def test_central_agent_answers_itself_when_department_has_no_routing_keywords(self):
        db = SessionLocal()
        try:
            # finance 没有模板/routing_keywords（能力目录化改造后的新行为）——
            # match_department 直接返回 None，根本不会触发部门 Agent 查询。
            target = central_router.resolve_target_agent(
                db, self.owner["id"], self.central_id, "这笔报销预算超了吗", None,
            )
            self.assertEqual(target, self.central_id)
        finally:
            db.close()

    def test_central_agent_falls_back_when_department_agent_not_usable(self):
        db = SessionLocal()
        try:
            # procurement 关键词命中，存在 department_code="procurement" 的 Agent，
            # 但属于另一个不相关的用户（personal 默认 scope，没共享），owner 用不了
            # 它——应该退回中央 Agent 自己回答。
            target = central_router.resolve_target_agent(
                db, self.owner["id"], self.central_id, "这个供应商的库存够吗", None,
            )
            self.assertEqual(target, self.central_id)
        finally:
            db.close()

    def test_central_agent_no_keyword_match_answers_itself(self):
        db = SessionLocal()
        try:
            target = central_router.resolve_target_agent(
                db, self.owner["id"], self.central_id, "今天天气怎么样", None,
            )
            self.assertEqual(target, self.central_id)
        finally:
            db.close()

    def test_non_central_agent_is_untouched_regardless_of_message(self):
        # 回归重点：personal 类型的 Agent（现存 Agent 的默认值）不应该被路由改写，
        # 即使消息内容命中了某个部门的关键词。
        db = SessionLocal()
        try:
            target = central_router.resolve_target_agent(
                db, self.owner["id"], self.personal_id, "我想请假两天", None,
            )
            self.assertEqual(target, self.personal_id)
        finally:
            db.close()

    def test_existing_conversation_reuses_its_own_agent_not_rerouted(self):
        db = SessionLocal()
        try:
            # 已有会话固定用 central_id 创建；即使这次消息命中别的部门关键词，也不重新路由，
            # 沿用会话创建时定下的 agent_id。
            target = central_router.resolve_target_agent(
                db, self.owner["id"], self.central_id, "我想请假两天",
                self.existing_conversation_id,
            )
            self.assertEqual(target, self.central_id)
        finally:
            db.close()

    def test_draft_department_agent_is_not_matched_even_if_usable(self):
        # sales 部门 Agent 存在、owner 用得了它，但还是草稿状态——路由只认已发布的
        # 部门 Agent，应该退回中央 Agent 自己回答，不是"没找到部门 Agent"那种日志，
        # 是"找到了但没发布"。
        db = SessionLocal()
        try:
            target = central_router.resolve_target_agent(
                db, self.owner["id"], self.central_id, "帮我查一下这个客户的商机", None,
            )
            self.assertEqual(target, self.central_id)
        finally:
            db.close()

    def test_draft_central_agent_does_not_route_at_all(self):
        # 中央 Agent 自己还是草稿状态——不应该触发任何路由逻辑，原样返回，即使
        # 消息命中了关键词、对应部门 Agent 也已经发布。
        db = SessionLocal()
        try:
            draft_central = Agent(user_id=self.owner["id"], name="router-central-draft",
                                   agent_type="central", lifecycle_status="draft")
            db.add(draft_central)
            db.commit()
            draft_central_id = draft_central.id
            try:
                target = central_router.resolve_target_agent(
                    db, self.owner["id"], draft_central_id, "我想请假两天", None,
                )
                self.assertEqual(target, draft_central_id)
            finally:
                from sqlalchemy import text
                db.execute(text("DELETE FROM agent WHERE id=:i"), {"i": draft_central_id})
                db.commit()
        finally:
            db.close()

    # ---------------- 异步 ----------------

    def test_async_central_agent_routes_to_owned_department_agent(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                target = await central_router.resolve_target_agent_async(
                    db, self.owner["id"], self.central_id, "我想请假两天", None,
                )
                self.assertEqual(target, self.hr_dept_id)
        _run(_do())

    def test_async_draft_department_agent_is_not_matched(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                target = await central_router.resolve_target_agent_async(
                    db, self.owner["id"], self.central_id, "帮我查一下这个客户的商机", None,
                )
                self.assertEqual(target, self.central_id)
        _run(_do())

    def test_async_non_central_agent_is_untouched(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                target = await central_router.resolve_target_agent_async(
                    db, self.owner["id"], self.personal_id, "我想请假两天", None,
                )
                self.assertEqual(target, self.personal_id)
        _run(_do())


if __name__ == "__main__":
    unittest.main()
