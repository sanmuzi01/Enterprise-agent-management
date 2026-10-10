"""中央 Agent 路由计划：多部门命中的排序与备选、没转出去的原因、转交记录与管理端查询。"""
import unittest

from sqlalchemy import text

from models.init_db import Agent, AgentHandoff, SessionLocal
from service.runtime import central_router
from tests import _route_client as rc
from tests._async_helpers import run_async

_AVAILABLE, _WHY = rc.route_tests_available()


class MatchDepartmentsTest(unittest.TestCase):
    def test_more_keyword_hits_rank_first(self):
        matches = central_router.match_departments("我想请假，顺便查下这笔报销预算")
        self.assertEqual([code for code, _ in matches], ["finance", "hr"])
        self.assertEqual(dict(matches)["finance"], ["预算", "报销"])

    def test_ties_follow_template_order(self):
        # 请假(hr) 和 库存(procurement) 各命中 1 个词：按模板声明顺序 hr 在前。
        self.assertEqual([c for c, _ in central_router.match_departments("请假 库存")], ["hr", "procurement"])

    def test_single_department_and_none(self):
        self.assertEqual([c for c, _ in central_router.match_departments("帮我跟进客户")], ["sales"])
        self.assertEqual(central_router.match_departments("今天天气怎么样"), [])
        self.assertEqual(central_router.match_departments(None), [])

    def test_match_department_uses_strongest(self):
        self.assertEqual(central_router.match_department("我想请假，顺便查下这笔报销预算"), "finance")


def run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as db:
            return await fn(db)
    return run_async(go())


