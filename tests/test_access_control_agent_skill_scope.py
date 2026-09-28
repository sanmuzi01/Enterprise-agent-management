"""Phase 3D 阶段2：`get_usable_agent`/`can_read_skill` 的部门/企业共享范围
（docs/enterprise-rbac-plan.md 9.5）。

只测"能不能用"这一件事，不碰"能不能改"——`get_owned_agent`/`can_write_skill` 之类
管理类判断本次没有改动，不在这个文件里重复测。

矩阵：
  - Agent/Skill A：scope_type=personal（默认值）—— 只有 owner 能用，回归旧行为不变。
  - Agent/Skill B：scope_type=department + team_id —— owner + 该部门在职成员能用；
    只是企业成员但不在这个部门的人不能用。
  - Agent/Skill C：scope_type=enterprise + organization_id —— owner + 任意在职企业成员
    能用；只在部门里但不在企业成员表里的人不能用（故意不让 department_member 也加入
    organization_members，用来确认企业范围判断真的只看 organization_members，不是从
    team_members 推出来的）。
  - outsider：什么都不是，什么都不能用。
"""
import unittest

from models.init_db import (
    Agent,
    EnterpriseRole,
    Organization,
    OrganizationMember,
    SessionLocal,
    Skill,
    Team,
    TeamMember,
)
from service import access_control
from tests import _route_client as rc
from tests._async_helpers import run_async as _run

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class AgentSkillScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = rc.create_user("scope-owner")
        cls.dept_member = rc.create_user("scope-dept-member")
        cls.org_member = rc.create_user("scope-org-member")
        cls.outsider = rc.create_user("scope-outsider")

        db = SessionLocal()
        try:
            org = Organization(name="scope-test-org", owner_user_id=cls.owner["id"])
            db.add(org)
            db.commit()
            cls.org_id = org.id

            team = Team(name="scope-test-team", owner_user_id=cls.owner["id"], organization_id=cls.org_id)
            db.add(team)
            db.commit()
            cls.team_id = team.id

            team_member_role_id = db.query(EnterpriseRole.id).filter_by(scope="team", code="member").scalar()
            org_member_role_id = db.query(EnterpriseRole.id).filter_by(scope="organization", code="member").scalar()

            db.add(TeamMember(team_id=cls.team_id, user_id=cls.dept_member["id"],
                               role_id=team_member_role_id, status="active"))
            db.add(OrganizationMember(organization_id=cls.org_id, user_id=cls.org_member["id"],
                                       role_id=org_member_role_id, status="active"))
            db.commit()

            # 部门/企业共享的 Agent 必须是 published 才能被非作者使用（见
            # service/access_control.py::get_usable_agent 的生命周期检查），
            # 这里的 dept_member/org_member 用例都是测"非作者能不能用共享 Agent"，
            # 所以要显式发布，不能用默认的 draft。
            agent_personal = Agent(user_id=cls.owner["id"], name="scope-agent-personal")
            agent_dept = Agent(user_id=cls.owner["id"], name="scope-agent-dept",
                                scope_type="department", team_id=cls.team_id, lifecycle_status="published")
            agent_org = Agent(user_id=cls.owner["id"], name="scope-agent-org",
                               scope_type="enterprise", organization_id=cls.org_id, lifecycle_status="published")
            db.add_all([agent_personal, agent_dept, agent_org])
            db.commit()
            cls.agent_personal_id = agent_personal.id
            cls.agent_dept_id = agent_dept.id
            cls.agent_org_id = agent_org.id

            skill_personal = Skill(user_id=cls.owner["id"], name="scope-skill-personal", config_file="x.yml")
            skill_dept = Skill(user_id=cls.owner["id"], name="scope-skill-dept", config_file="x.yml",
                                scope_type="department", team_id=cls.team_id)
            skill_org = Skill(user_id=cls.owner["id"], name="scope-skill-org", config_file="x.yml",
                               scope_type="enterprise", organization_id=cls.org_id)
            db.add_all([skill_personal, skill_dept, skill_org])
            db.commit()
            cls.skill_personal_id = skill_personal.id
            cls.skill_dept_id = skill_dept.id
            cls.skill_org_id = skill_org.id
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        from sqlalchemy import text
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM skill WHERE id IN (:a,:b,:c)"),
                       {"a": cls.skill_personal_id, "b": cls.skill_dept_id, "c": cls.skill_org_id})
            db.execute(text("DELETE FROM agent WHERE id IN (:a,:b,:c)"),
                       {"a": cls.agent_personal_id, "b": cls.agent_dept_id, "c": cls.agent_org_id})
            db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": cls.team_id})
            db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org_id})
            db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team_id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org_id})
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        rc.cleanup()

    # ---------------- Agent：同步 ----------------

    def test_owner_can_use_all_three(self):
        db = SessionLocal()
        try:
            for agent_id in (self.agent_personal_id, self.agent_dept_id, self.agent_org_id):
                self.assertIsNotNone(access_control.get_usable_agent(db, self.owner["id"], agent_id))
        finally:
            db.close()

    def test_dept_member_only_usable_for_department_scoped(self):
        db = SessionLocal()
        try:
            self.assertIsNone(access_control.get_usable_agent(db, self.dept_member["id"], self.agent_personal_id))
            self.assertIsNotNone(access_control.get_usable_agent(db, self.dept_member["id"], self.agent_dept_id))
            # 部门成员没有被加进 organization_members，企业级不该放行
            self.assertIsNone(access_control.get_usable_agent(db, self.dept_member["id"], self.agent_org_id))
        finally:
            db.close()

    def test_org_member_only_usable_for_enterprise_scoped(self):
        db = SessionLocal()
        try:
            self.assertIsNone(access_control.get_usable_agent(db, self.org_member["id"], self.agent_personal_id))
            # 企业成员不是这个部门的成员，部门级不该放行
            self.assertIsNone(access_control.get_usable_agent(db, self.org_member["id"], self.agent_dept_id))
            self.assertIsNotNone(access_control.get_usable_agent(db, self.org_member["id"], self.agent_org_id))
        finally:
            db.close()

    def test_outsider_gets_nothing(self):
        db = SessionLocal()
        try:
            for agent_id in (self.agent_personal_id, self.agent_dept_id, self.agent_org_id):
                self.assertIsNone(access_control.get_usable_agent(db, self.outsider["id"], agent_id))
        finally:
            db.close()

    def _set_agent_lifecycle_status(self, db, agent_id: int, status: str) -> None:
        from sqlalchemy import text
        db.execute(text("UPDATE agent SET lifecycle_status=:s WHERE id=:id"), {"s": status, "id": agent_id})
        db.commit()

    def test_non_owner_blocked_when_shared_agent_not_published(self):
        # P0：部门共享的 Agent 改回 draft/reviewing/retired 后，非作者立刻不能用了
        # ——哪怕之前是 published 过的、哪怕已经在跟它聊天（继续对话每次都会重新查
        # get_usable_agent，不是只在创建会话那一刻查一次）。
        db = SessionLocal()
        try:
            for status in ("draft", "reviewing", "retired"):
                self._set_agent_lifecycle_status(db, self.agent_dept_id, status)
                self.assertIsNone(
                    access_control.get_usable_agent(db, self.dept_member["id"], self.agent_dept_id),
                    f"dept_member 不应该能用 lifecycle_status={status} 的部门共享 Agent",
                )
        finally:
            # 还原成 published，不影响其他测试方法（unittest 不保证方法执行顺序）
            self._set_agent_lifecycle_status(db, self.agent_dept_id, "published")
            db.close()

    def test_owner_can_use_own_agent_regardless_of_status_except_retired(self):
        # 作者自己任何状态都能用（测草稿版本天经地义），唯独 retired 例外
        # ——即便是作者自己，退役了也不能再用（跟 service/lifecycle.py 的文档一致）。
        db = SessionLocal()
        try:
            for status in ("draft", "reviewing", "published"):
                self._set_agent_lifecycle_status(db, self.agent_dept_id, status)
                self.assertIsNotNone(access_control.get_usable_agent(db, self.owner["id"], self.agent_dept_id))

            self._set_agent_lifecycle_status(db, self.agent_dept_id, "retired")
            self.assertIsNone(access_control.get_usable_agent(db, self.owner["id"], self.agent_dept_id))
        finally:
            self._set_agent_lifecycle_status(db, self.agent_dept_id, "published")
            db.close()

    def test_get_owned_agent_unaffected_by_scope_widening(self):
        # 管理类判断（get_owned_agent）没有改，department/enterprise 共享不应该让非 owner
        # 通过这个检查——回归旧行为，防止以后有人不小心把两个函数合并成一个。
        db = SessionLocal()
        try:
            self.assertIsNone(access_control.get_owned_agent(db, self.dept_member["id"], self.agent_dept_id))
            self.assertIsNone(access_control.get_owned_agent(db, self.org_member["id"], self.agent_org_id))
        finally:
            db.close()

    # ---------------- Agent：异步 ----------------

    def test_async_scope_matrix(self):
        async def _do():
            from models.async_db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                self.assertIsNotNone(
                    await access_control.get_usable_agent_async(db, self.owner["id"], self.agent_dept_id))
                self.assertIsNotNone(
                    await access_control.get_usable_agent_async(db, self.dept_member["id"], self.agent_dept_id))
                self.assertIsNone(
                    await access_control.get_usable_agent_async(db, self.org_member["id"], self.agent_dept_id))
                self.assertIsNotNone(
                    await access_control.get_usable_agent_async(db, self.org_member["id"], self.agent_org_id))
                self.assertIsNone(
                    await access_control.get_usable_agent_async(db, self.outsider["id"], self.agent_org_id))
        _run(_do())

    # ---------------- Skill ----------------

    def test_can_read_skill_without_db_keeps_old_behavior(self):
        # 不传 db：跟改动前完全一样，department/enterprise 共享不生效。
        db = SessionLocal()
        try:
            from models.skill_dao import get_skill_by_id
            skill_dept = get_skill_by_id(db, self.skill_dept_id)
            self.assertFalse(access_control.can_read_skill(skill_dept, self.dept_member["id"]))
        finally:
            db.close()

    def test_can_read_skill_with_db_honors_scope(self):
        db = SessionLocal()
        try:
            from models.skill_dao import get_skill_by_id
            skill_dept = get_skill_by_id(db, self.skill_dept_id)
            skill_org = get_skill_by_id(db, self.skill_org_id)
            skill_personal = get_skill_by_id(db, self.skill_personal_id)

            self.assertTrue(access_control.can_read_skill(skill_dept, self.dept_member["id"], db))
            self.assertFalse(access_control.can_read_skill(skill_dept, self.org_member["id"], db))
            self.assertTrue(access_control.can_read_skill(skill_org, self.org_member["id"], db))
            self.assertFalse(access_control.can_read_skill(skill_org, self.dept_member["id"], db))
            self.assertFalse(access_control.can_read_skill(skill_personal, self.dept_member["id"], db))
            self.assertFalse(access_control.can_read_skill(skill_personal, self.outsider["id"], db))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
