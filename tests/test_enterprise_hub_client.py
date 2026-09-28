"""service/enterprise_hub_client.py::resolve_caller_context 的单测（真实 DB）。

核心场景：一个人同时属于两个部门时，`team_id` 不能靠"在职的第一个部门"瞎猜——
应该优先用当前对话所在的部门 Agent 自己的 team_id（`agent.team_id`，仅当
`agent_type='department'`），没有部门 Agent 上下文时才退回"用户自己在职的
第一个部门"（旧行为，作为兜底保留）。修复记录见 docs/enterprise-rbac-plan.md。
"""
import unittest

from models.init_db import Agent, SessionLocal
from service import enterprise_hub_client as hub
from tests import _route_client as rc
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class ResolveCallerContextMultiTeamTest(unittest.TestCase):
    """user 同时是 team_a（先建，id 更小）和 team_b（后建，id 更大）的部门负责人——
    旧的 `ORDER BY id LIMIT 1` 一定会拿到 team_a，即使这次操作明明是在 team_b 的
    部门 Agent 下发起的。"""

    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.user = rc.create_user("ehc-multi-team-user")
        cls.org_id = _create_org(cls.db, "ehc-multi-team-org", cls.user["id"])
        _add_org_member(cls.db, cls.org_id, cls.user["id"], "member")

        cls.team_a = _create_team(cls.db, cls.org_id, "ehc-team-a", cls.user["id"])
        cls.team_b = _create_team(cls.db, cls.org_id, "ehc-team-b", cls.user["id"])
        # 两边都是负责人（admin），纯粹的"该用哪个部门"问题，不是"有没有权限"问题。
        _add_team_member(cls.db, cls.team_a, cls.user["id"], "admin")
        _add_team_member(cls.db, cls.team_b, cls.user["id"], "admin")

        dept_agent_b = Agent(user_id=cls.user["id"], name="ehc-dept-agent-b",
                              agent_type="department", department_code="procurement", team_id=cls.team_b)
        personal_agent = Agent(user_id=cls.user["id"], name="ehc-personal-agent")  # 默认 personal，无 team_id
        cls.db.add_all([dept_agent_b, personal_agent])
        cls.db.commit()
        cls.dept_agent_b_id = dept_agent_b.id
        cls.personal_agent_id = personal_agent.id

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.execute(__import__("sqlalchemy").text("DELETE FROM teams WHERE id IN (:a, :b)"),
                        {"a": cls.team_a, "b": cls.team_b})
        cls.db.execute(__import__("sqlalchemy").text("DELETE FROM organizations WHERE id=:i"), {"i": cls.org_id})
        cls.db.commit()
        cls.db.close()

    def test_department_agent_context_uses_agents_own_team(self):
        # 通过 team_b 的部门 Agent 操作——即使 team_a 的 id 更小，也必须拿到 team_b。
        auth = hub.resolve_caller_context(self.user["id"], self.dept_agent_b_id)
        self.assertEqual(auth["team_id"], self.team_b)
        self.assertTrue(auth["is_team_admin"])

    def test_no_agent_context_falls_back_to_first_team(self):
        # 没有部门 Agent 上下文（比如没传 agent_id）——退回旧的"第一个在职部门"兜底行为。
        auth = hub.resolve_caller_context(self.user["id"], None)
        self.assertEqual(auth["team_id"], self.team_a)

    def test_personal_agent_context_falls_back_to_first_team(self):
        # 部门 Agent 之外的其它 Agent（agent_type != 'department'）不提供 team_id，同样退回兜底。
        auth = hub.resolve_caller_context(self.user["id"], self.personal_agent_id)
        self.assertEqual(auth["team_id"], self.team_a)

    def test_nonexistent_agent_id_falls_back_to_first_team(self):
        auth = hub.resolve_caller_context(self.user["id"], 999_999_999)
        self.assertEqual(auth["team_id"], self.team_a)

    def test_disabled_department_agent_team_is_skipped(self):
        # team_b 被停用后，即使还是通过 team_b 的部门 Agent 操作，也不能再拿到
        # team_b 的 team_id（那是个已经不存在的部门了）——应该退回兜底（team_a）。
        from sqlalchemy import text as _sql
        self.db.execute(_sql("UPDATE teams SET status='disabled' WHERE id=:i"), {"i": self.team_b})
        self.db.commit()
        try:
            auth = hub.resolve_caller_context(self.user["id"], self.dept_agent_b_id)
            self.assertEqual(auth["team_id"], self.team_a)
        finally:
            self.db.execute(_sql("UPDATE teams SET status='active' WHERE id=:i"), {"i": self.team_b})
            self.db.commit()

    def test_disabled_first_team_is_skipped_by_fallback(self):
        # 没有部门 Agent 上下文时，兜底逻辑也不能选中已停用的 team_a——应该跳过它，
        # 落到仍然启用的 team_b。
        from sqlalchemy import text as _sql
        self.db.execute(_sql("UPDATE teams SET status='disabled' WHERE id=:i"), {"i": self.team_a})
        self.db.commit()
        try:
            auth = hub.resolve_caller_context(self.user["id"], None)
            self.assertEqual(auth["team_id"], self.team_b)
        finally:
            self.db.execute(_sql("UPDATE teams SET status='active' WHERE id=:i"), {"i": self.team_a})
            self.db.commit()


if __name__ == "__main__":
    unittest.main()
