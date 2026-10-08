"""内容安全策略（CSP）不能悄悄漂移：生产 Nginx 下发的、本地 `vite preview` 用的是同一份；
构建出来的页面里没有内联脚本（有的话严格的 script-src 会把页面自己弄坏）；API 响应有最严格的策略。"""
import pathlib
import re
import unittest

from tests import _route_client  # noqa: F401  先导入：里面过滤了第三方 TestClient 的升级提示

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _nginx_policy() -> str:
    match = re.search(r'add_header Content-Security-Policy "([^"]+)" always;', _read("deploy/nginx.conf"))
    assert match, "deploy/nginx.conf 里没有 Content-Security-Policy"
    return match.group(1)


def _vite_policy() -> str:
    match = re.search(r'const CSP = "([^"]+)"', _read("frontend/vite.config.ts"))
    assert match, "frontend/vite.config.ts 里没有 CSP"
    return match.group(1)


class CspPolicyTests(unittest.TestCase):
    def test_nginx_and_vite_preview_use_the_same_policy(self):
        self.assertEqual(_nginx_policy(), _vite_policy())

    def test_policy_has_the_important_restrictions(self):
        directives = {part.split(" ", 1)[0]: part for part in (p.strip() for p in _nginx_policy().split(";")) if part}
        self.assertEqual(directives["default-src"], "default-src 'self'")
        self.assertEqual(directives["script-src"], "script-src 'self'", "脚本只能来自本站：不允许 unsafe-inline / unsafe-eval / 外部域名")
        self.assertEqual(directives["object-src"], "object-src 'none'")
        self.assertEqual(directives["frame-ancestors"], "frame-ancestors 'none'")
        self.assertEqual(directives["base-uri"], "base-uri 'self'")
        self.assertEqual(directives["connect-src"], "connect-src 'self'", "页面只能向本站发请求，XSS 想把数据发到别处会被挡住")
        self.assertNotIn("unsafe-eval", _nginx_policy())

    def test_source_index_html_has_no_inline_script(self):
        html = _read("frontend/index.html")
        for match in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I):
            self.assertIn("src=", match.group(1), "index.html 里有内联脚本，会被 CSP 挡掉；请放到 public/ 下以外部文件加载")
            self.assertEqual(match.group(2).strip(), "")

    def test_api_responses_carry_a_locked_down_policy(self):
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from service.security_middleware import SecurityHeadersMiddleware

        async def ok(_request):
            return JSONResponse({"ok": True})
        app = Starlette(routes=[Route("/x", ok), Route("/docs", ok)])
        app.add_middleware(SecurityHeadersMiddleware)
        client = TestClient(app)
        self.assertIn("default-src 'none'", client.get("/x").headers["content-security-policy"])
        self.assertIn("frame-ancestors 'none'", client.get("/x").headers["content-security-policy"])
        self.assertNotIn("content-security-policy", client.get("/docs").headers, "在线文档页要从 CDN 加载脚本，不加这条限制")


if __name__ == "__main__":
    unittest.main()
