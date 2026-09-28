"""技能历史版本与回滚：编辑前自动快照、去重、只留最近 N 个、恢复（含恢复前保存、坏版本拒绝恢复）。

数据库读写用内存里的假 DAO，配置文件读写走临时目录里的真实 YML 和真实校验。
"""
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import yaml

import service.tools  # noqa: F401
from service.skills import loader as skill_loader
from service.skills_core import crud, versioning
from service.skills_core.versioning import VersionError


class FakeVersionDao:
    """和 models/skill_version_dao.py 同样的接口，数据放在内存里。"""

    def __init__(self):
        self.rows = []
        self._id = 0

    def latest(self, db, skill_id):
        rows = [r for r in self.rows if r.skill_id == skill_id]
        return max(rows, key=lambda r: r.version_no) if rows else None

    def create(self, db, skill_id, name, description, config_text, note, created_by):
        last = self.latest(db, skill_id)
        self._id += 1
        row = SimpleNamespace(id=self._id, skill_id=skill_id, version_no=(last.version_no if last else 0) + 1,
                              name=name, description=description, config_text=config_text, note=note,
                              created_by=created_by, created_at=datetime(2026, 9, 21, 12, 0, 0))
        self.rows.append(row)
        return row

    def list_versions(self, db, skill_id, limit=50):
        return sorted((r for r in self.rows if r.skill_id == skill_id), key=lambda r: -r.version_no)[:limit]

    def get(self, db, skill_id, version_id):
        return next((r for r in self.rows if r.skill_id == skill_id and r.id == version_id), None)

    def prune(self, db, skill_id, keep):
        rows = sorted((r for r in self.rows if r.skill_id == skill_id), key=lambda r: -r.version_no)
        for r in rows[keep:]:
            self.rows.remove(r)
        return max(0, len(rows) - keep)

    def delete_all(self, db, skill_id):
        self.rows = [r for r in self.rows if r.skill_id != skill_id]


