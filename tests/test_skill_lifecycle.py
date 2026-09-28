"""Skill 发布生命周期（draft/reviewing/published/retired）+ row_version 乐观锁的测试。

补的是"字段只加了，没有任何强制逻辑"这个缺口（docs/enterprise-rbac-plan.md 相关
记录）：草稿 Skill 之前可以被任何人绑定和运行，并发编辑也不会冲突。分两块：
1. `LifecycleUnitTest`：纯逻辑，测 `service.access_control.can_bind_skill`。
2. 真实 DB 的集成测试：绑定时的发布状态门禁、运行时跳过已退役 Skill、
   `update_skill` 的乐观锁 UPDATE。
"""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from models.init_db import Agent, Skill, SessionLocal
from service import access_control
from service.exceptions import Conflict, InvalidInput
from service.skills import loader as skill_loader
from service.skills_core import binding, crud
from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()


class CanBindSkillUnitTest(unittest.TestCase):
    def test_owner_can_bind_own_draft(self):
        skill = SimpleNamespace(user_id=1, lifecycle_status="draft")
        self.assertTrue(access_control.can_bind_skill(skill, 1))

    def test_owner_can_bind_own_retired(self):
        # 作者自己也能把已退役的 Skill 绑到测试 Agent 上（比如打算重新发布前先验证）——
        # 这条只挡"别人"，运行时的 RETIRED_STATUS 检查才是真正拦所有人的那一道。
        skill = SimpleNamespace(user_id=1, lifecycle_status="retired")
        self.assertTrue(access_control.can_bind_skill(skill, 1))

    def test_non_owner_cannot_bind_draft(self):
        skill = SimpleNamespace(user_id=1, lifecycle_status="draft")
        self.assertFalse(access_control.can_bind_skill(skill, 2))

    def test_non_owner_cannot_bind_reviewing(self):
        skill = SimpleNamespace(user_id=1, lifecycle_status="reviewing")
        self.assertFalse(access_control.can_bind_skill(skill, 2))

    def test_non_owner_can_bind_published(self):
        skill = SimpleNamespace(user_id=1, lifecycle_status="published")
        self.assertTrue(access_control.can_bind_skill(skill, 2))

    def test_non_owner_cannot_bind_retired(self):
        skill = SimpleNamespace(user_id=1, lifecycle_status="retired")
        self.assertFalse(access_control.can_bind_skill(skill, 2))

    def test_none_skill_is_false(self):
        self.assertFalse(access_control.can_bind_skill(None, 1))


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class BindingLifecycleGateTest(unittest.TestCase):
    """真实 DB：非作者只能绑已发布的公开 Skill，作者自己不受限制。"""

    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.author = rc.create_user("skl-lifecycle-author")
        cls.other = rc.create_user("skl-lifecycle-other")

        cls.draft_skill = Skill(user_id=cls.author["id"], name="draft-skill", description="d",
                                 config_file="unused.yml", is_public=1, lifecycle_status="draft")
        cls.published_skill = Skill(user_id=cls.author["id"], name="published-skill", description="d",
                                    config_file="unused.yml", is_public=1, lifecycle_status="published")
        cls.db.add_all([cls.draft_skill, cls.published_skill])
        cls.db.commit()

        cls.author_agent = Agent(user_id=cls.author["id"], name="author-agent")
        cls.other_agent = Agent(user_id=cls.other["id"], name="other-agent")
        cls.db.add_all([cls.author_agent, cls.other_agent])
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.execute(__import__("sqlalchemy").text(
            "DELETE FROM agent_skill WHERE skill_id IN (:a, :b)"),
            {"a": cls.draft_skill.id, "b": cls.published_skill.id})
        cls.db.execute(__import__("sqlalchemy").text("DELETE FROM agent WHERE id IN (:a, :b)"),
                        {"a": cls.author_agent.id, "b": cls.other_agent.id})
        cls.db.execute(__import__("sqlalchemy").text("DELETE FROM skill WHERE id IN (:a, :b)"),
                        {"a": cls.draft_skill.id, "b": cls.published_skill.id})
        cls.db.commit()
        rc.cleanup()
        cls.db.close()

    def test_author_can_bind_own_draft_to_own_agent(self):
        ok = binding.bind_skill(self.db, self.author_agent.id, self.draft_skill.id, self.author["id"])
        self.assertTrue(ok)
        binding.unbind_skill(self.db, self.author_agent.id, self.draft_skill.id, self.author["id"])

    def test_other_user_cannot_bind_draft(self):
        ok = binding.bind_skill(self.db, self.other_agent.id, self.draft_skill.id, self.other["id"])
        self.assertFalse(ok)

    def test_other_user_can_bind_published(self):
        ok = binding.bind_skill(self.db, self.other_agent.id, self.published_skill.id, self.other["id"])
        self.assertTrue(ok)
        binding.unbind_skill(self.db, self.other_agent.id, self.published_skill.id, self.other["id"])

    def test_update_agent_skills_rejects_draft_for_non_owner(self):
        ok = binding.update_agent_skills(
            self.db, self.other_agent.id, [self.draft_skill.id], user_id=self.other["id"],
        )
        self.assertFalse(ok)

    def test_update_agent_skills_allows_published_for_non_owner(self):
        ok = binding.update_agent_skills(
            self.db, self.other_agent.id, [self.published_skill.id], user_id=self.other["id"],
        )
        self.assertTrue(ok)
        binding.update_agent_skills(self.db, self.other_agent.id, [], user_id=self.other["id"])


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class RetiredSkillIsSkippedAtRuntimeTest(unittest.TestCase):
    """真实 DB + 临时 SKILLS_ROOT：已退役的 Skill 即便绑在 Agent 上，运行时也不加载
    （哪怕是作者自己的 Agent）——`get_agent_skills_merged_config` 是唯一的运行时入口。"""

    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.owner = rc.create_user("skl-lifecycle-runtime")
        cls.root = tempfile.mkdtemp(prefix="skl_lifecycle_")
        cls._patches = [
            patch.object(skill_loader, "SKILLS_ROOT", cls.root),
        ]
        for p in cls._patches:
            p.start()

        def _write(filename, name):
            cfg = {"name": name, "description": "d", "tools": [{"name": "word_count", "defaults": {}}],
                   "system_prompt": f"prompt for {name}"}
            with open(os.path.join(cls.root, filename), "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

        _write("active.yml", "active-skill")
        _write("retired.yml", "retired-skill")
        skill_loader.invalidate_skill_config()

        cls.active_skill = Skill(user_id=cls.owner["id"], name="active-skill", description="d",
                                  config_file="active.yml", lifecycle_status="published")
        cls.retired_skill = Skill(user_id=cls.owner["id"], name="retired-skill", description="d",
                                   config_file="retired.yml", lifecycle_status="retired")
        cls.db.add_all([cls.active_skill, cls.retired_skill])
        cls.db.commit()

        cls.agent = Agent(user_id=cls.owner["id"], name="runtime-test-agent")
        cls.db.add(cls.agent)
        cls.db.commit()
        binding.bind_skill(cls.db, cls.agent.id, cls.active_skill.id, cls.owner["id"])
        # 直接 DAO 绑定已退役的那条——can_bind_skill 会拦（这不是这个测试要测的），
        # 这里只关心"已经绑上了"这个状态下运行时会不会正确跳过。
        from models.skill_dao import bind_skill_to_agent
        bind_skill_to_agent(cls.db, cls.agent.id, cls.retired_skill.id)

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()
        shutil.rmtree(cls.root, ignore_errors=True)
        skill_loader.invalidate_skill_config()
        cls.db.execute(__import__("sqlalchemy").text(
            "DELETE FROM agent_skill WHERE agent_id=:a"), {"a": cls.agent.id})
        cls.db.execute(__import__("sqlalchemy").text("DELETE FROM agent WHERE id=:a"), {"a": cls.agent.id})
        cls.db.execute(__import__("sqlalchemy").text(
            "DELETE FROM skill WHERE id IN (:a, :b)"),
            {"a": cls.active_skill.id, "b": cls.retired_skill.id})
        cls.db.commit()
        rc.cleanup()
        cls.db.close()

    def test_retired_skill_is_excluded_from_merged_config(self):
        merged = binding.get_agent_skills_merged_config(self.db, self.agent.id)
        self.assertIn("active-skill", merged["skill_names"])
        self.assertNotIn("retired-skill", merged["skill_names"])


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class OptimisticLockingTest(unittest.TestCase):
    """每个测试自己建一条 Skill（不共用一条跨测试改），避免 unittest 默认按字母序
    跑测试时，后面的测试意外依赖前一个测试已经把 row_version 推到了哪个值。"""

    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.owner = rc.create_user("skl-lifecycle-lock")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.close()

    def setUp(self):
        self.skill = Skill(user_id=self.owner["id"], name="lock-skill", description="d",
                            config_file="unused.yml")
        self.db.add(self.skill)
        self.db.commit()

    def tearDown(self):
        self.db.execute(__import__("sqlalchemy").text("DELETE FROM skill WHERE id=:i"), {"i": self.skill.id})
        self.db.commit()

    def test_update_with_matching_version_succeeds_and_bumps_version(self):
        result = crud.update_skill(
            self.db, self.skill.id, self.owner["id"],
            expected_row_version=0, description="改过一次",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["row_version"], 1)
        self.assertEqual(result["description"], "改过一次")

    def test_update_with_stale_version_raises_conflict(self):
        # 先真的改一次（row_version 0->1），再拿旧的 0 去更新——模拟"没刷新页面
        # 就提交"的并发场景。
        crud.update_skill(self.db, self.skill.id, self.owner["id"], expected_row_version=0, description="第一次修改")
        with self.assertRaises(Conflict):
            crud.update_skill(
                self.db, self.skill.id, self.owner["id"],
                expected_row_version=0, description="过期的修改",
            )

    def test_update_without_expected_version_skips_check(self):
        # 不传 expected_row_version 就是旧行为：不比对，直接改——兼容还不知道这个
        # 概念的既有调用方（import/translate/reanalyze）。
        result = crud.update_skill(self.db, self.skill.id, self.owner["id"], description="不比对版本")
        self.assertIsNotNone(result)

    def test_invalid_lifecycle_status_raises_invalid_input(self):
        with self.assertRaises(InvalidInput):
            crud.update_skill(self.db, self.skill.id, self.owner["id"], lifecycle_status="not_a_real_status")

    def test_valid_lifecycle_status_transition_succeeds(self):
        result = crud.update_skill(self.db, self.skill.id, self.owner["id"], lifecycle_status="published")
        self.assertEqual(result["lifecycle_status"], "published")


if __name__ == "__main__":
    unittest.main()
