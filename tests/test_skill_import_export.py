"""Skill 导入（yml / zip 包）与导出链路的接口级回归测试。

只 mock 数据库落库（models.skill_dao.create_skill / get_skill_by_id），
其余环节——YAML 解析、ToolRegistry 校验、zip 安全检查、文件真实读写——
都用临时目录跑真实逻辑，覆盖的正是 project-status.md 里点名的
“Skill 导入”这条尚缺接口级测试的核心链路。
"""
import io
import os
import shutil
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import yaml

import service.tools  # noqa: F401 - 确保 word_count 等内置工具已注册
from service import skill_service
from service.skills import loader as skill_loader
from service.skills_core import import_export as skill_import_export


VALID_SKILL_YAML = {
    "name": "字数统计助手",
    "description": "统计文本字数",
    "tools": ["word_count"],
    "system_prompt": "你是一个字数统计助手。",
}


def _make_zip(manifest: dict, skill_md: str = "你是一个测试 Skill。", extra_files=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.yaml", yaml.safe_dump(manifest, allow_unicode=True))
        zf.writestr("SKILL.md", skill_md)
        for name, content in (extra_files or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


class SkillImportExportTestBase(unittest.TestCase):
    def setUp(self):
        # import_skill_from_upload 的 zip 分支会把解压内容写到
        # os.path.dirname(SKILLS_ROOT)/skills_packages/imported/ 下（对应生产环境的
        # <项目根>/skills_packages/），所以把 SKILLS_ROOT 放在一个可整体清理的临时目录里，
        # 而不是直接用临时目录本身，避免在系统 /tmp 下残留 skills_packages。
        self._base_tmp = tempfile.mkdtemp(prefix="skill_import_export_")
        self.tmp_root = os.path.join(self._base_tmp, "skills")
        os.makedirs(self.tmp_root, exist_ok=True)
        self._patches = [
            patch.object(skill_loader, "SKILLS_ROOT", self.tmp_root),
            patch.object(skill_import_export, "SKILLS_ROOT", self.tmp_root),
        ]
        for p in self._patches:
            p.start()
        skill_loader.invalidate_skill_config()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self._base_tmp, ignore_errors=True)
        skill_loader.invalidate_skill_config()


class ImportYamlSkillTest(SkillImportExportTestBase):
    def test_import_valid_yaml_creates_file_and_db_record(self):
        fake_skill = SimpleNamespace(
            id=1, user_id=1, name="字数统计助手", description="统计文本字数",
            config_file="imported/placeholder.yml", is_public=0, created_at=None,
            lifecycle_status="draft", row_version=0,
        )
        db = SimpleNamespace(commit=lambda: None)
        content = yaml.safe_dump(VALID_SKILL_YAML, allow_unicode=True).encode("utf-8")

        with patch.object(skill_import_export, "dao_create", return_value=fake_skill) as mock_create:
            result = skill_service.import_skill_from_upload(
                db, user_id=1, filename="word_count_skill.yml", content=content,
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["id"], 1)
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        self.assertEqual(kwargs["name"], "字数统计助手")
        self.assertTrue(kwargs["config_file"].startswith("imported/u1_"))
        # 文件确实写入了临时 SKILLS_ROOT
        self.assertTrue(os.path.exists(os.path.join(self.tmp_root, kwargs["config_file"])))

    def test_import_invalid_yaml_missing_tools_field_is_rejected_and_cleaned_up(self):
        db = SimpleNamespace(commit=lambda: None)
        bad_yaml = {"name": "缺工具字段"}  # 缺少必填的 tools 字段
        content = yaml.safe_dump(bad_yaml, allow_unicode=True).encode("utf-8")

        with patch.object(skill_import_export, "dao_create") as mock_create:
            result = skill_service.import_skill_from_upload(
                db, user_id=1, filename="broken.yml", content=content,
            )

        self.assertIsNone(result)
        mock_create.assert_not_called()
        imported_dir = os.path.join(self.tmp_root, "imported")
        leftover = [f for f in os.listdir(imported_dir)] if os.path.exists(imported_dir) else []
        self.assertEqual(leftover, [], "校验失败的 yml 文件应当被清理，不留垃圾文件")

    def test_import_rejects_unsupported_extension(self):
        db = SimpleNamespace(commit=lambda: None)
        with patch.object(skill_import_export, "dao_create") as mock_create:
            result = skill_service.import_skill_from_upload(
                db, user_id=1, filename="not_a_skill.txt", content=b"hello",
            )
        self.assertIsNone(result)
        mock_create.assert_not_called()


class ImportZipSkillTest(SkillImportExportTestBase):
    def test_import_valid_zip_package(self):
        fake_skill = SimpleNamespace(
            id=2, user_id=1, name="论文写作助手", description="",
            config_file="imported/placeholder.yml", is_public=0, created_at=None,
            lifecycle_status="draft", row_version=0,
        )
        db = SimpleNamespace(commit=lambda: None)
        manifest = {
            "name": "essay_helper",
            "display_name": "论文写作助手",
            "description": "帮助写论文大纲",
            "tools": ["word_count"],
        }
        content = _make_zip(manifest)

        with patch.object(skill_import_export, "dao_create", return_value=fake_skill) as mock_create:
            result = skill_service.import_skill_from_upload(
                db, user_id=1, filename="essay.zip", content=content,
            )

        self.assertIsNotNone(result)
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        self.assertEqual(kwargs["name"], "论文写作助手")
        saved_path = os.path.join(self.tmp_root, kwargs["config_file"])
        self.assertTrue(os.path.exists(saved_path))
        with open(saved_path, "r", encoding="utf-8") as f:
            saved_cfg = yaml.safe_load(f)
        tool_names_in_file = [tool.get("name") for tool in saved_cfg.get("tools", [])]
        self.assertIn("word_count", tool_names_in_file)

    def test_import_zip_with_unsafe_file_is_rejected(self):
        db = SimpleNamespace(commit=lambda: None)
        manifest = {"name": "evil", "tools": ["word_count"]}
        content = _make_zip(manifest, extra_files={"payload.exe": "MZ..."})

        with patch.object(skill_import_export, "dao_create") as mock_create:
            result = skill_service.import_skill_from_upload(
                db, user_id=1, filename="evil.zip", content=content,
            )

        self.assertIsNone(result)
        mock_create.assert_not_called()
        packages_dir = os.path.join(os.path.dirname(self.tmp_root), "skills_packages", "imported")
        # 不安全文件应当被拒绝，解压目录被清理（如果曾经创建过的话）
        if os.path.exists(packages_dir):
            self.assertEqual(os.listdir(packages_dir), [])

    def test_import_zip_missing_skill_md_is_rejected(self):
        db = SimpleNamespace(commit=lambda: None)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.yaml", yaml.safe_dump({"name": "x", "tools": ["word_count"]}))
        with patch.object(skill_import_export, "dao_create") as mock_create:
            result = skill_service.import_skill_from_upload(
                db, user_id=1, filename="incomplete.zip", content=buf.getvalue(),
            )
        self.assertIsNone(result)
        mock_create.assert_not_called()


class ExportSkillPackageTest(SkillImportExportTestBase):
    def test_export_produces_zip_with_manifest_and_skill_md(self):
        config_file = "user_created/u1_test_export.yml"
        full_path = os.path.join(self.tmp_root, config_file)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            yaml.safe_dump({
                "name": "导出测试",
                "description": "用于导出测试",
                "tools": ["word_count"],
                "system_prompt": "导出用的提示词",
            }, f, allow_unicode=True)

        fake_skill = SimpleNamespace(
            id=3, user_id=1, name="导出测试", description="用于导出测试",
            config_file=config_file, is_public=0, created_at=None,
            lifecycle_status="draft", row_version=0,
        )
        db = SimpleNamespace()
        # fake_skill.user_id == user_id，can_read_skill 走真实逻辑即可返回 True，不需要 mock
        with patch.object(skill_import_export, "dao_get", return_value=fake_skill):
            result = skill_service.export_skill_package(db, skill_id=3, user_id=1)

        self.assertIsNotNone(result)
        self.assertTrue(os.path.exists(result["path"]))
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            self.assertIn("manifest.yaml", names)
            self.assertIn("SKILL.md", names)
            self.assertEqual(zf.read("SKILL.md").decode("utf-8"), "导出用的提示词")
            manifest = yaml.safe_load(zf.read("manifest.yaml"))
            self.assertEqual(manifest["display_name"], "导出测试")
        os.remove(result["path"])


if __name__ == "__main__":
    unittest.main()
