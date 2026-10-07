"""XSS 专项（后端部分）：前端渲染的防线见 scripts/check_xss_browser.py；这里守住后端能被利用的几个点——
响应头注入（导出文件名）、用户上传的 HTML / SVG 被当成页面渲染、任何接口把用户输入当 text/html 回显。"""
import mimetypes
import unittest
import urllib.parse
import uuid

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import Agent, Conversation, Message, SessionLocal

_AVAILABLE, _WHY = rc.route_tests_available()

SCRIPT = "<script>alert(1)</script>"
TITLES = ['会话"; evil=1', "a\r\nSet-Cookie: pwned=1", "a\nX-Injected: 1", SCRIPT, "../../etc/passwd", "x" * 300, "中文标题", "emoji 😀 title", "tab\ttitle", "", "   ",
          "CON", "a;b,c", "%0d%0aSet-Cookie: x=1", "‮exe.txt"]


@unittest.skipUnless(_AVAILABLE, _WHY)
class ExportHeaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.user = rc.create_user("xssb-u")
        cls.db = SessionLocal()
        agent = Agent(user_id=cls.user["id"], name="xss-agent")
        cls.db.add(agent)
        cls.db.commit()
        cls.agent_id = agent.id
        cls.conv_ids = []

    @classmethod
    def tearDownClass(cls):
        cls.db.commit()
        for conv_id in cls.conv_ids:
            cls.db.execute(text("DELETE FROM message WHERE conversation_id=:c"), {"c": conv_id})
            cls.db.execute(text("DELETE FROM conversation WHERE id=:c"), {"c": conv_id})
        cls.db.execute(text("DELETE FROM agent WHERE id=:a"), {"a": cls.agent_id})
        cls.db.commit()
        cls.db.close()
        rc.cleanup()

    def conversation(self, title):
        conv = Conversation(user_id=self.user["id"], agent_id=self.agent_id, title=title[:255])
        self.db.add(conv)
        self.db.commit()
        conv_id = conv.id
        self.db.add(Message(conversation_id=conv_id, role="assistant", content=f"回答里有 {SCRIPT} 和 ![x](https://evil.example/leak)"))
        self.db.commit()
        self.conv_ids.append(conv_id)
        return conv_id

    def test_export_headers_are_safe_for_hostile_titles(self):
        for title in TITLES:
            conv_id = self.conversation(title)
            for fmt in ("markdown", "json"):
                with self.subTest(title=title, fmt=fmt):
                    response = self.client.get(f"/conversation/{conv_id}/export?format={fmt}", headers=self.user["headers"])
                    self.assertEqual(response.status_code, 200, response.text[:200])
                    disposition = response.headers["content-disposition"]
                    self.assertTrue(disposition.startswith("attachment"))
                    for name, value in response.headers.items():
                        self.assertNotIn("\r", value)
                        self.assertNotIn("\n", value)
                        self.assertNotIn(name.lower(), ("set-cookie", "x-injected"))
                    self.assertIn("nosniff", response.headers.get("x-content-type-options", ""))
                    self.assertNotIn("text/html", response.headers["content-type"])
                    filename = disposition.split("filename", 1)[1]
                    self.assertNotIn("/", urllib.parse.unquote(filename))
                    self.assertNotIn("..", urllib.parse.unquote(filename).replace("....", ""))

    def test_export_is_a_download_not_a_page_even_when_the_content_has_scripts(self):
        conv_id = self.conversation("普通标题")
        response = self.client.get(f"/conversation/{conv_id}/export?format=markdown", headers=self.user["headers"])
        self.assertIn(SCRIPT, response.text)                         # 原样保留（这是用户自己的数据）
        self.assertTrue(response.headers["content-disposition"].startswith("attachment"))
        self.assertTrue(response.headers["content-type"].startswith("text/markdown"))