class VersioningTestBase(unittest.TestCase):
    OWNER = 7

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="skillver_")
        self.root = os.path.join(self.base, "skills")
        os.makedirs(os.path.join(self.root, "user_created"))
        self.config_file = "user_created/u7_demo.yml"
        self.write_config("旧提示词")
        self.skill = SimpleNamespace(
            id=1, user_id=self.OWNER, name="演示技能", description="演示说明",
            config_file=self.config_file, is_public=1, created_at=None,
            lifecycle_status="draft", row_version=0,
        )
        self.dao = FakeVersionDao()
        self.updates = []
        self.db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
        self._patches = [
            patch.object(skill_loader, "SKILLS_ROOT", self.root),
            patch.object(versioning, "vdao", self.dao),
            patch.object(versioning, "dao_get", lambda db, sid: self.skill if sid == 1 else None),
            patch.object(versioning, "dao_update", self._record_update),
            patch.object(crud, "dao_get", lambda db, sid: self.skill if sid == 1 else None),
        ]
        for p in self._patches:
            p.start()
        skill_loader.invalidate_skill_config()

    def _record_update(self, db, sid, **kwargs):
        self.updates.append(kwargs)
        return self.skill

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.base, ignore_errors=True)
        skill_loader.invalidate_skill_config()

    def write_config(self, prompt, extra=None):
        cfg = {"name": "演示技能", "description": "演示说明", "tools": [{"name": "word_count", "defaults": {}}],
               "system_prompt": prompt, **(extra or {})}
        with open(os.path.join(self.root, self.config_file), "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        skill_loader.invalidate_skill_config()

    def prompt_on_disk(self):
        skill_loader.invalidate_skill_config()
        return skill_loader.load_skill_config(self.config_file)["system_prompt"]


class SnapshotTest(VersioningTestBase):
    def test_snapshot_dedupes_and_numbers_versions(self):
        self.assertIsNotNone(versioning.snapshot_skill(self.db, self.skill, self.OWNER, "第一次"))
        self.assertIsNone(versioning.snapshot_skill(self.db, self.skill, self.OWNER, "内容没变，不重复存"))
        self.write_config("新提示词")
        row = versioning.snapshot_skill(self.db, self.skill, self.OWNER, "第二次")
        self.assertEqual(row.version_no, 2)
        self.assertEqual(len(self.dao.rows), 2)

    def test_name_or_description_change_alone_creates_a_version(self):
        versioning.snapshot_skill(self.db, self.skill, self.OWNER, "a")
        self.skill.description = "换了一句说明"
        self.assertIsNotNone(versioning.snapshot_skill(self.db, self.skill, self.OWNER, "b"))

    def test_only_the_most_recent_versions_are_kept(self):
        for i in range(versioning.MAX_VERSIONS + 5):
            self.write_config(f"提示词 {i}")
            versioning.snapshot_skill(self.db, self.skill, self.OWNER, str(i))
        kept = self.dao.list_versions(self.db, 1, 100)
        self.assertEqual(len(kept), versioning.MAX_VERSIONS)
        self.assertEqual(kept[0].version_no, versioning.MAX_VERSIONS + 5)

    def test_snapshot_before_edit_only_for_the_owner_and_never_raises(self):
        versioning.snapshot_before_edit(self.db, 1, user_id=999)     # 别人的技能：不存
        self.assertEqual(self.dao.rows, [])
        versioning.snapshot_before_edit(self.db, 1, user_id=self.OWNER)
        self.assertEqual(len(self.dao.rows), 1)
        with patch.object(versioning, "dao_get", side_effect=RuntimeError("db down")):
            versioning.snapshot_before_edit(self.db, 1, user_id=self.OWNER)   # 出错不能挡住编辑

    def test_list_returns_metadata_without_the_config_text(self):
        versioning.snapshot_skill(self.db, self.skill, self.OWNER, "备注")
        rows = versioning.list_skill_versions(self.db, 1, self.OWNER)
        self.assertEqual(rows[0]["note"], "备注")
        self.assertNotIn("config_text", rows[0])
        self.assertGreater(rows[0]["size"], 0)
        self.assertIsNone(versioning.list_skill_versions(self.db, 1, 999))

    def test_deleting_a_skill_clears_its_versions(self):
        versioning.snapshot_skill(self.db, self.skill, self.OWNER, "x")
        versioning.delete_versions(self.db, 1)
        self.assertEqual(self.dao.rows, [])


class EditingCreatesSnapshotsTest(VersioningTestBase):
    def test_editing_the_prompt_saves_the_old_version_first(self):
        ok = crud.update_skill_config(self.db, 1, self.OWNER, system_prompt="改过的提示词", tool_names=["word_count"])
        self.assertTrue(ok)
        self.assertEqual(self.prompt_on_disk(), "改过的提示词")
        versions = self.dao.list_versions(self.db, 1)
        self.assertEqual(len(versions), 1)
        self.assertIn("旧提示词", versions[0].config_text)
        self.assertEqual(versions[0].note, "编辑前自动保存")

    def test_someone_elses_edit_is_refused_and_leaves_no_snapshot(self):
        self.assertFalse(crud.update_skill_config(self.db, 1, 999, system_prompt="x", tool_names=["word_count"]))
        self.assertEqual(self.dao.rows, [])

    def test_admin_can_manage_a_skill_owned_by_another_account(self):
        ok = crud.update_skill_config(
            self.db, 1, 999, system_prompt="管理员修订", tool_names=["word_count"], allow_admin=True,
        )
        self.assertTrue(ok)
        self.assertEqual(self.prompt_on_disk(), "管理员修订")

    def test_combined_edit_creates_one_snapshot_without_an_intermediate_version(self):
        def update_row(db, skill_id, **fields):
            for key, value in fields.items():
                setattr(self.skill, key, value)
            return self.skill

        with patch.object(crud, "dao_update", update_row):
            result = crud.update_skill_with_config(
                self.db,
                1,
                self.OWNER,
                fields={"name": "新名称", "description": "新说明"},
                config_fields={"system_prompt": "新提示词", "tool_names": ["word_count"]},
            )
        self.assertIsNotNone(result)
        self.assertEqual(len(self.dao.rows), 1)
        self.assertEqual(self.dao.rows[0].name, "演示技能")
        self.assertIn("旧提示词", self.dao.rows[0].config_text)
        self.assertEqual(self.prompt_on_disk(), "新提示词")

    def test_invalid_edit_keeps_the_published_file_unchanged(self):
        path = os.path.join(self.root, self.config_file)
        with open(path, "r", encoding="utf-8") as handle:
            before = handle.read()
        ok = crud._write_skill_config(
            self.config_file,
            name=self.skill.name,
            description=self.skill.description,
            system_prompt="不应发布",
            tool_names=["word_count"],
            permissions={"network": False, "file_read": ["missing.md"], "exec": False},
        )
        self.assertFalse(ok)
        with open(path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), before)
        self.assertFalse(any(name.startswith(".skill-") for name in os.listdir(os.path.dirname(path))))


