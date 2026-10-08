"""漏洞豁免登记表的核对逻辑（scripts/check_vuln_exceptions.py）：到期 / 缺字段 / ci.yml 与登记表不一致都要报。"""
import datetime
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_vuln_exceptions", ROOT / "scripts" / "check_vuln_exceptions.py")
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

ENTRY = {"id": "CVE-X-1", "package": "pkg", "owner": "某人", "added": "2026-10-07", "expires": "2026-12-01", "reason": "不受影响", "recheck": "出修复版本就升级"}


class VulnExceptionTests(unittest.TestCase):
    def check_with(self, entries, workflow_text, today="2026-10-07"):
        with tempfile.TemporaryDirectory() as tmp:
            register, workflow = pathlib.Path(tmp) / "r.json", pathlib.Path(tmp) / "ci.yml"
            register.write_text(json.dumps({"exceptions": entries}), encoding="utf-8")
            workflow.write_text(workflow_text, encoding="utf-8")
            with mock.patch.object(checker, "REGISTER", register), mock.patch.object(checker, "WORKFLOW", workflow):
                return checker.check(datetime.date.fromisoformat(today))

    def test_the_real_register_is_consistent_with_ci_today(self):
        self.assertEqual(checker.check(datetime.date(2026, 10, 8)), [])      # 固定日期：结构和 ci.yml 的一致性；到期由 CI 里的脚本用真实日期核对

    def test_consistent_register_passes(self):
        self.assertEqual(self.check_with([ENTRY], "pip-audit --ignore-vuln CVE-X-1"), [])

    def test_expired_exception_fails(self):
        problems = self.check_with([ENTRY], "--ignore-vuln CVE-X-1", today="2026-12-02")
        self.assertTrue(any("到期" in p for p in problems), problems)

    def test_ignored_but_not_registered_fails(self):
        problems = self.check_with([ENTRY], "--ignore-vuln CVE-X-1 --ignore-vuln CVE-Y-2")
        self.assertTrue(any("CVE-Y-2" in p and "登记表里没有" in p for p in problems), problems)

    def test_registered_but_no_longer_ignored_fails(self):
        problems = self.check_with([ENTRY], "pip-audit")
        self.assertTrue(any("CVE-X-1" in p and "删掉" in p for p in problems), problems)

    def test_missing_fields_fail(self):
        problems = self.check_with([{**ENTRY, "owner": " ", "recheck": ""}], "--ignore-vuln CVE-X-1")
        self.assertTrue(any("owner" in p for p in problems) and any("recheck" in p for p in problems), problems)

    def test_overlong_exception_fails(self):
        problems = self.check_with([{**ENTRY, "expires": "2028-01-01"}], "--ignore-vuln CVE-X-1")
        self.assertTrue(any("180" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
