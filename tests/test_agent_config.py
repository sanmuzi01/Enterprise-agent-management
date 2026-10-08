"""管理端智能体的完整配置：运行参数、知识库、技能、发布前检查、可选项。

创建 / 编辑不再只有名称和提示词：模型与温度、长期记忆、知识库检索行为、绑定的知识库和技能都在这里一并配置，
并且有发布前检查告诉管理员还缺什么。"""
import unittest
from unittest import mock

from sqlalchemy import text

from models.init_db import (KnowledgeSpace, KnowledgeSpaceDepartment, Organization, SessionLocal, Skill, Team)
from service.exceptions import Conflict, InvalidInput, NotFound
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
class AgentConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = rc.create_user("acf-admin")
        cls.other_admin = rc.create_user("acf-admin2")
        db = SessionLocal()
        cls.db = db
        cls.org = Organization(name="acf-org", owner_user_id=cls.admin["id"])
        cls.org2 = Organization(name="acf-org2", owner_user_id=cls.admin["id"])
        db.add_all([cls.org, cls.org2])
        db.commit()
        cls.org_id, cls.org2_id = cls.org.id, cls.org2.id
        cls.team_a = Team(name="acf-team-a", owner_user_id=cls.admin["id"], organization_id=cls.org_id)
        cls.team_x = Team(name="acf-team-x", owner_user_id=cls.admin["id"], organization_id=cls.org2_id)
        db.add_all([cls.team_a, cls.team_x])
        db.commit()
        cls.a, cls.x = cls.team_a.id, cls.team_x.id

        def skill(name, user_id, status, config_file):
            s = Skill(user_id=user_id, name=name, config_file=config_file, lifecycle_status=status)
            db.add(s)
            db.commit()
            return s.id

        cls.skill_published_other = skill("acf-skill-published", cls.other_admin["id"], "published", "acf/published.yml")
        cls.skill_draft_other = skill("acf-skill-draft", cls.other_admin["id"], "draft", "acf/draft.yml")
        cls.skill_own_draft = skill("acf-skill-own-draft", cls.admin["id"], "draft", "acf/own.yml")
        cls.skill_other_agent_template = skill("acf-skill-template-of-someone", cls.other_admin["id"], "published", "enterprise/agent_999999.yml")

        def space(name, scope_type="personal", team_ids=(), owner=None, sensitivity="internal"):
            s = KnowledgeSpace(user_id=(owner or cls.other_admin)["id"], name=name, scope_type=scope_type, sensitivity=sensitivity,
                               organization_id=cls.org_id if scope_type != "personal" else None)
            db.add(s)
            db.commit()
            for team_id in team_ids:
                db.add(KnowledgeSpaceDepartment(space_id=s.id, team_id=team_id))
            db.commit()
            return s.id

        cls.space_a = space("acf-space-a", "department", [cls.a])
        cls.space_loose = space("acf-space-loose")             # 别的管理员建的、还没划分的
        cls.space_company = space("acf-space-company", "enterprise")

    @classmethod
    def tearDownClass(cls):
        db = cls.db
        db.execute(text("DELETE FROM agent_skill WHERE agent_id IN (SELECT id FROM agent WHERE name LIKE 'acf-%')"))
        db.execute(text("DELETE FROM agent_knowledge_space WHERE agent_id IN (SELECT id FROM agent WHERE name LIKE 'acf-%')"))
        db.execute(text("DELETE FROM agent_external_endpoint WHERE agent_id IN (SELECT id FROM agent WHERE name LIKE 'acf-%')"))
        db.execute(text("DELETE FROM agent WHERE name LIKE 'acf-%'"))
        db.execute(text("DELETE FROM skill WHERE name LIKE 'acf-%'"))
        db.execute(text("DELETE FROM knowledge_spaces WHERE name LIKE 'acf-%'"))
        for team_id in (cls.a, cls.x):
            db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": team_id})
            db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": team_id})
        db.execute(text("DELETE FROM organizations WHERE id IN (:a,:b)"), {"a": cls.org_id, "b": cls.org2_id})
        db.commit()
        db.close()
        rc.cleanup()

    # ---------- 辅助 ----------
    @staticmethod
    def svc():
        import service.agent_admin_service as svc
        return svc

    def _org(self):
        return mock.patch("service.organization_admin_service._get_default_organization", mock.AsyncMock(return_value=self.org))

    def create(self, name, agent_type="department", **kw):
        with self._org():
            return _run_db(lambda db: self.svc().create_managed_agent(db, self.admin["id"], name, agent_type, **kw))

    def update(self, agent_id, **kw):
        with self._org():
            return _run_db(lambda db: self.svc().update_managed_agent(db, agent_id, self.admin["id"], **kw))

    def detail(self, agent_id):
        return _run_db(lambda db: self.svc().get_managed_agent_detail(db, agent_id))

    def assign(self, agent_id, target, **kw):
        with self._org():
            return _run_db(lambda db: self.svc().assign_managed_agent(db, agent_id, self.admin["id"], target, **kw))

    # ---------- 完整配置 ----------
    def test_create_with_the_full_configuration(self):
        agent = self.create(
            "acf-full", role="你是人事助手", task="回答制度问题", constraints="不编造", output="先结论后依据",
            model_name="glm-4-plus",
            config={"temperature": 30, "memory_enabled": 0, "rag_enabled": 1, "kb_top_k": 8, "kb_rerank_enabled": 1,
                    "kb_force_citation": 0, "kb_refuse_when_empty": 0},
            space_ids=[self.space_a, self.space_loose], skill_ids=[self.skill_published_other, self.skill_own_draft],
        )
        detail = self.detail(agent["id"])
        self.assertEqual(detail["model_name"], "glm-4-plus")
        self.assertEqual(detail["config"], {"temperature": 30, "memory_enabled": 0, "rag_enabled": 1, "kb_top_k": 8,
                                            "kb_rerank_enabled": 1, "kb_force_citation": 0, "kb_refuse_when_empty": 0})
        self.assertEqual(detail["space_ids"], sorted([self.space_a, self.space_loose]))
        self.assertEqual(detail["skill_ids"], sorted([self.skill_published_other, self.skill_own_draft]))
        self.assertEqual(detail["prompt"]["constraints"], "不编造")
        self.assertEqual(detail["prompt"]["output"], "先结论后依据")

    def test_defaults_when_no_configuration_is_given(self):
        detail = self.detail(self.create("acf-defaults")["id"])
        self.assertEqual((detail["config"]["temperature"], detail["config"]["memory_enabled"], detail["config"]["rag_enabled"]), (70, 1, 0))
        self.assertEqual((detail["space_ids"], detail["skill_ids"]), ([], []))

    def test_update_changes_parameters_and_replaces_bindings(self):
        agent = self.create("acf-update", config={"rag_enabled": 1}, space_ids=[self.space_a], skill_ids=[self.skill_published_other])
        before = self.detail(agent["id"])["row_version"]
        updated = self.update(agent["id"], config={"temperature": 10, "kb_top_k": 3}, space_ids=[self.space_company], skill_ids=[])
        detail = self.detail(agent["id"])
        self.assertEqual((detail["config"]["temperature"], detail["config"]["kb_top_k"], detail["config"]["rag_enabled"]), (10, 3, 1))
        self.assertEqual((detail["space_ids"], detail["skill_ids"]), ([self.space_company], []))
        self.assertEqual(detail["row_version"], before + 1)
        self.assertEqual(updated["row_version"], before + 1)

    def test_not_passing_bindings_leaves_them_alone(self):
        agent = self.create("acf-keep", space_ids=[self.space_a], skill_ids=[self.skill_published_other])
        self.update(agent["id"], name="acf-keep-renamed")
        detail = self.detail(agent["id"])
        self.assertEqual((detail["space_ids"], detail["skill_ids"]), ([self.space_a], [self.skill_published_other]))

    def test_changing_only_the_bindings_still_counts_as_a_change(self):
        """只改绑定也要递增版本号，否则两个管理员同时改绑定互相覆盖都不知道。"""
        agent = self.create("acf-bind-only")
        self.update(agent["id"], space_ids=[self.space_a])
        with self.assertRaises(Conflict):
            self.update(agent["id"], space_ids=[], expected_row_version=agent["row_version"])

    def test_the_templates_own_skill_is_bound_listed_for_that_agent_only_and_survives_edits(self):
        agent = self.create("acf-templated", template_id="oa", department_code="hr")
        skill_ids = self.detail(agent["id"])["skill_ids"]
        self.assertEqual(len(skill_ids), 1, "模板自带一个专属的专业技能")
        own = _run_db(lambda db: self.svc().agent_options(db, self.admin["id"], agent["id"]))
        other = _run_db(lambda db: self.svc().agent_options(db, self.admin["id"]))
        self.assertIn(skill_ids[0], {s["id"] for s in own["skills"]}, "编辑这个智能体时能看到并保留它自己的专属技能")
        self.assertNotIn(skill_ids[0], {s["id"] for s in other["skills"]}, "其他智能体的可选项里不出现它")
        self.update(agent["id"], name="acf-templated-renamed")
        self.assertEqual(self.detail(agent["id"])["skill_ids"], skill_ids)
        self.update(agent["id"], skill_ids=skill_ids + [self.skill_published_other])
        self.assertEqual(self.detail(agent["id"])["skill_ids"], sorted(skill_ids + [self.skill_published_other]))

    # ---------- 校验 ----------
    def test_invalid_parameters_are_rejected(self):
        agent = self.create("acf-validate")
        for config in ({"temperature": 101}, {"temperature": -1}, {"kb_top_k": 0}, {"kb_top_k": 21}, {"rag_enabled": 2},
                       {"temperature": "hot"}, {"temperature": True}, {"unknown": 1}):
            with self.assertRaises(InvalidInput, msg=str(config)):
                self.update(agent["id"], config=config)
            with self.assertRaises(InvalidInput, msg=str(config)):
                self.create("acf-validate-create", config=config)

    def test_bad_models_are_rejected_on_create_and_update(self):
        agent = self.create("acf-model")
        for model in ("not-a-model", "BAAI/bge-small-zh-v1.5", ""):
            with self.assertRaises(InvalidInput, msg=model):
                self.update(agent["id"], model_name=model)
        with self.assertRaises(InvalidInput):
            self.create("acf-model-create", model_name="not-a-model")

    def test_unknown_or_unpublished_bindings_are_rejected_without_changing_anything(self):
        agent = self.create("acf-bad-binding", space_ids=[self.space_a], skill_ids=[self.skill_published_other])
        with self.assertRaises(InvalidInput):
            self.update(agent["id"], name="acf-should-not-apply", space_ids=[987654321])
        with self.assertRaises(InvalidInput):
            self.update(agent["id"], name="acf-should-not-apply", skill_ids=[987654321])
        with self.assertRaises(InvalidInput) as ctx:
            self.update(agent["id"], name="acf-should-not-apply", skill_ids=[self.skill_draft_other])
        self.assertIn("没有发布", str(ctx.exception))
        detail = self.detail(agent["id"])
        self.assertEqual(detail["name"], "acf-bad-binding", "绑定校验失败时，同一次请求里的其他修改也不生效")
        self.assertEqual((detail["space_ids"], detail["skill_ids"]), ([self.space_a], [self.skill_published_other]))

    def test_an_admin_can_bind_spaces_they_do_not_own(self):
        agent = self.create("acf-any-space", space_ids=[self.space_loose])
        self.assertEqual(self.detail(agent["id"])["space_ids"], [self.space_loose])

    def test_missing_agents_are_not_found(self):
        with self.assertRaises(NotFound):
            self.detail(987654321)

    # ---------- 发布前检查 ----------
    def _levels(self, agent_id):
        return {item["key"]: item["level"] for item in self.detail(agent_id)["readiness"]["items"]}

    def test_readiness_for_a_fresh_unassigned_agent(self):
        agent = self.create("acf-ready-fresh")
        readiness = self.detail(agent["id"])["readiness"]
        levels = {i["key"]: i["level"] for i in readiness["items"]}
        self.assertEqual((readiness["ready"], levels["assignment"], levels["prompt"], levels["model"]), (False, "error", "warn", "ok"))

    def test_readiness_turns_ready_after_assigning_and_writing_the_prompt(self):
        agent = self.create("acf-ready-ok", role="你是助手", task="回答问题")
        self.assign(agent["id"], "department", team_id=self.a, department_code="hr")
        readiness = self.detail(agent["id"])["readiness"]
        self.assertTrue(readiness["ready"])
        self.assertEqual({i["level"] for i in readiness["items"]}, {"ok"})

    def test_readiness_warns_when_knowledge_is_enabled_but_empty(self):
        agent = self.create("acf-ready-rag", role="r", task="t", config={"rag_enabled": 1})
        self.assertEqual(self._levels(agent["id"])["knowledge"], "warn")

    def test_readiness_warns_when_bound_knowledge_is_unreadable_to_the_audience(self):
        agent = self.create("acf-ready-gap", role="r", task="t", config={"rag_enabled": 1}, space_ids=[self.space_loose])
        self.assign(agent["id"], "department", team_id=self.a, department_code="hr")
        item = next(i for i in self.detail(agent["id"])["readiness"]["items"] if i["key"] == "knowledge")
        self.assertEqual(item["level"], "warn")
        self.assertIn("acf-space-loose", item["message"])
        self.update(agent["id"], space_ids=[self.space_a])
        self.assertEqual(self._levels(agent["id"])["knowledge"], "ok")

    def test_readiness_for_external_agents(self):
        agent = self.create("acf-ready-external")
        self.assign(agent["id"], "enterprise")
        self.db.execute(text("UPDATE agent SET runtime_type='external' WHERE id=:i"), {"i": agent["id"]})
        self.db.commit()
        levels = self._levels(agent["id"])
        self.assertEqual(levels["runtime"], "error")
        self.assertNotIn("prompt", levels, "外部服务自己决定怎么回答，不检查平台里的设定")

    # ---------- 可选项 ----------
    def test_options_cover_models_skills_spaces_and_this_enterprises_teams(self):
        with self._org():
            options = _run_db(lambda db: self.svc().agent_options(db, self.admin["id"]))
        self.assertIn("glm-4", options["models"])
        self.assertNotIn("BAAI/bge-small-zh-v1.5", options["models"], "向量模型不是聊天模型")
        skill_names = {s["name"] for s in options["skills"]}
        self.assertIn("acf-skill-published", skill_names)
        self.assertIn("acf-skill-own-draft", skill_names, "自己的草稿技能可以绑")
        self.assertNotIn("acf-skill-draft", skill_names, "别人的草稿技能不能绑，所以不给选")
        self.assertNotIn("acf-skill-template-of-someone", skill_names, "别的智能体专属的模板技能不出现在可选项里")
        spaces = {s["name"]: s for s in options["spaces"]}
        self.assertEqual([d["name"] for d in spaces["acf-space-a"]["departments"]], ["acf-team-a"])
        self.assertEqual(spaces["acf-space-company"]["scope_type"], "enterprise")
        self.assertEqual(spaces["acf-space-loose"]["scope_type"], "personal")
        teams = {t["name"]: t for t in options["teams"]}
        self.assertIn("acf-team-a", teams)
        self.assertNotIn("acf-team-x", teams, "平台只服务一个企业：别的企业的部门不给选")
        self.assertEqual(set(teams["acf-team-a"]), {"id", "name", "department_code"})

    def test_listing_only_shows_this_enterprises_agents(self):
        mine = self.create("acf-listed")
        with self._org():
            foreign = _run_db(lambda db: self.svc().create_managed_agent(
                db, self.admin["id"], "acf-foreign", "central", organization_id=self.org2_id))
            listed = {a["id"]: a for a in _run_db(lambda db: self.svc().list_managed_agents(db))}
        self.assertIn(mine["id"], listed)
        self.assertNotIn(foreign["id"], listed, "别的企业的智能体不出现在列表里")
        self.assertNotIn("organization_name", listed[mine["id"]], "列表里不带企业信息")


if __name__ == "__main__":
    unittest.main()
