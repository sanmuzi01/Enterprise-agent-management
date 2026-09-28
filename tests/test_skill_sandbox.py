"""脚本沙箱链路测试：运行器 → 客户端 → run_skill_script 工具 → 附件存取 → 绑定合并。

运行器直接用 FastAPI TestClient 在进程内起；脚本真的会在临时目录里被 Python 子进程执行，
但这里只跑测试自己写的几行代码。容器层面的隔离（无网络、只读根、资源上限）在这里测不到，
那部分要靠 docker-compose.prod.yml 的配置和部署时的实机验证。
"""
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import service.tools  # noqa: F401 - 注册内置工具 + run_skill_script
from service import attachment_service, sandbox
from service.skills import loader as skill_loader
from service.skills_core import binding, package_import
from service.tools.base import ToolContext
from service.tools.skill_script import RunSkillScriptTool

TOKEN = "test-token"
ROOT = Path(__file__).resolve().parent.parent


def _load_runner():
    os.environ["SANDBOX_TOKEN"] = TOKEN
    spec = importlib.util.spec_from_file_location("sandbox_runner_under_test", ROOT / "sandbox" / "runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runner = _load_runner()
client = TestClient(runner.app)


def _b64(text: str) -> str:
    import base64
    return base64.b64encode(text.encode()).decode()


def _run(files: dict, script: str, args=None, timeout=10, token=TOKEN):
    return client.post(
        "/run",
        json={"files": [{"path": p, "b64": _b64(c)} for p, c in files.items()], "script": script,
              "args": args or [], "timeout": timeout},
        headers={"X-Sandbox-Token": token},
    )


class RunnerTest(unittest.TestCase):
    def test_runs_script_and_returns_stdout(self):
        r = _run({"a.py": "import sys\nprint('hello', sys.argv[1:])"}, "a.py", ["x", "y"])
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["exit_code"], 0)
        self.assertIn("hello ['x', 'y']", body["stdout"])

    def test_returns_only_new_or_changed_files(self):
        code = "open('outputs_dir_file.txt','w').write('new')\nimport os\nos.makedirs('outputs',exist_ok=True)\nopen('outputs/r.txt','w').write('R')"
        r = _run({"a.py": code, "inputs/in.txt": "same"}, "a.py")
        paths = sorted(o["path"] for o in r.json()["outputs"])
        self.assertEqual(paths, ["outputs/r.txt", "outputs_dir_file.txt"])  # 没动过的输入文件、脚本本身都不回传

    def test_nonzero_exit_and_stderr(self):
        r = _run({"a.py": "import sys\nsys.stderr.write('boom')\nsys.exit(3)"}, "a.py")
        self.assertEqual(r.json()["exit_code"], 3)
        self.assertIn("boom", r.json()["stderr"])

    def test_timeout_kills_the_script(self):
        r = _run({"a.py": "while True: pass"}, "a.py", timeout=1)
        body = r.json()
        self.assertTrue(body["timed_out"])
        self.assertEqual(body["exit_code"], -1)

    def test_child_does_not_inherit_environment_secrets(self):
        code = "import os\nprint(os.environ.get('SANDBOX_TOKEN'), os.environ.get('DB_PASSWORD'))"
        with patch.dict(os.environ, {"DB_PASSWORD": "secret"}):
            r = _run({"a.py": code}, "a.py")
        self.assertIn("None None", r.json()["stdout"])

    def test_wrong_token_is_rejected(self):
        self.assertEqual(_run({"a.py": "print(1)"}, "a.py", token="nope").status_code, 401)

    def test_rejects_bad_paths_and_non_python(self):
        self.assertEqual(_run({"../evil.py": "print(1)"}, "../evil.py").status_code, 400)
        self.assertEqual(_run({"/abs.py": "print(1)"}, "/abs.py").status_code, 400)
        self.assertEqual(_run({"a.sh": "echo"}, "a.sh").status_code, 400)
        self.assertEqual(_run({"a.py": "print(1)"}, "missing.py").status_code, 400)

    def test_busy_sandbox_returns_503(self):
        with patch.object(runner, "_slots") as slots:
            slots.acquire.return_value = False
            self.assertEqual(_run({"a.py": "print(1)"}, "a.py").status_code, 503)


class _RunnerBackend(sandbox.RunnerBackend):
    """把 HTTP 调用改接到进程内的运行器。"""

    def __init__(self):
        super().__init__("http://sandbox", TOKEN)

    def run(self, files, script, args, timeout):
        with patch.object(sandbox.requests, "post", side_effect=lambda url, json, headers, timeout: client.post(
                "/run", json=json, headers=headers)):
            return super().run(files, script, args, timeout)


