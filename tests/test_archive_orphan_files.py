"""孤儿文件：对应助手已不存在的提示词 / 专业技能配置（service/data_health.py::orphan_agent_files、
scripts/archive_orphan_agent_files.py）。体检要能查出来；归档是“移到 backups/ 并留清单”，不是删除。"""
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_script():
    spec = importlib.util.spec_from_file_location("archive_orphan_agent_files", ROOT / "scripts" / "archive_orphan_agent_files.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class OrphanPromptFileTest(unittest.TestCase):
    def test_prompt_file_without_agent_is_reported(self):
        import prompt.prompt_manager as pm
        from models.init_db import SessionLocal
        from service import data_health
        orphan = pm.PROMPT_DIR / "987654321.yaml"          # 测试里 PROMPT_DIR 是临时目录（见 tests/_route_client.py）
        orphan.write_text("role: x\n", encoding="utf-8")
        try:
            with SessionLocal() as db:
                self.assertIn(str(orphan), data_health.orphan_agent_files(db)["prompt"])
                with mock.patch("service.data_health.SAMPLE_LIMIT", 100_000):
                    found = data_health.check_orphan_prompt_files(db)
            self.assertTrue(any(s.endswith("987654321.yaml") for s in found.samples))
        finally:
            orphan.unlink(missing_ok=True)


class ArchiveScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "prompt" / "prompts").mkdir(parents=True)
        (self.root / "skills" / "enterprise").mkdir(parents=True)
        self.orphan = self.root / "prompt" / "prompts" / "42.yaml"
        self.tracked = self.root / "prompt" / "prompts" / "1.yaml"
        self.skill = self.root / "skills" / "enterprise" / "agent_42.yml"
        for f in (self.orphan, self.tracked, self.skill):
            f.write_text("x\n", encoding="utf-8")
        self.script = _load_script()

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, argv, agents=5):
        db = mock.MagicMock()
        db.__enter__.return_value = db
        db.execute.return_value.scalar.return_value = agents
        orphans = {"prompt": [str(self.orphan), str(self.tracked)], "skill": [str(self.skill)]}
        with mock.patch.object(self.script, "ROOT", self.root), \
                mock.patch.object(self.script, "SessionLocal", return_value=db), \
                mock.patch.object(self.script.data_health, "orphan_agent_files", return_value=orphans), \
                mock.patch.object(self.script, "_tracked_files", return_value={str(self.tracked.resolve())}), \
                mock.patch("service.audit_service.record") as audit, mock.patch("builtins.print"):
            code = self.script.main(argv)
        return code, audit

    def test_dry_run_moves_nothing(self):
        code, audit = self.run_script([])
        self.assertEqual(code, 0)
        self.assertTrue(self.orphan.exists() and self.skill.exists())
        audit.assert_not_called()

    def test_apply_moves_to_backups_with_a_manifest_and_keeps_tracked_files(self):
        code, audit = self.run_script(["--apply"])
        self.assertEqual(code, 0)
        self.assertFalse(self.orphan.exists())
        self.assertFalse(self.skill.exists())
        self.assertTrue(self.tracked.exists(), "git 跟踪的文件不动")
        archive = next((self.root / "backups").glob("orphan_agent_files_*"))
        manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(m["from"] for m in manifest), ["prompt/prompts/42.yaml", "skills/enterprise/agent_42.yml"])
        for m in manifest:
            self.assertTrue((self.root / m["to"]).exists(), "按清单能找到归档后的文件")
        self.assertEqual(audit.call_args.args[1], "data_health.orphan_files_archived")

    def test_refuses_when_the_database_has_no_agents(self):
        code, _ = self.run_script(["--apply"], agents=0)
        self.assertEqual(code, 1, "一个助手都没有多半是连错了库，所有文件都会被当成孤儿")
        self.assertTrue(self.orphan.exists())


if __name__ == "__main__":
    unittest.main()
