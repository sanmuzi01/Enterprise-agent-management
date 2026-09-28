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
class NonOwnerSharedSkillDemotedAtRuntimeTest(unittest.TestCase):
    """P0：别人绑定过的已发布 Skill，作者事后改回 draft/reviewing 后，运行时要
    立刻停止把它喂给那个绑定它的 Agent——不能只信"绑定那一刻是 published"这个
    一次性检查（docs/enterprise-rbac-plan.md 相关记录）。跟上面
    `RetiredSkillIsSkippedAtRuntimeTest` 的区别：那个测的是 retired（作者自己的
    Agent 也会被挡），这个测的是"作者不是 Agent owner 时，非 published 也会被挡"。
    """

    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.author = rc.create_user("skl-dm-a")
        cls.agent_owner = rc.create_user("skl-dm-o")
        cls.root = tempfile.mkdtemp(prefix="skl_lifecycle_demote_")
        cls._patches = [patch.object(skill_loader, "SKILLS_ROOT", cls.root)]
        for p in cls._patches:
            p.start()

        cfg = {"name": "shared-skill", "description": "d",
               "tools": [{"name": "word_count", "defaults": {}}],
               "system_prompt": "prompt for shared-skill"}
        with open(os.path.join(cls.root, "shared.yml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        skill_loader.invalidate_skill_config()

        # 作者自己的 own-skill：author 绑到 author 自己的 own-agent 上，用来确认
        # "作者绑自己的 Skill 不受这条限制"（哪怕后面改回草稿也照常加载，只有
        # retired 才会被挡——跟上面已有的 RetiredSkillIsSkippedAtRuntimeTest 是
        # 同一条豁免规则，这里用它当对照组）。
        cls.own_skill = Skill(user_id=cls.author["id"], name="shared-skill", description="d",
                               config_file="shared.yml", is_public=1, lifecycle_status="published")
        cls.db.add(cls.own_skill)
        cls.db.commit()

        cls.own_agent = Agent(user_id=cls.author["id"], name="demote-own-agent")
        cls.shared_agent = Agent(user_id=cls.agent_owner["id"], name="demote-shared-agent")
        cls.db.add_all([cls.own_agent, cls.shared_agent])
        cls.db.commit()

        binding.bind_skill(cls.db, cls.own_agent.id, cls.own_skill.id, cls.author["id"])
        # 绑定那一刻 own_skill 是 published，agent_owner（非作者）能绑成功。
        ok = binding.bind_skill(cls.db, cls.shared_agent.id, cls.own_skill.id, cls.agent_owner["id"])
        assert ok, "绑定应该成功：绑定那一刻 Skill 是 published"

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()
        shutil.rmtree(cls.root, ignore_errors=True)
        skill_loader.invalidate_skill_config()
        from sqlalchemy import text
        cls.db.execute(text("DELETE FROM agent_skill WHERE agent_id IN (:a, :b)"),
                        {"a": cls.own_agent.id, "b": cls.shared_agent.id})
        cls.db.execute(text("DELETE FROM agent WHERE id IN (:a, :b)"),
                        {"a": cls.own_agent.id, "b": cls.shared_agent.id})
        cls.db.execute(text("DELETE FROM skill WHERE id=:i"), {"i": cls.own_skill.id})
        cls.db.commit()
        rc.cleanup()
        cls.db.close()

    def test_demoted_shared_skill_dropped_for_non_owner_agent_but_kept_for_own_agent(self):
        from sqlalchemy import text

        for status in ("draft", "reviewing"):
            self.db.execute(text("UPDATE skill SET lifecycle_status=:s WHERE id=:i"),
                             {"s": status, "i": self.own_skill.id})
            self.db.commit()

            shared_merged = binding.get_agent_skills_merged_config(self.db, self.shared_agent.id)
            self.assertNotIn(
                "shared-skill", shared_merged["skill_names"],
                f"非作者绑定的 Agent 不应该继续加载 lifecycle_status={status} 的共享 Skill",
            )

            own_merged = binding.get_agent_skills_merged_config(self.db, self.own_agent.id)
            self.assertIn(
                "shared-skill", own_merged["skill_names"],
                f"作者自己的 Agent 应该照常加载自己 lifecycle_status={status} 的 Skill",
            )

        self.db.execute(text("UPDATE skill SET lifecycle_status='published' WHERE id=:i"),
                         {"i": self.own_skill.id})
        self.db.commit()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class PublishedSkillAutoDemotesOnConfigEditTest(unittest.TestCase):
    """P0：改已发布 Skill 的运行配置（system_prompt/tool_names/permissions）必须
    自动把它打回 draft，不能让内容改动悄悄对所有绑定它的人原地生效。"""

    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.owner = rc.create_user("skl-adm")
        cls.root = tempfile.mkdtemp(prefix="skl_lifecycle_autodemote_")
        cls._patches = [patch.object(skill_loader, "SKILLS_ROOT", cls.root)]
        for p in cls._patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()
        shutil.rmtree(cls.root, ignore_errors=True)
        skill_loader.invalidate_skill_config()
        rc.cleanup()
        cls.db.close()

    def _make_published_skill(self, name: str) -> Skill:
        # 至少要有一个工具，不然 _validate_tool_names 会拒（"工具不存在或未选择工具"），
        # 保存不成功就测不了后面的自动降级逻辑。
        cfg = {"name": name, "description": "d", "tools": [{"name": "word_count", "defaults": {}}],
               "tool_names": ["word_count"], "system_prompt": "old prompt"}
        skill = Skill(user_id=self.owner["id"], name=name, description="d",
                       config_file=f"user_created/{name}.yml", is_public=1, lifecycle_status="published")
        self.db.add(skill)
        self.db.commit()
        # 真正的配置文件路径要跟 config_file 一致，update_skill_config 只允许改
        # user_created/ 或 imported/ 开头的文件（见 service/skills_core/crud.py）。
        real_path = os.path.join(self.root, "user_created")
        os.makedirs(real_path, exist_ok=True)
        with open(os.path.join(real_path, f"{name}.yml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        skill_loader.invalidate_skill_config()
        return skill

    def tearDown(self):
        from sqlalchemy import text
        self.db.execute(text("DELETE FROM skill_version WHERE skill_id IN "
                              "(SELECT id FROM skill WHERE user_id=:u)"), {"u": self.owner["id"]})
        self.db.execute(text("DELETE FROM skill WHERE user_id=:u"), {"u": self.owner["id"]})
        self.db.commit()

    def test_editing_config_of_published_skill_demotes_to_draft(self):
        skill = self._make_published_skill("autodemote-a")
        result = crud.update_skill_with_config(
            self.db, skill.id, self.owner["id"],
            fields={}, config_fields={"system_prompt": "new prompt"}, allow_admin=True,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["lifecycle_status"], "draft")

    def test_editing_config_while_explicitly_retiring_keeps_explicit_choice(self):
        # 同一次请求里管理员自己主动把状态改成了别的值（不是"不小心带了当前值"）——
        # 这种情况尊重管理员的选择，不强制打回 draft。
        skill = self._make_published_skill("autodemote-b")
        result = crud.update_skill_with_config(
            self.db, skill.id, self.owner["id"],
            fields={"lifecycle_status": "retired"},
            config_fields={"system_prompt": "new prompt"}, allow_admin=True,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["lifecycle_status"], "retired")

    def test_editing_config_while_resending_same_published_value_still_demotes(self):
        # 前端编辑弹窗每次保存都会带上当前选中的发布状态（哪怕没碰过那个下拉框）
        # ——这里模拟这个真实场景：请求里的 lifecycle_status 跟数据库现有值一样，
        # 必须仍然按"没有主动做状态决定"处理，照样打回 draft。
        skill = self._make_published_skill("autodemote-c")
        result = crud.update_skill_with_config(
            self.db, skill.id, self.owner["id"],
            fields={"lifecycle_status": "published"},
            config_fields={"system_prompt": "new prompt"}, allow_admin=True,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["lifecycle_status"], "draft")

    def test_editing_only_name_does_not_touch_lifecycle_status(self):
        # 没碰运行配置（config_fields 空），只改名字这种基础字段——不触发自动降级。
        skill = self._make_published_skill("autodemote-d")
        result = crud.update_skill_with_config(
            self.db, skill.id, self.owner["id"],
            fields={"name": "renamed"}, config_fields={}, allow_admin=True,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["lifecycle_status"], "published")


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class ConfigOnlyEditBumpsRowVersionTest(unittest.TestCase):
    """P1：只改运行配置（system_prompt/tool_names/permissions，不改 name/is_public
    等 DB 字段）之前完全不会碰 row_version，乐观锁形同虚设——两次并发的纯配置
    编辑会互相覆盖都不知道。这里用 draft 状态的 Skill（不触发上面的自动降级
    逻辑），单独测"config_fields 单独出现时 row_version 照样递增+比对"这件事。
    """

    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.owner = rc.create_user("skl-cfgver")
        cls.root = tempfile.mkdtemp(prefix="skl_cfgver_")
        cls._patches = [patch.object(skill_loader, "SKILLS_ROOT", cls.root)]
        for p in cls._patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()
        shutil.rmtree(cls.root, ignore_errors=True)
        skill_loader.invalidate_skill_config()
        rc.cleanup()
        cls.db.close()

    def setUp(self):
        cfg = {"name": "cfgver", "description": "d", "tools": [{"name": "word_count", "defaults": {}}],
               "tool_names": ["word_count"], "system_prompt": "old prompt"}
        real_path = os.path.join(self.root, "user_created")
        os.makedirs(real_path, exist_ok=True)
        with open(os.path.join(real_path, "cfgver.yml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        skill_loader.invalidate_skill_config()
        self.skill = Skill(user_id=self.owner["id"], name="cfgver", description="d",
                            config_file="user_created/cfgver.yml")
        self.db.add(self.skill)
        self.db.commit()

    def tearDown(self):
        from sqlalchemy import text
        self.db.execute(text("DELETE FROM skill_version WHERE skill_id=:i"), {"i": self.skill.id})
        self.db.execute(text("DELETE FROM skill WHERE id=:i"), {"i": self.skill.id})
        self.db.commit()

    def test_config_only_edit_bumps_row_version(self):
        result = crud.update_skill_with_config(
            self.db, self.skill.id, self.owner["id"],
            fields={}, config_fields={"system_prompt": "new prompt"}, allow_admin=True,
            expected_row_version=0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["row_version"], 1)

    def test_config_only_edit_with_stale_version_raises_conflict(self):
        crud.update_skill_with_config(
            self.db, self.skill.id, self.owner["id"],
            fields={}, config_fields={"system_prompt": "first edit"}, allow_admin=True,
            expected_row_version=0,
        )
        with self.assertRaises(Conflict):
            crud.update_skill_with_config(
                self.db, self.skill.id, self.owner["id"],
                fields={}, config_fields={"system_prompt": "second edit, stale"}, allow_admin=True,
                expected_row_version=0,
            )


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