@unittest.skipUnless(_AVAILABLE, _WHY)
class RoutePlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = rc.create_user("plan-owner")
        cls.other = rc.create_user("plan-other")
        cls.db = SessionLocal()
        cls.agents = {}

        def make(key, user, **kw):
            agent = Agent(user_id=user["id"], name=f"plan-{key}", **kw)
            cls.db.add(agent)
            cls.db.commit()
            cls.agents[key] = agent.id

        make("central", cls.owner, agent_type="central", lifecycle_status="published")
        make("hr", cls.owner, agent_type="department", department_code="hr", lifecycle_status="published")
        make("finance", cls.owner, agent_type="department", department_code="finance", lifecycle_status="published")
        make("procurement", cls.other, agent_type="department", department_code="procurement",
             lifecycle_status="published")
        make("personal", cls.owner)

    @classmethod
    def tearDownClass(cls):
        cls.db.execute(text("DELETE FROM agent_handoff WHERE user_id IN (:a, :b)"),
                       {"a": cls.owner["id"], "b": cls.other["id"]})
        cls.db.commit()
        cls.db.close()
        # 流式对话测试会产生会话、运行记录，用户清理会按外键顺序连同这些 Agent 一起删干净。
        rc.cleanup()

    def plan(self, message, agent="central", conversation_id=None):
        return run_db(lambda db: central_router.plan_route_async(
            db, self.owner["id"], self.agents[agent], message, conversation_id))

    def test_multi_department_routes_to_strongest_and_lists_alternatives(self):
        plan = self.plan("我想请假，顺便查下这笔报销预算")
        self.assertEqual((plan.reason, plan.department_code), ("routed", "finance"))
        self.assertEqual(plan.target_agent_id, self.agents["finance"])
        self.assertEqual([a["department_code"] for a in plan.alternatives], ["hr"])
        self.assertEqual(plan.alternatives[0]["agent_id"], self.agents["hr"])
        self.assertEqual(plan.public()["matched_keywords"], ["预算", "报销"])

    def test_unusable_department_is_reported_not_silently_dropped(self):
        plan = self.plan("请假 库存")
        self.assertEqual((plan.reason, plan.department_code), ("routed", "hr"))
        self.assertEqual([u["department_code"] for u in plan.unavailable], ["procurement"])

    def test_no_usable_agent_reason(self):
        plan = self.plan("这个供应商的库存够吗")
        self.assertEqual(plan.reason, "no_usable_agent")
        self.assertEqual((plan.target_agent_id, plan.department_code), (self.agents["central"], "procurement"))

    def test_no_match_and_non_central(self):
        self.assertEqual(self.plan("今天天气怎么样").reason, "no_match")
        personal = self.plan("我想请假", agent="personal")
        self.assertEqual((personal.reason, personal.target_agent_id, personal.public()), ("not_central", self.agents["personal"], None))

    def test_async_resolve_wrapper_matches_plan(self):
        target = run_db(lambda db: central_router.resolve_target_agent_async(
            db, self.owner["id"], self.agents["central"], "帮我查报销预算", None))
        self.assertEqual(target, self.agents["finance"])

    def test_handoff_recorded_for_central_decisions_only(self):
        self.db.execute(text("DELETE FROM agent_handoff WHERE user_id=:u"), {"u": self.owner["id"]})
        self.db.commit()
        for message, agent in (("我想请假，顺便查下这笔报销预算", "central"), ("今天天气怎么样", "central"),
                               ("我想请假", "personal")):
            plan = self.plan(message, agent)
            run_db(lambda db: central_router.record_handoff_async(db, self.owner["id"], plan, message))
        self.db.commit()
        rows = self.db.query(AgentHandoff).filter(AgentHandoff.user_id == self.owner["id"]).order_by(AgentHandoff.id).all()
        self.assertEqual([r.reason for r in rows], ["routed", "no_match"])
        self.assertEqual(rows[0].target_agent_id, self.agents["finance"])
        self.assertIn("顺便查下这笔报销预算", rows[0].message_excerpt)

    def test_stream_emits_route_event_first_and_records_handoff(self):
        import json
        client = rc.make_client()
        self.db.execute(text("DELETE FROM agent_handoff WHERE user_id=:u"), {"u": self.owner["id"]})
        self.db.commit()
        with client.stream("POST", f"/chat/{self.agents['central']}/stream", json={"message": "我想请假，顺便查下这笔报销预算"},
                           headers=self.owner["headers"]) as response:
            body = "".join(response.iter_text())
        self.assertTrue(body.startswith("event: route\n"), body[:80])
        payload = json.loads(body.split("\n", 2)[1][len("data: "):])
        self.assertEqual((payload["reason"], payload["department_code"]), ("routed", "finance"))
        self.assertEqual(payload["target_agent_id"], self.agents["finance"])
        self.assertEqual([a["department_code"] for a in payload["alternatives"]], ["hr"])
        self.db.commit()
        self.assertEqual(self.db.query(AgentHandoff).filter(AgentHandoff.user_id == self.owner["id"]).count(), 1)

    def test_personal_agent_stream_has_no_route_event(self):
        client = rc.make_client()
        with client.stream("POST", f"/chat/{self.agents['personal']}/stream", json={"message": "我想请假"},
                           headers=self.owner["headers"]) as response:
            body = "".join(response.iter_text())
        self.assertNotIn("event: route", body)

    def test_admin_handoff_list_and_permissions(self):
        from service import handoff_service
        plan = self.plan("我想请假，顺便查下这笔报销预算")
        run_db(lambda db: central_router.record_handoff_async(db, self.owner["id"], plan, "我想请假，顺便查下这笔报销预算"))
        data = run_db(lambda db: handoff_service.list_handoffs(db, 50, reason="routed"))
        mine = next(i for i in data["items"] if i["user_id"] == self.owner["id"])
        self.assertEqual((mine["target_agent"], mine["alternatives"]), ("plan-finance", ["plan-hr"]))
        self.assertEqual(mine["central_agent"], "plan-central")
        client = rc.make_client()
        self.assertEqual(client.get("/admin/org/handoffs", headers=self.other["headers"]).status_code, 403)
        with rc.admin_env(self.other["name"]):
            self.assertEqual(client.get("/admin/org/handoffs?limit=5", headers=self.other["headers"]).status_code, 200)