class SandboxToggleTest(unittest.TestCase):
    def test_disabled_by_default_and_needs_token(self):
        with patch.dict(os.environ, {"SANDBOX_ENABLED": "false", "SANDBOX_TOKEN": "t"}):
            self.assertFalse(sandbox.is_enabled())
            self.assertIsNone(sandbox.get_backend())
        with patch.dict(os.environ, {"SANDBOX_ENABLED": "true", "SANDBOX_TOKEN": ""}):
            self.assertFalse(sandbox.is_enabled())
        with patch.dict(os.environ, {"SANDBOX_ENABLED": "true", "SANDBOX_TOKEN": "t"}):
            self.assertTrue(sandbox.is_enabled())
            self.assertIsInstance(sandbox.get_backend(), sandbox.RunnerBackend)

    def test_unreachable_runner_gives_friendly_error(self):
        backend = sandbox.RunnerBackend("http://127.0.0.1:9", "t")
        with self.assertRaises(sandbox.SandboxUnavailable):
            backend.run({"a.py": b"print(1)"}, "a.py", [], 2)


class AttachmentServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="att_")
        self._p = patch.dict(os.environ, {"ATTACHMENT_DIR": self.tmp})
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_and_resolve_are_scoped_to_the_owner(self):
        meta = attachment_service.save(1, "报告.pdf", b"data")
        self.assertEqual(meta["name"], "报告.pdf")
        path, name = attachment_service.resolve(1, meta["id"])
        self.assertEqual(path.read_bytes(), b"data")
        self.assertIsNone(attachment_service.resolve(2, meta["id"]))

    def test_rejects_bad_ids_executables_empty_and_oversize(self):
        self.assertIsNone(attachment_service.resolve(1, "../../etc"))
        self.assertIsNone(attachment_service.resolve(1, "x" * 24))
        with self.assertRaises(attachment_service.AttachmentError):
            attachment_service.save(1, "run.exe", b"MZ")
        with self.assertRaises(attachment_service.AttachmentError):
            attachment_service.save(1, "a.txt", b"")
        with self.assertRaises(attachment_service.AttachmentError):
            attachment_service.save(1, "a.txt", b"x" * (attachment_service.MAX_UPLOAD_BYTES + 1))

    def test_filename_is_sanitized(self):
        meta = attachment_service.save(1, "../../evil/../a b.txt", b"x")
        self.assertNotIn("/", meta["name"])
        self.assertNotIn("..", meta["name"])

    def test_expired_files_are_purged_on_next_upload(self):
        old = attachment_service.save(1, "old.txt", b"x")
        folder = Path(self.tmp) / "u1" / old["id"]
        past = folder.stat().st_mtime - 30 * 86400
        os.utime(folder, (past, past))
        attachment_service.save(1, "new.txt", b"y")
        self.assertIsNone(attachment_service.resolve(1, old["id"]))


class RunSkillScriptToolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tool_")
        self._p = patch.dict(os.environ, {"ATTACHMENT_DIR": os.path.join(self.tmp, "att")})
        self._p.start()
        self.bundle = os.path.join(self.tmp, "bundle")
        os.makedirs(os.path.join(self.bundle, "scripts"))
        Path(self.bundle, "scripts", "upper.py").write_text(
            "import sys, os\n"
            "src, dst = sys.argv[1], sys.argv[2]\n"
            "os.makedirs(os.path.dirname(dst), exist_ok=True)\n"
            "open(dst, 'w').write(open(src).read().upper())\n"
            "print('done')\n", encoding="utf-8")
        self.ctx = ToolContext(user_id=1, skill_bundles={"upper": {"root": self.bundle, "scripts": ["scripts/upper.py"]}})
        self.tool = RunSkillScriptTool()
        self.tool.set_context(self.ctx)

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _with_backend(self, backend):
        return patch.object(sandbox, "get_backend", return_value=backend)

    def test_end_to_end_input_attachment_to_downloadable_output(self):
        att = attachment_service.save(1, "note.txt", b"hello world")
        with self._with_backend(_RunnerBackend()):
            text = self.tool.execute(
                script="scripts/upper.py", args=["inputs/note.txt", "outputs/result.txt"], input_files=[att["id"]])
        self.assertIn("退出码 0", text)
        self.assertIn("done", text)
        link = [ln for ln in text.splitlines() if "attachment://" in ln][0]
        out_id = link.split("attachment://")[1].rstrip(")")
        path, name = attachment_service.resolve(1, out_id)
        self.assertEqual(name, "result.txt")
        self.assertEqual(path.read_text(), "HELLO WORLD")
        self.assertIsNone(attachment_service.resolve(2, out_id))  # 别的用户拿不到

    def test_disabled_sandbox_tells_the_model_honestly(self):
        with self._with_backend(None):
            self.assertIn("未开启", self.tool.execute(script="scripts/upper.py"))

    def test_unknown_script_and_unknown_skill_are_refused(self):
        with self._with_backend(_RunnerBackend()):
            self.assertIn("没有脚本", self.tool.execute(script="scripts/other.py"))
            self.assertIn("没有找到", self.tool.execute(skill="nope", script="scripts/upper.py"))
            self.assertIn("没有脚本", self.tool.execute(script="../../etc/passwd"))

    def test_missing_attachment_is_reported(self):
        with self._with_backend(_RunnerBackend()):
            self.assertIn("找不到附件", self.tool.execute(script="scripts/upper.py", input_files=["a" * 24]))

    def test_other_users_attachment_cannot_be_used(self):
        att = attachment_service.save(2, "secret.txt", b"x")
        with self._with_backend(_RunnerBackend()):
            self.assertIn("找不到附件", self.tool.execute(script="scripts/upper.py", input_files=[att["id"]]))

    def test_failed_script_shows_stderr(self):
        Path(self.bundle, "scripts", "bad.py").write_text("raise SystemExit('bad thing')", encoding="utf-8")
        self.ctx.skill_bundles["upper"]["scripts"].append("scripts/bad.py")
        with self._with_backend(_RunnerBackend()):
            text = self.tool.execute(script="scripts/bad.py")
        self.assertIn("退出码 1", text)
        self.assertIn("bad thing", text)

    def test_tool_is_hidden_from_the_skill_editor_tool_list(self):
        from service.skills_core.validation import list_available_tools
        self.assertNotIn("run_skill_script", [t["name"] for t in list_available_tools()])


class BindingMergeTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="bind_")
        self.root = os.path.join(self.base, "skills")
        os.makedirs(self.root)
        self._patches = [
            patch.object(skill_loader, "SKILLS_ROOT", self.root),
            patch.object(package_import, "SKILLS_ROOT", self.root),
            patch.object(package_import, "dao_create", side_effect=lambda **kw: SimpleNamespace(
                id=1, created_at=None, lifecycle_status="draft", row_version=0,
                **{k: v for k, v in kw.items() if k != "db"})),
        ]
        for p in self._patches:
            p.start()
        skill_loader.invalidate_skill_config()
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("pdf/SKILL.md", "---\nname: pdf\ndescription: d\n---\nRun scripts/a.py")
            zf.writestr("pdf/scripts/a.py", "print(1)")
        res = package_import.import_skill_bundle(SimpleNamespace(commit=lambda: None), 1, "pdf.zip", buf.getvalue())
        self.skill = SimpleNamespace(**res["imported"][0])

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.base, ignore_errors=True)
        skill_loader.invalidate_skill_config()

    def _merge(self):
        # get_agent_skills_merged_config 现在会查 Agent 的 owner 来判断"这是不是作者
        # 自己绑自己的 Skill"（P0：别人的共享 Skill 被作者改回非 published 后运行时
        # 要跟着停用，见 service/skills_core/binding.py）——这里测的是自己绑自己，
        # 伪造一个 owner 跟 self.skill.user_id 一致的 Agent，不受那条新规则影响。
        with patch.object(binding, "dao_list_by_agent", return_value=[self.skill]), \
                patch.object(binding, "get_agent_by_id", return_value=SimpleNamespace(user_id=self.skill.user_id)):
            return binding.get_agent_skills_merged_config(SimpleNamespace(), 1)

    def test_sandbox_on_adds_tool_bundle_and_instructions(self):
        with patch.dict(os.environ, {"SANDBOX_ENABLED": "true", "SANDBOX_TOKEN": "t"}):
            merged = self._merge()
        self.assertIn("run_skill_script", merged["tool_names"])
        self.assertEqual(merged["skill_bundles"]["pdf"]["scripts"], ["scripts/a.py"])
        self.assertIn("run_skill_script", merged["system_prompt"])
        self.assertNotIn("无法运行脚本", merged["system_prompt"])

    def test_sandbox_off_gives_no_tool_and_an_honest_note(self):
        with patch.dict(os.environ, {"SANDBOX_ENABLED": "false"}):
            merged = self._merge()
        self.assertNotIn("run_skill_script", merged["tool_names"])
        self.assertEqual(merged["skill_bundles"], {})
        self.assertIn("无法运行脚本", merged["system_prompt"])


if __name__ == "__main__":
    unittest.main()
