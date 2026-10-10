"""运行就绪检查：每项独立失败、给出处理办法、不泄露连接信息。"""
import os
import unittest
from unittest.mock import MagicMock, patch

from tests._async_helpers import run_async

from service import readiness


class ReadinessTest(unittest.TestCase):
    def test_hub_down_is_reported_with_fix_and_does_not_break_others(self):
        with patch("service.readiness.requests.get", side_effect=ConnectionError("refused http://127.0.0.1:8090")):
            item = readiness._enterprise_hub()
        self.assertFalse(item["ok"])
        self.assertIn("连不上", item["message"])
        self.assertIn("enterprise-business-hub", item["fix"])
        self.assertNotIn("127.0.0.1", item["message"] + item["fix"])   # 不回显内部地址

    def test_hub_up(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"status": "UP"}
        with patch("service.readiness.requests.get", return_value=response):
            self.assertTrue(readiness._enterprise_hub()["ok"])

    def test_model_item_reflects_offline_mode(self):
        with patch("service.llm.offline_demo.enabled", return_value=True):
            item = readiness._model()
        self.assertEqual(item["level"], "warn")
        self.assertIn("离线演示模型", item["message"])
        with patch("service.llm.offline_demo.enabled", return_value=False):
            self.assertEqual(readiness._model()["level"], "ok")

    def test_reminders_switch(self):
        with patch.dict(os.environ, {"REMINDERS_ENABLED": "0"}):
            self.assertFalse(readiness._worker()["ok"])
        with patch.dict(os.environ, {"REMINDERS_ENABLED": "1"}):
            self.assertTrue(readiness._worker()["ok"])

    def test_database_and_migration_against_real_db(self):
        try:
            database = readiness._database()
        except Exception:  # noqa: BLE001
            self.skipTest("没有本地 MySQL")
        if not database["ok"]:
            self.skipTest("没有本地 MySQL")
        migration = readiness._migration()
        self.assertTrue(migration["ok"], migration["message"])

    def test_collect_returns_all_items_and_overall_flag(self):
        with patch("service.readiness._enterprise_hub", return_value=readiness._item("enterprise_hub", "x", False, "down")):
            result = run_async(readiness.collect())
        self.assertEqual([c["key"] for c in result["checks"]],
                         ["database", "migration", "enterprise_hub", "model", "reminders", "demo_data"])
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
