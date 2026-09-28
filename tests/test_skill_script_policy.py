"""脚本沙箱的"信任边界"测试：谁能带脚本、配置能指向哪里、商店安装是否保留脚本。

重点防的是：普通用户上传一个 yml，把 scripts_root / resource_root 指到项目根目录（里面有 .env），
再通过沙箱把文件读出来。
"""
import io
import os
import shutil
import tempfile
import unittest
import zipfile
from itertools import count
from types import SimpleNamespace
from unittest.mock import patch

import yaml

import service.tools  # noqa: F401
from FasdtApi import skill_route
from service.skills import loader as skill_loader
from service.skills_core import binding, import_export, package_import
from service.skills_core.package_import import SkillImportError, import_skill_bundle

SCRIPTED_ZIP_FILES = {
    "pdf/SKILL.md": "---\nname: pdf\ndescription: d\n---\nRun scripts/a.py",
    "pdf/scripts/a.py": "print(1)",
}


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, c in files.items():
            zf.writestr(n, c)
    return buf.getvalue()


class PolicyTestBase(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="policy_")
        self.root = os.path.join(self.base, "skills")
        os.makedirs(os.path.join(self.root, "imported"))
        ids = count(1)
        self.rows = {}

        def fake_create(db, user_id, name, description, config_file, is_public=0):
            row = SimpleNamespace(id=next(ids), user_id=user_id, name=name, description=description,
                                  config_file=config_file, is_public=is_public, created_at=None,
                                  lifecycle_status="draft", row_version=0)
            self.rows[row.id] = row
            return row

        self._patches = [
            patch.object(skill_loader, "SKILLS_ROOT", self.root),
            patch.object(package_import, "SKILLS_ROOT", self.root),
            patch.object(import_export, "SKILLS_ROOT", self.root),
            patch.object(package_import, "dao_create", side_effect=fake_create),
            patch.object(import_export, "dao_create", side_effect=fake_create),
        ]
        for p in self._patches:
            p.start()
        skill_loader.invalidate_skill_config()
        self.db = SimpleNamespace(commit=lambda: None)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.base, ignore_errors=True)
        skill_loader.invalidate_skill_config()

    def cfg(self, skill):
        with open(os.path.join(self.root, skill["config_file"]), encoding="utf-8") as f:
            return yaml.safe_load(f)


class ConfigCannotPointOutsideManagedDirsTest(PolicyTestBase):
    def _yml(self, extra: dict) -> bytes:
        return yaml.safe_dump({"name": "evil", "tools": [], "system_prompt": "x", **extra}).encode()

    def test_yml_upload_cannot_declare_script_fields(self):
        for key, val in (("scripts_root", "/app"), ("scripts", ["a.py"]), ("origin", "official")):
            with self.assertRaises(SkillImportError, msg=key):
                import_skill_bundle(self.db, 1, "e.yml", self._yml({key: val}))

    def test_loader_refuses_scripts_root_outside_skills_packages(self):
        outside = os.path.join(self.base, "secret_dir")
        os.makedirs(outside)
        path = os.path.join(self.root, "imported", "hand_written.yml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"name": "x", "tools": [], "scripts_root": outside, "scripts": ["a.py"]}, f)
        with self.assertRaises(skill_loader.SkillValidationError):
            skill_loader.load_skill_config("imported/hand_written.yml")

    def test_loader_refuses_resource_root_outside_managed_dirs(self):
        path = os.path.join(self.root, "imported", "res.yml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"name": "x", "tools": [], "resource_root": self.base,
                            "resources": ["a.md"], "permissions": {"file_read": ["a.md"]}}, f)
        with self.assertRaises(skill_loader.SkillValidationError):
            skill_loader.load_skill_config("imported/res.yml")

    def test_loader_refuses_non_python_and_traversal_in_scripts(self):
        pkg = os.path.join(self.base, "skills_packages", "imported", "p", "bundle")
        os.makedirs(pkg)
        for bad in (["run.sh"], ["../../x.py"]):
            path = os.path.join(self.root, "imported", "s.yml")
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump({"name": "x", "tools": [], "scripts_root": pkg, "scripts": bad}, f)
            skill_loader.invalidate_skill_config()
            with self.assertRaises(skill_loader.SkillValidationError, msg=str(bad)):
                skill_loader.load_skill_config("imported/s.yml")


