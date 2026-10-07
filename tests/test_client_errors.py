"""前端页面错误进入问题中心：规范化（打包哈希、行号、页面编号不拆问题）、脱敏、噪声丢弃、限流、字段上限（真实 MySQL）。"""
import os
import unittest
from unittest.mock import patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import SessionLocal
from service.observability import client_errors as ce

_AVAILABLE, _WHY = rc.route_tests_available()

STACK_A = ("TypeError: Cannot read properties of undefined (reading 'name')\n"
           "    at setup (http://localhost:5173/assets/AttendanceModule-Cx7a9bQ2.js:12:34)\n"
           "    at callWithErrorHandling (http://localhost:5173/assets/index-Zk3d8LmP.js:5:6)")
STACK_B = ("TypeError: Cannot read properties of undefined (reading 'name')\n"
           "    at setup (http://localhost:5173/assets/AttendanceModule-Q9w8e7r6.js:99:1)\n"
           "    at callWithErrorHandling (http://localhost:5173/assets/index-Aa1Bb2Cc.js:77:8)")


class NormalizationTest(unittest.TestCase):
    def test_routes_lose_ids_and_query(self):
        self.assertEqual(ce.normalize_route("/department/42/tasks/7?x=1#top"), "/department/:id/tasks/:id")
        self.assertEqual(ce.normalize_route("/a/123e4567-e89b-12d3-a456-426614174000"), "/a/:id")
        self.assertEqual(ce.normalize_route(""), "/")

    def test_frames_ignore_bundle_hash_and_line_numbers(self):
        self.assertEqual(ce.frames_of(STACK_A), ce.frames_of(STACK_B))
        self.assertEqual(ce.frames_of(STACK_A), "AttendanceModule.js>index.js")
        dev = "Error: x\n    at foo (http://localhost:5173/src/components/AttendanceModule.vue?t=17000:45:6)"
        self.assertEqual(ce.frames_of(dev), "AttendanceModule.vue")

    def test_noise_is_dropped(self):
        for message, stack in (("ResizeObserver loop completed with undelivered notifications.", ""), ("Script error.", ""),
                               ("boom", "at x (chrome-extension://abc/content.js:1:1)"), ("Failed to fetch dynamically imported module: /a.js", "")):
            with self.subTest(message=message):
                self.assertTrue(ce.is_noise(message, stack))
        self.assertFalse(ce.is_noise("Cannot read properties of undefined", STACK_A))


@unittest.skipUnless(_AVAILABLE, _WHY)
class ClientErrorEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.db = SessionLocal()
        cls.clean()

    @classmethod
    def tearDownClass(cls):
        cls.clean()
        cls.db.close()

    @classmethod
    def clean(cls):
        cls.db.commit()
        ids = "SELECT id FROM system_issue WHERE service='web-frontend'"
        for table in ("issue_occurrence", "issue_event"):
            cls.db.execute(text(f"DELETE FROM {table} WHERE issue_id IN ({ids})"))
        cls.db.execute(text("DELETE FROM system_issue WHERE service='web-frontend'"))
        cls.db.commit()

    def setUp(self):
        self.clean()

    def send(self, **over):
        body = {"name": "TypeError", "message": "Cannot read properties of undefined (reading 'name')", "stack": STACK_A, "route": "/department", "source": "vue:setup"}
        body.update(over)
        return self.client.post("/client-errors", json=body)

    def issues(self):
        self.db.commit()
        return self.db.execute(text("SELECT title, occurrence_count, severity, category, error_code, status FROM system_issue WHERE service='web-frontend'")).all()

    def test_anonymous_report_creates_one_aggregated_issue(self):
        first = self.send()
        self.assertEqual(first.status_code, 202, first.text)
        self.send(stack=STACK_B, route="/department")                     # 换了打包哈希和行号：还是同一个问题
        rows = self.issues()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].occurrence_count, rows[0].error_code, rows[0].category), (2, "FRONTEND_ERROR", "code"))

    def test_different_code_or_route_is_a_different_issue(self):
        self.send()
        self.send(route="/agents")
        self.send(stack=STACK_A.replace("AttendanceModule", "ResponsibilityModule"))
        self.assertEqual(len(self.issues()), 3)

    def test_page_ids_do_not_split_issues(self):
        self.send(route="/department/12")
        self.send(route="/department/987")
        self.assertEqual([r.occurrence_count for r in self.issues()], [2])

    def test_secrets_in_message_and_stack_are_redacted(self):
        self.send(message="请求失败 Authorization: Bearer abcdefghijklmnop1234567890", stack="Error\n    at x (http://a/b.js:1:1)\napi_key=sk-secret1234567890abcdef")
        self.db.commit()
        row = self.db.execute(text("SELECT o.message, o.detail_json FROM issue_occurrence o JOIN system_issue i ON i.id=o.issue_id WHERE i.service='web-frontend'")).first()
        self.assertNotIn("abcdefghijklmnop1234567890", row.message)
        self.assertNotIn("sk-secret1234567890abcdef", row.detail_json)

    def test_noise_is_accepted_but_not_recorded(self):
        response = self.send(message="ResizeObserver loop completed with undelivered notifications.", stack="")
        self.assertEqual((response.status_code, response.json()["ignored"]), (202, True))
        self.assertEqual(self.issues(), [])

    def test_stackless_rejections_group_by_message_without_numbers(self):
        self.send(name="Error", message="加载第 3 页失败", stack="")
        self.send(name="Error", message="加载第 17 页失败", stack="")
        self.assertEqual([r.occurrence_count for r in self.issues()], [2])

    def test_field_limits_and_empty_message(self):
        self.assertEqual(self.send(message="").status_code, 422)
        self.assertEqual(self.send(message="x" * 501).status_code, 422)
        self.assertEqual(self.send(stack="x" * 4001).status_code, 422)

    def test_per_ip_rate_limit(self):
        from utils.rate_limit import rate_limiter
        with patch.dict(os.environ, {"CLIENT_ERROR_IP_LIMIT": "2"}):
            with rate_limiter._lock:
                rate_limiter._items.clear()           # 前面的用例已经用掉了同一个测试 IP 的配额
            statuses = [self.send(message=f"m{i}").status_code for i in range(4)]
        self.assertEqual(statuses[:2], [202, 202])
        self.assertIn(429, statuses[2:])


if __name__ == "__main__":
    unittest.main()
