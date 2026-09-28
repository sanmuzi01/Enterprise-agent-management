"""官方 Skill / GitHub 仓库 zip / 本平台能力包的导入测试。

和 test_skill_import_export.py 一样只 mock 落库，解析、校验、文件读写都走真实逻辑。
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

import service.tools  # noqa: F401 - 确保内置工具已注册
from service.skills import loader as skill_loader
from service.skills_core import github_import, package_import
from service.skills_core.package_import import SkillImportError, import_skill_bundle, parse_skill_md

OFFICIAL_MD = """---
name: pdf-helper
description: Use this skill when the user wants to work with PDF files: extract text, merge, split.
allowed-tools: Bash, Read
---

# PDF helper

Run `scripts/extract.py` to pull text out of the PDF.
"""


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class BundleTestBase(unittest.TestCase):
    def setUp(self):
        self._base = tempfile.mkdtemp(prefix="skill_bundle_")
        self.root = os.path.join(self._base, "skills")
        os.makedirs(self.root)
        ids = count(1)
        self.created = []

        def fake_create(db, user_id, name, description, config_file, is_public=0):
            s = SimpleNamespace(id=next(ids), user_id=user_id, name=name, description=description,
                                config_file=config_file, is_public=is_public, created_at=None,
                                lifecycle_status="draft", row_version=0)
            self.created.append(s)
            return s

        self._patches = [
            patch.object(skill_loader, "SKILLS_ROOT", self.root),
            patch.object(package_import, "SKILLS_ROOT", self.root),
            patch.object(package_import, "dao_create", side_effect=fake_create),
        ]
        for p in self._patches:
            p.start()
        skill_loader.invalidate_skill_config()
        self.db = SimpleNamespace(commit=lambda: None)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self._base, ignore_errors=True)
        skill_loader.invalidate_skill_config()

    def run_import(self, files, filename="x.zip", **kw):
        return import_skill_bundle(self.db, 1, filename, _zip(files) if isinstance(files, dict) else files, **kw)

    def cfg(self, skill):
        with open(os.path.join(self.root, skill["config_file"]), encoding="utf-8") as f:
            return yaml.safe_load(f)


class ParseFrontmatterTest(unittest.TestCase):
    def test_parses_name_and_description(self):
        meta, body = parse_skill_md(OFFICIAL_MD)
        self.assertEqual(meta["name"], "pdf-helper")
        self.assertIn("# PDF helper", body)

    def test_falls_back_when_yaml_is_broken_by_unquoted_colon(self):
        text = "---\nname: x\ndescription: Use when: the user asks: things\n---\nbody"
        meta, body = parse_skill_md(text)
        self.assertEqual(meta["name"], "x")
        self.assertTrue(meta["description"].startswith("Use when"))
        self.assertEqual(body, "body")

    def test_no_frontmatter_returns_whole_text(self):
        meta, body = parse_skill_md("just a prompt")
        self.assertEqual((meta, body), ({}, "just a prompt"))


class OfficialSkillImportTest(BundleTestBase):
    def test_official_folder_zip_keeps_python_scripts_in_a_bundle(self):
        res = self.run_import({
            "pdf-helper/SKILL.md": OFFICIAL_MD,
            "pdf-helper/scripts/extract.py": "print('hi')",
            "pdf-helper/scripts/run.sh": "echo hi",
            "pdf-helper/reference.md": "# ref\nmore details",
            "pdf-helper/logo.png": b"\x89PNG",
            "pdf-helper/bin/tool.exe": b"MZ",
        })
        skill = res["imported"][0]
        self.assertEqual(skill["name"], "pdf-helper")
        self.assertEqual(skill["script_count"], 1)
        cfg = self.cfg(skill)
        self.assertIn("# PDF helper", cfg["system_prompt"])
        self.assertIn("适用场景", cfg["system_prompt"])
        self.assertEqual(cfg["origin"], "official")
        self.assertEqual(cfg["tools"], [])
        self.assertFalse(cfg["permissions"]["exec"])  # 脚本不在主服务里执行
        self.assertEqual(cfg["permissions"]["file_read"], ["reference.md"])
        self.assertEqual(cfg["scripts"], ["scripts/extract.py"])
        notes = " ".join(skill["notes"])
        self.assertIn("Python 脚本", notes)
        self.assertIn("非 Python 脚本", notes)
        self.assertIn("allowed-tools", notes)
        # 脚本包保留了 py 和图片，但不含可执行二进制；脚本不在 resources（提示词资源）里
        bundle = cfg["scripts_root"]
        self.assertTrue(os.path.exists(os.path.join(bundle, "scripts", "extract.py")))
        self.assertTrue(os.path.exists(os.path.join(bundle, "logo.png")))
        self.assertFalse(os.path.exists(os.path.join(bundle, "bin", "tool.exe")))
        resource_root = cfg["resource_root"]
        self.assertFalse(os.path.exists(os.path.join(resource_root, "scripts", "extract.py")))
        self.assertTrue(os.path.exists(os.path.join(resource_root, "reference.md")))

    def test_oversized_bundle_file_is_skipped_with_a_note(self):
        with patch.object(package_import, "MAX_BUNDLE_FILE_BYTES", 1000):
            res = self.run_import({
                "s/SKILL.md": "---\nname: s\n---\nbody",
                "s/scripts/run.py": "print(1)",
                "s/data/big.bin": b"a" * 5000,
            })
        skill = res["imported"][0]
        bundle = self.cfg(skill)["scripts_root"]
        self.assertTrue(os.path.exists(os.path.join(bundle, "scripts", "run.py")))
        self.assertFalse(os.path.exists(os.path.join(bundle, "data", "big.bin")))
        self.assertIn("太大或太多", " ".join(skill["notes"]))

    def test_skill_without_python_scripts_has_no_bundle(self):
        res = self.run_import({"s/SKILL.md": "---\nname: s\n---\nbody", "s/logo.png": b"\x89PNG"})
        skill = res["imported"][0]
        self.assertEqual(skill["script_count"], 0)
        self.assertNotIn("scripts_root", self.cfg(skill))
        self.assertIn("非文本", " ".join(skill["notes"]))

    def test_notes_say_sandbox_is_off_when_disabled(self):
        with patch.dict(os.environ, {"SANDBOX_ENABLED": "false"}):
            res = self.run_import({"s/SKILL.md": "---\nname: s\n---\nbody", "s/a.py": "print(1)"})
        self.assertIn("沙箱还没开启", " ".join(res["imported"][0]["notes"]))

    def test_editing_an_imported_skill_keeps_its_script_info(self):
        from service.skills_core import crud
        res = self.run_import({"s/SKILL.md": "---\nname: s\n---\nbody", "s/a.py": "print(1)"})
        skill = res["imported"][0]
        self.assertTrue(crud._write_skill_config(skill["config_file"], "s", "d", "new prompt", ["word_count"], None))
        cfg = self.cfg(skill)
        self.assertEqual(cfg["system_prompt"], "new prompt")
        self.assertEqual(cfg["scripts"], ["a.py"])
        self.assertEqual(cfg["origin"], "official")
        self.assertTrue(os.path.isdir(cfg["scripts_root"]))

    def test_repo_zip_with_multiple_skills_imports_each_and_skips_template(self):
        res = self.run_import({
            "skills-main/README.md": "repo readme",
            "skills-main/skills/a/SKILL.md": "---\nname: a\ndescription: A\n---\nbody a",
            "skills-main/skills/b/SKILL.md": "---\nname: b\ndescription: B\n---\nbody b",
            "skills-main/template/SKILL.md": "---\nname: template-skill\n---\nplaceholder",
        })
        self.assertEqual(sorted(s["name"] for s in res["imported"]), ["a", "b"])

    def test_subpath_limits_import_to_one_folder(self):
        res = self.run_import({
            "skills-main/skills/a/SKILL.md": "---\nname: a\n---\nbody a",
            "skills-main/skills/b/SKILL.md": "---\nname: b\n---\nbody b",
        }, subpath="skills/b")
        self.assertEqual([s["name"] for s in res["imported"]], ["b"])

    def test_one_bad_skill_does_not_block_the_others(self):
        res = self.run_import({
            "good/SKILL.md": "---\nname: good\n---\nbody",
            "empty/SKILL.md": "---\nname: empty\n---\n",
        })
        self.assertEqual([s["name"] for s in res["imported"]], ["good"])
        self.assertEqual(res["failed"][0]["name"], "empty")

    def test_resource_budget_keeps_prompt_small(self):
        big = "x" * 10_000
        res = self.run_import({
            "s/SKILL.md": "---\nname: s\n---\nbody",
            "s/a.md": big, "s/b.md": big, "s/c.md": big,
        })
        skill = res["imported"][0]
        self.assertEqual(skill["resource_count"], 3)
        self.assertEqual(skill["prompt_resource_count"], 1)  # 16000 字预算只装得下 1 个
        self.assertTrue(any("并入了提示词" in n for n in skill["notes"]))

    def test_license_files_do_not_use_up_the_prompt_budget(self):
        res = self.run_import({
            "s/SKILL.md": "---\nname: s\n---\nbody",
            "s/LICENSE.txt": "legal " * 100,
            "s/REFERENCE.md": "useful reference",
        })
        skill = res["imported"][0]
        self.assertEqual(skill["resource_count"], 2)  # 许可证仍然保存
        self.assertEqual(self.cfg(skill)["permissions"]["file_read"], ["REFERENCE.md"])

    def test_single_skill_md_upload(self):
        res = self.run_import(OFFICIAL_MD.encode("utf-8"), filename="SKILL.md")
        self.assertEqual(res["imported"][0]["name"], "pdf-helper")

    def test_official_skill_at_zip_root(self):
        res = self.run_import({"SKILL.md": OFFICIAL_MD}, filename="my-skill.zip")
        self.assertEqual(res["imported"][0]["name"], "pdf-helper")

    def test_skill_that_imports_can_be_loaded_by_runtime(self):
        res = self.run_import({"s/SKILL.md": OFFICIAL_MD, "s/ref.md": "reference text"})
        cfg = skill_loader.load_skill_config(res["imported"][0]["config_file"])
        self.assertIn("reference text", cfg["resource_text"])


class LegacyManifestPackageTest(BundleTestBase):
    def test_manifest_in_subfolder_and_unknown_tool_is_ignored_with_note(self):
        manifest = {"name": "essay", "display_name": "论文助手", "tools": ["word_count", "no_such_tool"],
                    "constraints": "不要编造引用", "permissions": {"file_read": ["guide.md"]}}
        res = self.run_import({
            "essay/manifest.yaml": yaml.safe_dump(manifest, allow_unicode=True),
            "essay/SKILL.md": "你是论文助手",
            "essay/resources/guide.md": "写作指南",
        })
        skill = res["imported"][0]
        self.assertEqual(skill["name"], "论文助手")
        cfg = self.cfg(skill)
        self.assertEqual([t["name"] for t in cfg["tools"]], ["word_count"])
        self.assertIn("不要编造引用", cfg["system_prompt"])
        self.assertEqual(cfg["permissions"]["file_read"], ["guide.md"])
        self.assertTrue(any("no_such_tool" in n for n in skill["notes"]))


class RejectionTest(BundleTestBase):
    def assertRejected(self, files, message_part, **kw):
        with self.assertRaises(SkillImportError) as ctx:
            self.run_import(files, **kw)
        self.assertIn(message_part, str(ctx.exception))
        packages = os.path.join(self._base, "skills_packages", "imported")
        self.assertEqual(os.listdir(packages) if os.path.exists(packages) else [], [])

    def test_zip_without_skill_md(self):
        self.assertRejected({"readme.txt": "hi"}, "没有找到 Skill")

    def test_not_a_zip(self):
        self.assertRejected(b"not a zip", "不是有效的 zip", filename="x.zip")

    def test_unsupported_extension(self):
        self.assertRejected(b"hi", "不支持这种文件", filename="x.txt")

    def test_path_traversal_rejected(self):
        self.assertRejected({"../evil/SKILL.md": "x"}, "不安全")

    def test_empty_body(self):
        self.assertRejected({"s/SKILL.md": "---\nname: s\n---\n"}, "正文是空的")

    def test_too_many_skills(self):
        files = {f"s{i}/SKILL.md": f"---\nname: s{i}\n---\nbody" for i in range(package_import.MAX_SKILLS_PER_UPLOAD + 1)}
        self.assertRejected(files, "最多导入")

    def test_oversized_skill_md_is_rejected(self):
        with patch.object(package_import, "MAX_TEXT_MEMBER_BYTES", 100):
            self.assertRejected({"s/SKILL.md": "---\nname: s\n---\n" + "x" * 500}, "SKILL.md 太大")

    def test_oversized_manifest_is_rejected(self):
        with patch.object(package_import, "MAX_TEXT_MEMBER_BYTES", 100):
            self.assertRejected({"SKILL.md": "---\nname: s\n---\nbody", "manifest.yaml": "name: s\n# " + "x" * 500},
                                "manifest.yaml 太大")

    def test_yaml_missing_tools_gives_specific_reason(self):
        self.assertRejected(yaml.safe_dump({"name": "x"}).encode(), "缺少 tools", filename="x.yml")

    def test_yaml_unknown_tool_lists_available(self):
        content = yaml.safe_dump({"name": "x", "tools": ["nope"]}).encode()
        self.assertRejected(content, "本平台没有：nope", filename="x.yml")


class GithubUrlTest(unittest.TestCase):
    def test_parse_variants(self):
        p = github_import.parse_github_url
        self.assertEqual(p("https://github.com/anthropics/skills"), ("anthropics", "skills", "HEAD", ""))
        self.assertEqual(p("https://github.com/anthropics/skills/"), ("anthropics", "skills", "HEAD", ""))
        self.assertEqual(p("https://github.com/anthropics/skills.git"), ("anthropics", "skills", "HEAD", ""))
        self.assertEqual(p("https://github.com/anthropics/skills/tree/main/skills/pdf"),
                         ("anthropics", "skills", "main", "skills/pdf"))
        self.assertEqual(p("https://github.com/anthropics/skills/blob/main/skills/pdf/SKILL.md"),
                         ("anthropics", "skills", "main", "skills/pdf"))

    def test_rejects_non_github_and_odd_hosts(self):
        for url in ["http://169.254.169.254/latest", "https://evil.com/github.com/a/b",
                    "https://github.com.evil.com/a/b", "file:///etc/passwd", "", "https://github.com/onlyowner"]:
            with self.assertRaises(SkillImportError, msg=url):
                github_import.parse_github_url(url)

    def test_download_only_ever_hits_codeload(self):
        seen = []

        class Resp:
            status_code = 200
            headers = {}

            def iter_content(self, chunk_size):
                yield b"zipbytes"

        def fake_get(url, **kw):
            seen.append((url, kw.get("allow_redirects")))
            return Resp()

        with patch.object(github_import.requests, "get", side_effect=fake_get):
            data = github_import.download_repo_zip("anthropics", "skills", "main")
        self.assertEqual(data, b"zipbytes")
        self.assertEqual(seen, [("https://codeload.github.com/anthropics/skills/zip/main", False)])

    def test_redirect_to_other_host_is_refused(self):
        class Resp:
            status_code = 302
            headers = {"Location": "http://10.0.0.1/x"}

        with patch.object(github_import.requests, "get", return_value=Resp()):
            with self.assertRaises(SkillImportError):
                github_import.download_repo_zip("a", "b", "HEAD")

    def test_network_failure_gives_manual_download_hint(self):
        with patch.object(github_import.requests, "get", side_effect=github_import.requests.ConnectionError()):
            with self.assertRaises(SkillImportError) as ctx:
                github_import.download_repo_zip("a", "b", "HEAD")
        self.assertIn("Download ZIP", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
