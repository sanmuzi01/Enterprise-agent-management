"""部门助手要能换算“昨天”“下周一”：系统提示词里带上企业所在时区的今天日期。"""
import os
import unittest
from datetime import datetime
from unittest import mock

from service.runtime.agent_runtime import _today_line


class TodayLineTest(unittest.TestCase):
    def test_uses_beijing_time_by_default(self):
        # UTC 10 月 8 日 17:30 = 北京时间 10 月 9 日 01:30（星期五）
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("APP_TIMEZONE", None)
            line = _today_line(datetime(2026, 10, 8, 17, 30))
        self.assertIn("今天是 2026-10-09（星期五）", line)
        self.assertIn("昨天", line)

    def test_timezone_is_configurable(self):
        with mock.patch.dict(os.environ, {"APP_TIMEZONE": "UTC"}):
            line = _today_line(datetime(2026, 10, 8, 17, 30))
        self.assertIn("今天是 2026-10-08（星期四）", line)

    def test_unknown_timezone_falls_back_to_utc_plus_8(self):
        with mock.patch.dict(os.environ, {"APP_TIMEZONE": "Not/AZone"}):
            line = _today_line(datetime(2026, 10, 8, 17, 30))
        self.assertIn("2026-10-09", line)


if __name__ == "__main__":
    unittest.main()
