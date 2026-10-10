"""技能配置里的资源 / 脚本目录要能换机器用（service/skills/loader.py::localize_root，读取时换算）。

现象：在 Windows 上导入的技能，部署到 Docker（Linux）后全部显示“permissions.file_read 包含不存在的资源”、能力不可用。
原因：导入时把 D:\\PyCharm\\...\\skills_packages\\imported\\x\\resources 这样的本机绝对路径写进了配置；
容器里资源文件其实在 /app/skills_packages/imported/x/resources，但配置指向的路径不存在。
"""
import os
import shutil
import unittest
import uuid

import yaml

from service.skills import loader


class PortableRootTest(unittest.TestCase):
    def setUp(self):
        self.name = f"rt_portable_{uuid.uuid4().hex[:8]}"
        self.package = os.path.join(loader._packages_base(), "imported", self.name)
        os.makedirs(os.path.join(self.package, "resources", "references"))
        with open(os.path.join(self.package, "resources", "references", "character-profile.md"), "w", encoding="utf-8") as f:
            f.write("小省导购员：亲切、专业")
        self.config_file = f"imported/{self.name}.yml"
        self.yml = loader._get_yml_path(self.config_file)

    def tearDown(self):
        shutil.rmtree(self.package, ignore_errors=True)
        if os.path.exists(self.yml):
            os.remove(self.yml)
        loader.invalidate_skill_config(self.config_file)

    def _write(self, resource_root):
        config = {
            "name": "数字人视频提示词", "description": "x", "version": "1.0", "tools": [],
            "permissions": {"network": False, "file_read": ["references/character-profile.md"], "exec": False},
            "resource_root": resource_root, "resources": ["references/character-profile.md"], "system_prompt": "x",
        }
        with open(self.yml, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True)
        loader.invalidate_skill_config(self.config_file)

    def _load(self):
        return loader.load_skill_config(self.config_file)

    def test_windows_path_from_the_importing_machine_still_works_here(self):
        self._write(f"D:\\PyCharm\\PythonProject1\\skills_packages\\imported\\{self.name}\\resources")
        cfg = self._load()
        self.assertEqual(os.path.realpath(cfg["resource_root"]), os.path.realpath(os.path.join(self.package, "resources")))
        self.assertIn("小省导购员", cfg["resource_text"], "资源能读到，技能可用")

    def test_linux_path_from_another_server_still_works_here(self):
        self._write(f"/srv/agent-app/skills_packages/imported/{self.name}/resources")
        self.assertIn("小省导购员", self._load()["resource_text"])

    def test_relative_path_from_the_project_root_also_works(self):
        self._write(f"skills_packages/imported/{self.name}/resources")
        self.assertIn("小省导购员", self._load()["resource_text"], "以 skills_packages/ 开头的相对路径按项目根目录解析")

    def test_local_path_that_does_not_exist_yet_is_left_alone(self):
        """本机管理目录下、只是还没建出来的路径，不做换算（测试会临时替换 SKILLS_ROOT，不能被换回真实项目目录）。"""
        local = os.path.join(loader._packages_base(), "imported", "not_created_yet", "resources")
        self.assertEqual(loader.localize_root(local), local)

    def test_cannot_escape_the_managed_directories(self):
        for evil in ("skills_packages/../..", "/etc/skills_packages/../../..", "C:\\Windows\\System32"):
            self._write(evil)
            with self.assertRaises(loader.SkillValidationError, msg=evil):
                self._load()

    def test_missing_resource_is_still_reported(self):
        self._write(f"D:\\x\\skills_packages\\imported\\{self.name}\\resources")
        os.remove(os.path.join(self.package, "resources", "references", "character-profile.md"))
        loader.invalidate_skill_config(self.config_file)
        with self.assertRaises(loader.SkillValidationError) as ctx:
            self._load()
        self.assertIn("包含不存在的资源", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
