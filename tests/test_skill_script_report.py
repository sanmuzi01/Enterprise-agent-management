"""脚本兼容性静态检查、附件额度、脚本限频。"""
import io
import os
import re
import shutil
import tempfile
import textwrap
import unittest
import zipfile
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import service.tools  # noqa: F401
from service import attachment_service, sandbox
from service.skills import loader as skill_loader
from service.skills_core import binding, package_import
from service.skills_core.package_import import import_skill_bundle
from service.skills_core.script_report import PACKAGE_MODULES, analyze_bundle
from service.skills_core.validation import validate_skill_config_file
from service.tools.base import ToolContext
from service.tools.skill_script import RunSkillScriptTool
from utils.rate_limit import LimitExceeded

ROOT = Path(__file__).resolve().parent.parent


def _write(base, rel, code):
    path = os.path.join(base, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(code))


class AnalyzerTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="report_")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _analyze(self, files):
        for rel, code in files.items():
            _write(self.d, rel, code)
        return analyze_bundle(self.d, [r for r in files if r.startswith("scripts/") and r.endswith(".py")])

    def test_stdlib_sandbox_libs_and_sibling_modules_are_fine(self):
        r = self._analyze({
            "scripts/a.py": "import os, json\nimport pandas as pd\nfrom helper import x\nfrom openpyxl import load_workbook\nfrom docx import Document\n",
            "scripts/helper.py": "x = 1\n",
        })
        self.assertEqual(r["status"], "ready")
        self.assertEqual(r["runnable"], ["scripts/a.py", "scripts/helper.py"])

    def test_missing_third_party_packages_are_named(self):
        r = self._analyze({"scripts/a.py": "import torch\nimport transformers.models\nimport os\n"})
        self.assertEqual(r["status"], "unsupported")
        self.assertEqual(r["missing_packages"], ["torch", "transformers"])

    def test_network_libraries_block_the_script(self):
        r = self._analyze({"scripts/a.py": "import requests\n", "scripts/b.py": "import urllib.request\n",
                           "scripts/c.py": "import os\n"})
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["runnable"], ["scripts/c.py"])
        self.assertEqual(r["network"], 2)

    def test_subprocess_is_only_a_warning(self):
        r = self._analyze({"scripts/a.py": "import subprocess\nsubprocess.run(['ffmpeg'])\n"})
        self.assertEqual(r["status"], "ready")
        self.assertEqual(r["system"], 1)
        self.assertEqual(r["problems"]["scripts/a.py"], {"system": True})

    def test_optional_imports_do_not_count_as_missing(self):
        r = self._analyze({"scripts/a.py": "try:\n    import scipy\nexcept ImportError:\n    scipy = None\n"})
        self.assertEqual(r["status"], "ready")

    def test_syntax_errors_are_not_runnable(self):
        r = self._analyze({"scripts/a.py": "def (:\n"})
        self.assertEqual(r["status"], "unsupported")

    def test_no_scripts(self):
        self.assertEqual(analyze_bundle(self.d, [])["status"], "none")

    def test_module_table_matches_the_sandbox_requirements_file(self):
        names = set()
        for line in (ROOT / "sandbox" / "requirements.txt").read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if line:
                names.add(re.split(r"[=<>~!\[]", line)[0].strip().lower())
        names -= {"fastapi", "uvicorn"}  # 运行器自己用的
        self.assertEqual(names, set(PACKAGE_MODULES), "sandbox/requirements.txt 和 script_report.PACKAGE_MODULES 不一致")


class ImportAndBindingTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="reportimp_")
        self.root = os.path.join(self.base, "skills")
        os.makedirs(os.path.join(self.root, "imported"))
        ids = count(1)
        self._patches = [
            patch.object(skill_loader, "SKILLS_ROOT", self.root),
            patch.object(package_import, "SKILLS_ROOT", self.root),
            patch.object(package_import, "dao_create", side_effect=lambda **kw: SimpleNamespace(
                id=next(ids), created_at=None, lifecycle_status="draft", row_version=0,
                **{k: v for k, v in kw.items() if k != "db"})),
        ]
        for p in self._patches:
            p.start()
        skill_loader.invalidate_skill_config()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mix/SKILL.md", "---\nname: mix\ndescription: d\n---\nRun scripts")
            zf.writestr("mix/scripts/good.py", "import pandas\nprint(1)")
            zf.writestr("mix/scripts/net.py", "import requests\n")
        res = import_skill_bundle(SimpleNamespace(commit=lambda: None), 1, "mix.zip", buf.getvalue())
        self.skill = res["imported"][0]

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.base, ignore_errors=True)
        skill_loader.invalidate_skill_config()

    def test_import_records_the_report_and_says_so(self):
        self.assertEqual(self.skill["script_status"], "partial")
        self.assertEqual(self.skill["script_runnable"], 1)
        note = " ".join(self.skill["notes"])
        self.assertIn("兼容性检查", note)
        self.assertIn("requests", note)
        cfg = skill_loader.load_skill_config(self.skill["config_file"])
        self.assertEqual(cfg["runnable_scripts"], ["scripts/good.py"])
        self.assertEqual(len(cfg["scripts"]), 2)

    def test_validation_exposes_status_for_the_frontend_badges(self):
        with patch.dict(os.environ, {"SANDBOX_ENABLED": "true", "SANDBOX_TOKEN": "t"}):
            v = validate_skill_config_file(self.skill["config_file"])
        self.assertEqual((v["script_status"], v["script_count"], v["script_runnable"]), ("partial", 2, 1))
        self.assertTrue(v["sandbox_enabled"])
        self.assertIn("requests", v["script_missing_packages"])
        with patch.dict(os.environ, {"SANDBOX_ENABLED": "false"}):
            self.assertFalse(validate_skill_config_file(self.skill["config_file"])["sandbox_enabled"])

    def test_assistant_only_gets_the_runnable_scripts(self):
        with patch.dict(os.environ, {"SANDBOX_ENABLED": "true", "SANDBOX_TOKEN": "t"}), \
                patch.object(binding, "dao_list_by_agent", return_value=[SimpleNamespace(**self.skill)]):
            merged = binding.get_agent_skills_merged_config(SimpleNamespace(), 1)
        self.assertEqual(merged["skill_bundles"]["mix"]["scripts"], ["scripts/good.py"])
        self.assertNotIn("net.py", merged["system_prompt"])

    def test_skill_with_no_runnable_script_gets_no_tool(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("v/SKILL.md", "---\nname: v\n---\nbody")
            zf.writestr("v/a.py", "import torch\n")
        res = import_skill_bundle(SimpleNamespace(commit=lambda: None), 1, "v.zip", buf.getvalue())
        with patch.dict(os.environ, {"SANDBOX_ENABLED": "true", "SANDBOX_TOKEN": "t"}), \
                patch.object(binding, "dao_list_by_agent", return_value=[SimpleNamespace(**res["imported"][0])]):
            merged = binding.get_agent_skills_merged_config(SimpleNamespace(), 1)
        self.assertNotIn("run_skill_script", merged["tool_names"])
        self.assertEqual(merged["skill_bundles"], {})

    def test_reanalyze_picks_up_new_sandbox_dependencies_without_reimport(self):
        from service.skills_core import script_report
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("t/SKILL.md", "---\nname: t\n---\nbody")
            zf.writestr("t/a.py", "import torch\n")
        skill = import_skill_bundle(SimpleNamespace(commit=lambda: None), 1, "t.zip", buf.getvalue())["imported"][0]
        self.assertEqual(skill["script_status"], "unsupported")

        # 管理员往沙箱里加了 torch（改了 requirements 和 PACKAGE_MODULES）后，刷新检查结果
        with patch.object(script_report, "SANDBOX_MODULES", script_report.SANDBOX_MODULES | {"torch"}):
            res = script_report.reanalyze_config(skill["config_file"])
        self.assertEqual((res["changed"], res["before"], res["after"]), (True, "unsupported", "ready"))
        skill_loader.invalidate_skill_config()
        self.assertEqual(skill_loader.load_skill_config(skill["config_file"])["runnable_scripts"], ["a.py"])

        with patch.object(script_report, "SANDBOX_MODULES", script_report.SANDBOX_MODULES | {"torch"}):
            self.assertFalse(script_report.reanalyze_config(skill["config_file"])["changed"])  # 幂等

    def test_reanalyze_skips_skills_without_a_script_bundle(self):
        from service.skills_core import script_report
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("p/SKILL.md", "---\nname: p\n---\nbody")
        skill = import_skill_bundle(SimpleNamespace(commit=lambda: None), 1, "p.zip", buf.getvalue())["imported"][0]
        self.assertTrue(script_report.reanalyze_config(skill["config_file"])["skipped"])

    def test_loader_rejects_runnable_scripts_that_are_not_a_subset(self):
        import yaml
        path = os.path.join(self.root, self.skill["config_file"])
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["runnable_scripts"] = ["scripts/other.py"]
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)
        skill_loader.invalidate_skill_config()
        with self.assertRaises(skill_loader.SkillValidationError):
            skill_loader.load_skill_config(self.skill["config_file"])


class AttachmentQuotaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="quota_")
        self._p = patch.dict(os.environ, {"ATTACHMENT_DIR": self.tmp})
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_file_count_limit(self):
        with patch.dict(os.environ, {"ATTACHMENT_USER_MAX_FILES": "2"}):
            attachment_service.save(1, "a.txt", b"x")
            attachment_service.save(1, "b.txt", b"x")
            with self.assertRaises(attachment_service.AttachmentError) as ctx:
                attachment_service.save(1, "c.txt", b"x")
            attachment_service.save(2, "other-user.txt", b"x")  # 别人不受影响
        self.assertIn("数量", str(ctx.exception))

    def test_space_limit(self):
        with patch.dict(os.environ, {"ATTACHMENT_USER_MAX_MB": "1"}):
            attachment_service.save(1, "big.bin", b"x" * (700 * 1024))
            with self.assertRaises(attachment_service.AttachmentError) as ctx:
                attachment_service.save(1, "big2.bin", b"x" * (700 * 1024))
        self.assertIn("空间", str(ctx.exception))


class ToolLimitsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="toollimit_")
        self._p = patch.dict(os.environ, {"ATTACHMENT_DIR": os.path.join(self.tmp, "att")})
        self._p.start()
        bundle = os.path.join(self.tmp, "bundle")
        os.makedirs(os.path.join(bundle, "scripts"))
        Path(bundle, "scripts", "a.py").write_text("open('big.bin','wb').write(b'x'*600000)\nprint('ok')", encoding="utf-8")
        self.tool = RunSkillScriptTool()
        self.tool.set_context(ToolContext(user_id=1, skill_bundles={"s": {"root": bundle, "scripts": ["scripts/a.py"]}}))

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rate_limit_message(self):
        with patch.object(sandbox, "get_backend", return_value=object()), \
                patch("service.tools.skill_script.require_limit", side_effect=LimitExceeded("too many", retry_after=7)):
            self.assertIn("太频繁", self.tool.execute(script="scripts/a.py"))

    def test_outputs_that_cannot_be_saved_are_reported_not_silently_dropped(self):
        from tests.test_skill_sandbox import _RunnerBackend
        with patch.object(sandbox, "get_backend", return_value=_RunnerBackend()), \
                patch.dict(os.environ, {"ATTACHMENT_USER_MAX_MB": "0"}):
            # MB 限制最小为 1，600KB 的产出放得下；先占满空间再验证
            attachment_service.save(1, "fill.bin", b"x" * (900 * 1024))
            with patch.dict(os.environ, {"ATTACHMENT_USER_MAX_MB": "1"}):
                text = self.tool.execute(script="scripts/a.py")
        self.assertIn("没能保存", text)


if __name__ == "__main__":
    unittest.main()