@unittest.skipUnless(_AVAILABLE, _WHY)
class AttachmentDownloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.owner = rc.create_user("xssb-own")
        cls.other = rc.create_user("xssb-oth")
        cls.saved = []

    @classmethod
    def tearDownClass(cls):
        import shutil
        from service import attachment_service
        for user_id in (cls.owner["id"], cls.other["id"]):
            shutil.rmtree(attachment_service._root() / f"u{user_id}", ignore_errors=True)
        rc.cleanup()

    def save(self, name, content):
        from service import attachment_service
        result = attachment_service.save(self.owner["id"], name, content, check_ext=False)
        return result["id"]

    def test_uploaded_html_and_svg_are_downloaded_never_rendered(self):
        for name, content in (("evil.html", b"<script>alert(1)</script>"), ("evil.svg", b"<svg onload=alert(1)>"), ("evil.xml", b"<x/>"), ("evil.htm", b"<script>1</script>"),
                              ("evil.xhtml", b"<script>1</script>"), ("evil.pdf", b"%PDF-1.4 <script>")):
            att_id = self.save(name, content)
            response = self.client.get(f"/attachment/{att_id}/download", headers=self.owner["headers"])
            with self.subTest(name=name):
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.headers["content-disposition"].startswith("attachment"), response.headers["content-disposition"])
                self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
                self.assertEqual(response.headers.get("x-frame-options"), "DENY")

    def test_chinese_attachment_names_download_fine(self):
        att_id = self.save("季度报告 2026.txt", "内容".encode("utf-8"))
        response = self.client.get(f"/attachment/{att_id}/download", headers=self.owner["headers"])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-disposition"].startswith("attachment"))
        self.assertEqual(response.content, "内容".encode("utf-8"))

    def test_other_users_cannot_download_it(self):
        att_id = self.save("secret.txt", b"secret")
        self.assertEqual(self.client.get(f"/attachment/{att_id}/download", headers=self.other["headers"]).status_code, 404)
        self.assertEqual(self.client.get(f"/attachment/{att_id}/download").status_code, 401)

    def test_id_traversal_and_junk_ids_find_nothing(self):
        for junk in ("../../etc/passwd", "..%2f..%2fetc%2fpasswd", "%2e%2e%2f", "0" * 24, "a" * 24, "x" * 500, "%00"):
            response = self.client.get(f"/attachment/{junk}/download", headers=self.owner["headers"])
            with self.subTest(junk=junk):
                self.assertIn(response.status_code, (404, 400, 422))


@unittest.skipUnless(_AVAILABLE, _WHY)
class NoHtmlEchoTest(unittest.TestCase):
    """任何 GET 接口都不能把用户输入当 text/html 回显（那样就是反射型 XSS）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.user = rc.create_user("xssb-echo")
        cls.spec = cls.client.get("/openapi.json").json()

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()

    def test_no_get_route_answers_with_html(self):
        checked = 0
        offenders = []
        for path, methods in self.spec["paths"].items():
            if "get" not in methods or path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect") or path.startswith("/admin/export"):
                continue
            operation = methods["get"]
            url, query = path, {}
            for parameter in operation.get("parameters", []):
                kind = parameter.get("schema", {}).get("type")
                if parameter["in"] == "path":
                    url = url.replace("{" + parameter["name"] + "}", "1" if kind == "integer" else urllib.parse.quote(SCRIPT, safe=""))
                elif parameter["in"] == "query":
                    query[parameter["name"]] = 1 if kind in ("integer", "number") else (False if kind == "boolean" else SCRIPT)
            if any(word in path for word in ("stream", "/chat", "metrics")):
                continue
            response = self.client.get(url, params=query, headers=self.user["headers"])
            checked += 1
            content_type = response.headers.get("content-type", "")
            if "html" in content_type.lower():
                offenders.append(f"{path} -> {content_type}")
            self.assertNotIn(SCRIPT, response.text if "json" not in content_type else "", path) if "html" in content_type else None
        self.assertGreater(checked, 100)
        self.assertEqual(offenders, [])

    def test_error_responses_are_json_even_for_hostile_input(self):
        for url in (f"/user/{urllib.parse.quote(SCRIPT)}", f"/agent/{urllib.parse.quote(SCRIPT)}", f"/nonexistent/{urllib.parse.quote(SCRIPT)}", "/%3Cscript%3E"):
            response = self.client.get(url, headers=self.user["headers"])
            with self.subTest(url=url):
                self.assertNotIn("html", response.headers.get("content-type", "").lower())
                self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")


class DispositionHelperTest(unittest.TestCase):
    def test_hostile_and_non_latin_names_produce_valid_headers(self):
        from utils.http_headers import attachment_disposition
        for name in TITLES + ["报告 2026年10月.md", "a\"b.txt", "line1\r\nline2.txt", "..", "...", "名字" * 100]:
            value = attachment_disposition(name)
            with self.subTest(name=name[:30]):
                value.encode("latin-1")                                       # 必须能作为 HTTP 头发出去
                self.assertTrue(value.startswith("attachment; filename=\""))
                self.assertNotRegex(value, r"[\r\n]")
                self.assertNotIn("\u202e", value)
                fallback = value.split('filename="', 1)[1].split('"', 1)[0]
                self.assertRegex(fallback, r"^[A-Za-z0-9._-]+$")
        encoded = attachment_disposition("报告.md").split("filename*=UTF-8''", 1)[1]
        self.assertEqual(urllib.parse.unquote(encoded), "报告.md")
        self.assertTrue(attachment_disposition("报告.md").split('filename="', 1)[1].startswith("download.md") or ".md" in attachment_disposition("报告.md"))


if __name__ == "__main__":
    unittest.main()