@unittest.skipUnless(_AVAILABLE, _WHY)
class OwnDepartmentFallbackTest(unittest.TestCase):
    """部门助手只对本部门成员开放。销售员工问报销 / 请假：财务、人事助手他用不了，但这是他自己的通用办公事务，
    交给他本部门的销售助手办（模板里都带通用办公工具）；问采购、库存这类部门专属业务，照旧说明没有可用的助手。"""

    @classmethod
    def setUpClass(cls):
        import uuid
        from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team
        cls.db = SessionLocal()
        cls.admin = rc.create_user("own-admin")
        cls.seller = rc.create_user("own-seller")
        cls.accountant = rc.create_user("own-acct")
        cls.org = _create_org(cls.db, "own-org-" + uuid.uuid4().hex[:6], cls.admin["id"])
        cls.sales = _create_team(cls.db, cls.org, "own-sales", cls.admin["id"])
        cls.fin = _create_team(cls.db, cls.org, "own-fin", cls.admin["id"])
        for user in (cls.seller, cls.accountant):
            _add_org_member(cls.db, cls.org, user["id"], "member")
        _add_team_member(cls.db, cls.sales, cls.seller["id"], "member")
        _add_team_member(cls.db, cls.fin, cls.accountant["id"], "member")
        cls.agents = {}

        def make(key, **kw):
            agent = Agent(user_id=cls.admin["id"], name=f"own-{key}", lifecycle_status="published", organization_id=cls.org, **kw)
            cls.db.add(agent)
            cls.db.commit()
            cls.agents[key] = agent.id
        make("central", agent_type="central", scope_type="organization")
        make("sales", agent_type="department", department_code="sales", team_id=cls.sales, scope_type="department")
        make("finance", agent_type="department", department_code="finance", team_id=cls.fin, scope_type="department")

    @classmethod
    def tearDownClass(cls):
        ids = {"a": cls.seller["id"], "b": cls.accountant["id"]}
        cls.db.execute(text("DELETE FROM agent_handoff WHERE user_id IN (:a, :b)"), ids)
        cls.db.commit()
        rc.cleanup()
        cls.db.execute(text("DELETE FROM agent WHERE id IN :ids").bindparams(
            __import__("sqlalchemy").bindparam("ids", expanding=True)), {"ids": list(cls.agents.values())})
        cls.db.execute(text("DELETE FROM team_members WHERE team_id IN (:a, :b)"), {"a": cls.sales, "b": cls.fin})
        cls.db.execute(text("DELETE FROM teams WHERE id IN (:a, :b)"), {"a": cls.sales, "b": cls.fin})
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()

    def plan(self, user, message):
        return run_db(lambda db: central_router.plan_route_async(db, user["id"], self.agents["central"], message, None))

    def test_sales_staff_expense_goes_to_own_department_agent(self):
        plan = self.plan(self.seller, "帮我报销 300 元打车费")
        self.assertEqual((plan.reason, plan.department_code), ("own_department", "finance"))
        self.assertEqual((plan.target_agent_id, plan.target_name), (self.agents["sales"], "own-sales"))
        self.assertEqual(plan.public()["reason"], "own_department")

    def test_sales_staff_leave_goes_to_own_department_agent(self):
        plan = self.plan(self.seller, "我还有几天年假")
        self.assertEqual((plan.reason, plan.department_code, plan.target_agent_id), ("own_department", "hr", self.agents["sales"]))

    def test_department_specific_business_is_not_redirected(self):
        plan = self.plan(self.seller, "这个供应商的库存够吗")
        self.assertEqual((plan.reason, plan.target_agent_id), ("no_usable_agent", self.agents["central"]))

    def test_finance_staff_still_go_to_finance_agent(self):
        plan = self.plan(self.accountant, "帮我报销 300 元打车费")
        self.assertEqual((plan.reason, plan.target_agent_id), ("routed", self.agents["finance"]))

    def test_own_customer_questions_route_normally(self):
        plan = self.plan(self.seller, "帮我跟进一下这个客户")
        self.assertEqual((plan.reason, plan.target_agent_id), ("routed", self.agents["sales"]))


class TemplateGeneralToolsTest(unittest.TestCase):
    def test_every_department_template_can_do_own_office_affairs(self):
        from service.enterprise_agent_templates import TEMPLATES
        for template in TEMPLATES.values():
            if template["agent_type"] != "department":
                continue
            for tool in ("create_leave_draft", "submit_expense_claim", "create_it_ticket", "read_feishu_group_chat"):
                self.assertIn(tool, template["tools"], f"{template['id']} 缺 {tool}")
            self.assertEqual(len(template["tools"]), len(set(template["tools"])), template["id"])


if __name__ == "__main__":
    unittest.main()