class ScriptsAreAdminOnlyTest(PolicyTestBase):
    def test_import_without_script_permission_keeps_only_the_text(self):
        res = import_skill_bundle(self.db, 5, "pdf.zip", _zip(SCRIPTED_ZIP_FILES), allow_scripts=False)
        skill = res["imported"][0]
        cfg = self.cfg(skill)
        self.assertNotIn("scripts_root", cfg)
        self.assertNotIn("scripts", cfg)
        self.assertEqual(skill["script_count"], 0)
        self.assertIn("只保留管理员导入", " ".join(skill["notes"]))
        pkg = os.path.join(self.base, "skills_packages", "imported")
        self.assertFalse(any(os.path.exists(os.path.join(pkg, d, "bundle")) for d in os.listdir(pkg)))

    def test_import_policy_is_decided_by_role_even_though_the_route_is_admin_only(self):
        user = SimpleNamespace()
        with patch.object(skill_route, "is_admin_user", return_value=True):
            self.assertEqual(skill_route._import_policy(user, 1), {"allow_scripts": True, "is_public": 1})
            self.assertEqual(skill_route._import_policy(user, 0), {"allow_scripts": True, "is_public": 0})
            self.assertEqual(skill_route._import_policy(user, 7), {"allow_scripts": True, "is_public": 0})  # 只认 1
        # 纵深防御：万一以后路由放宽，非管理员既不能带脚本，也不能上架
        with patch.object(skill_route, "is_admin_user", return_value=False):
            self.assertEqual(skill_route._import_policy(user, 1), {"allow_scripts": False, "is_public": 0})

    def test_import_routes_require_the_admin_dependency(self):
        """导入是最危险的入口，直接检查路由依赖，不依赖数据库。"""
        from service.dependencies import get_current_admin_user

        wanted = {"/skill/import", "/skill/import/github", "/skill/admin/reanalyze",
                  "/skill/{skill_id}/versions", "/skill/{skill_id}/versions/{version_id}/restore"}
        found = set()
        for route in skill_route.router.routes:
            if route.path in wanted:
                found.add(route.path)
                deps = [d.call for d in route.dependant.dependencies]
                self.assertIn(get_current_admin_user, deps, f"{route.path} 必须要求管理员")
        self.assertEqual(found, wanted)


class StoreInstallKeepsScriptsTest(PolicyTestBase):
    def test_user_installing_an_admin_skill_gets_runnable_scripts(self):
        res = import_skill_bundle(self.db, 1, "pdf.zip", _zip(SCRIPTED_ZIP_FILES), is_public=1)
        source = self.rows[res["imported"][0]["id"]]
        self.assertEqual(source.is_public, 1)

        with patch.object(import_export, "dao_get", return_value=source):
            installed = import_export.install_public_skill(self.db, user_id=9, skill_id=source.id)
        self.assertIsNotNone(installed)
        self.assertNotEqual(installed["user_id"], source.user_id)
        cfg = self.cfg(installed)
        self.assertEqual(cfg["scripts"], ["scripts/a.py"])
        self.assertEqual(cfg["origin"], "official")
        self.assertTrue(os.path.isfile(os.path.join(cfg["scripts_root"], "scripts", "a.py")))

        with patch.dict(os.environ, {"SANDBOX_ENABLED": "true", "SANDBOX_TOKEN": "t"}), \
                patch.object(binding, "dao_list_by_agent", return_value=[SimpleNamespace(**installed)]):
            merged = binding.get_agent_skills_merged_config(SimpleNamespace(), 1)
        self.assertIn("run_skill_script", merged["tool_names"])
        self.assertEqual(merged["skill_bundles"]["pdf"]["scripts"], ["scripts/a.py"])


if __name__ == "__main__":
    unittest.main()