class RestoreTest(VersioningTestBase):
    def _edit(self, prompt):
        crud.update_skill_config(self.db, 1, self.OWNER, system_prompt=prompt, tool_names=["word_count"])

    def test_restore_brings_back_the_old_prompt_and_name(self):
        self._edit("第二版")
        v1 = self.dao.list_versions(self.db, 1)[0]                     # "旧提示词" 那一版
        self.skill.name = "被改坏的名字"
        result = versioning.restore_skill_version(self.db, 1, v1.id, self.OWNER)
        self.assertEqual(result, {"restored_to": 1, "name": "演示技能"})
        self.assertEqual(self.prompt_on_disk(), "旧提示词")
        self.assertEqual(self.updates[-1], {"name": "演示技能", "description": "演示说明"})

    def test_restore_itself_can_be_undone(self):
        self._edit("第二版")
        v1 = self.dao.list_versions(self.db, 1)[0]
        versioning.restore_skill_version(self.db, 1, v1.id, self.OWNER)
        auto = self.dao.list_versions(self.db, 1)[0]                   # 恢复前自动保存的"第二版"
        self.assertIn("恢复到 v1 前自动保存", auto.note)
        versioning.restore_skill_version(self.db, 1, auto.id, self.OWNER)
        self.assertEqual(self.prompt_on_disk(), "第二版")

    def test_not_the_owner_gets_none_and_nothing_changes(self):
        self._edit("第二版")
        v1 = self.dao.list_versions(self.db, 1)[0]
        self.assertIsNone(versioning.restore_skill_version(self.db, 1, v1.id, 999))
        self.assertEqual(self.prompt_on_disk(), "第二版")

    def test_unknown_version_is_a_clear_error(self):
        with self.assertRaises(VersionError):
            versioning.restore_skill_version(self.db, 1, 424242, self.OWNER)

    def test_a_version_that_is_no_longer_valid_is_refused_and_current_config_is_kept(self):
        # 旧版本引用了一个如今不存在的资源文件：写回去会让技能加载失败
        bad = self.dao.create(self.db, 1, "演示技能", "演示说明", yaml.safe_dump({
            "name": "演示技能", "tools": [], "system_prompt": "坏版本",
            "permissions": {"file_read": ["missing.md"]}, "resources": ["missing.md"],
            "resource_root": os.path.join(self.base, "skills_packages", "imported", "gone", "resources"),
        }, allow_unicode=True), "坏的", self.OWNER)
        with self.assertRaises(VersionError) as ctx:
            versioning.restore_skill_version(self.db, 1, bad.id, self.OWNER)
        self.assertIn("未恢复", str(ctx.exception))
        self.assertEqual(self.prompt_on_disk(), "旧提示词")           # 当前配置原样保住
        self.assertEqual(self.updates, [])                              # 没有去改名称/说明


if __name__ == "__main__":
    unittest.main()
