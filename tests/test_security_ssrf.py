"""SSRF 专项：所有用户可控的出站 URL（爬虫、Webhook、企业接口连接器、组件 HTTP 数据源、LLM 接口地址）共用
web_crawler_service.validate_crawl_url。这里用攻击载荷矩阵检查校验本身，再检查每个出站点不会跟随重定向、
校验之后解析结果被固定（防 DNS 重绑定）、响应体有大小上限。"""
import os
import socket
import unittest
from unittest.mock import MagicMock, patch

from service import web_crawler_service as crawler
from service.web_crawler_service import CrawlerError, validate_crawl_url


def env(**values):
    base = {k: v for k, v in os.environ.items() if k not in ("CRAWLER_ALLOW_PRIVATE_NETWORK", "CRAWLER_ALLOW_PRIVATE_DNS")}
    base.update(values)
    return patch.dict(os.environ, base, clear=True)


PROD = {"APP_ENV": "production"}
DEV = {"APP_ENV": "development"}

# 字面 IP 与各种“看起来不像 IP”的写法：全部指向不该访问的地址
LITERAL_PAYLOADS = [
    "http://127.0.0.1/", "http://127.0.0.1:8011/admin", "http://localhost/", "http://LOCALHOST/", "http://localhost./", "http://foo.localhost/",
    "http://0.0.0.0/", "http://[::]/", "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://[::ffff:7f00:1]/", "http://[::ffff:10.0.0.1]/",
    "http://10.1.2.3/", "http://172.16.0.1/", "http://172.31.255.255/", "http://192.168.1.1/", "http://[fd00::1]/", "http://[fe80::1]/",
    "http://169.254.169.254/latest/meta-data/",                 # AWS / 大多数云的元数据
    "http://100.100.100.200/latest/meta-data/",                 # 阿里云元数据（共享地址空间 100.64.0.0/10，不属于 is_private）
    "http://metadata.google.internal/", "http://192.0.0.192/",  # GCP / Oracle 元数据
    "http://[64:ff9b::7f00:1]/",                                # NAT64 嵌入 127.0.0.1
    "http://2130706433/", "http://0x7f000001/", "http://0177.0.0.1/", "http://127.1/", "http://0x7f.1/", "http://017700000001/",
    "http://2852039166/",                                       # 169.254.169.254 的十进制写法
    "http://1.1.1.1.localhost/",
]
SCHEME_PAYLOADS = ["file:///etc/passwd", "gopher://127.0.0.1:6379/_INFO", "ftp://example.com/x", "javascript:alert(1)", "data:text/html,<script>1</script>",
                   "dict://127.0.0.1:11211/", "ldap://127.0.0.1/", "jar:http://example.com!/"]
PARSER_TRICKS = ["http://example.com@127.0.0.1/", "http://127.0.0.1#@example.com/", "http://user:pass@example.com/", "http://:@127.0.0.1/",
                 "http://example.com\\@127.0.0.1/", "http:///etc/passwd", "http://"]


class ValidatorMatrixTest(unittest.TestCase):
    def blocked(self, url):
        with self.assertRaises(CrawlerError, msg=url):
            validate_crawl_url(url)

    def test_literal_and_numeric_ip_forms_are_blocked_in_production(self):
        for url in LITERAL_PAYLOADS:
            with self.subTest(url=url), env(**PROD):
                self.blocked(url)

    def test_literal_and_numeric_ip_forms_are_blocked_in_development_too(self):
        """开发环境只放宽“本地代理把所有域名解析成 198.18.x.x”这一种情况，不能因此放过字面 IP 和数字写法。"""
        for url in LITERAL_PAYLOADS:
            with self.subTest(url=url), env(**DEV):
                self.blocked(url)

    def test_dangerous_schemes_and_parser_tricks_are_blocked(self):
        for url in SCHEME_PAYLOADS + PARSER_TRICKS:
            with self.subTest(url=url), env(**PROD):
                self.blocked(url)

    def test_names_that_resolve_to_internal_addresses_are_blocked(self):
        cases = {"rebind.example": ["127.0.0.1"], "meta.example": ["169.254.169.254"], "aliyun.example": ["100.100.100.200"],
                 "mixed.example": ["93.184.216.34", "10.0.0.5"],        # 只要有一个内网地址就拒绝（不能只看第一个）
                 "v6.example": ["::1"], "mapped.example": ["::ffff:127.0.0.1"], "private.example": ["10.0.0.5"]}
        for name, addresses in cases.items():
            for label, settings in (("production", PROD), ("development", DEV)):
                with self.subTest(name=name, env=label), env(**settings), patch.object(crawler, "_resolve_host", return_value=addresses):
                    self.blocked(f"http://{name}/")

    def test_development_tolerates_only_fake_ip_proxy_ranges(self):
        with env(**DEV), patch.object(crawler, "_resolve_host", return_value=["198.18.0.7"]):
            self.assertEqual(validate_crawl_url("http://news.example/a"), "http://news.example/a")
        with env(**PROD), patch.object(crawler, "_resolve_host", return_value=["198.18.0.7"]), self.assertRaises(CrawlerError):
            validate_crawl_url("http://news.example/a")

    def test_public_addresses_still_work(self):
        for url, addresses in (("https://example.com/a?b=1", ["93.184.216.34"]), ("http://8.8.8.8/", None), ("http://[2606:4700:4700::1111]/", None)):
            with self.subTest(url=url), env(**PROD):
                if addresses:
                    with patch.object(crawler, "_resolve_host", return_value=addresses):
                        self.assertEqual(validate_crawl_url(url), url)
                else:
                    self.assertEqual(validate_crawl_url(url), url)

    def test_explicit_private_network_switch_still_works_for_local_debugging(self):
        with env(**PROD, CRAWLER_ALLOW_PRIVATE_NETWORK="1"):
            self.assertEqual(validate_crawl_url("http://127.0.0.1:9000/x"), "http://127.0.0.1:9000/x")


class DnsRebindingTest(unittest.TestCase):
    """校验时解析一次、真正建连接时又解析一次：攻击者可以让两次结果不同。校验通过的地址必须被固定下来。"""

    def test_connect_time_resolution_is_pinned_to_the_validated_addresses(self):
        answers = iter([[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],       # 校验时：公网
                        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]] * 5)       # 之后：换成回环
        with env(**PROD), patch.object(crawler, "_orig_getaddrinfo", side_effect=lambda *a, **k: next(answers)):
            validate_crawl_url("http://rebind.example/a")
            connect_time = socket.getaddrinfo("rebind.example", 80, type=socket.SOCK_STREAM)
        self.assertEqual({item[4][0] for item in connect_time}, {"93.184.216.34"})
        self.assertEqual(connect_time[0][4][1], 80)

    def test_pin_is_per_host_and_expires(self):
        with env(**PROD), patch.object(crawler, "_resolve_host", return_value=["93.184.216.34"]):
            validate_crawl_url("http://pinned.example/")
        self.assertIn("pinned.example", crawler._pins)
        crawler._pins["pinned.example"] = (0.0, ["93.184.216.34"])        # 过期
        with patch.object(crawler, "_orig_getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 80))]):
            self.assertEqual(socket.getaddrinfo("pinned.example", 80)[0][4][0], "1.2.3.4")
        crawler._pins.pop("pinned.example", None)

    def test_ip_literals_and_unrelated_hosts_are_not_affected(self):
        with patch.object(crawler, "_orig_getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("9.9.9.9", 443))]) as orig:
            socket.getaddrinfo("other.example", 443)
            orig.assert_called_once()


class RedirectAndSizeTest(unittest.TestCase):
    def test_crawler_revalidates_every_redirect_hop(self):
        first = MagicMock(is_redirect=True, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
        session = MagicMock()
        session.get.return_value = first
        with env(**PROD), patch.object(crawler, "_resolve_host", return_value=["93.184.216.34"]), patch("requests.Session", return_value=session):
            with self.assertRaises(CrawlerError):
                crawler._download("http://example.com/start")
        self.assertEqual(session.get.call_count, 1)                    # 第二跳被拦下，没有真的去请求内网地址

    def test_connector_tool_never_follows_redirects(self):
        from types import SimpleNamespace
        from service.tools import http_connector_tool as tool_module
        response = MagicMock(status_code=302, headers={"Location": "http://127.0.0.1/"})
        connector = SimpleNamespace(id=1, name="c", description="d", url="https://api.example.com/x", method="GET", headers_encrypted=None,
                                    param_schema_json='{"type": "object", "properties": {}}', static_query_json="{}")
        with patch.object(tool_module, "validate_crawl_url", lambda u: u), patch.object(tool_module.requests, "request", return_value=response) as sent:
            result = tool_module.build_http_connector_tool(connector).func()
        self.assertIs(sent.call_args.kwargs["allow_redirects"], False)
        self.assertIn("重定向", result)

    def test_connector_tool_stops_reading_an_oversized_response(self):
        from types import SimpleNamespace
        from service.tools import http_connector_tool as tool_module
        chunks_read = []

        def endless(**_kwargs):
            for _ in range(10_000):
                chunks_read.append(1)
                yield b"x" * 16384
        response = MagicMock(status_code=200, encoding="utf-8")
        response.raise_for_status.return_value = None
        response.iter_content.side_effect = endless
        connector = SimpleNamespace(id=1, name="c", description="d", url="https://api.example.com/x", method="GET", headers_encrypted=None,
                                    param_schema_json='{"type": "object", "properties": {}}', static_query_json="{}")
        with patch.object(tool_module, "validate_crawl_url", lambda u: u), patch.object(tool_module.requests, "request", return_value=response), \
                patch.dict(os.environ, {"AGENT_API_CONNECTOR_MAX_BYTES": str(64 * 1024)}):
            result = tool_module.build_http_connector_tool(connector).func()
        self.assertIn("过大", result)
        self.assertLess(len(chunks_read), 10)             # 读到上限就停，没有把无限的响应读完

    def test_every_requests_call_to_a_user_supplied_url_disables_redirects(self):
        """静态守卫：这些文件里直接调用 requests 的地方必须显式 allow_redirects=False（requests 默认会跟随 3xx，
        公网地址 302 到 169.254.169.254 就绕过了出站前的 SSRF 校验）。新增调用没写会让这个测试失败。"""
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent
        files = ["service/tools/http_connector_tool.py", "service/llm/glm_client.py", "service/llm/openai_compatible_client.py",
                 "service/skills_core/github_import.py", "service/web_crawler_service.py"]
        call = re.compile(r"requests\.(?:request|get|post|put|delete|patch)\(")
        for name in files:
            source = (root / name).read_text(encoding="utf-8")
            for match in call.finditer(source):
                depth, i = 0, match.end() - 1
                while i < len(source):
                    depth += source[i] == "("
                    depth -= source[i] == ")"
                    if depth == 0:
                        break
                    i += 1
                with self.subTest(file=name, at=source[:match.start()].count("\n") + 1):
                    self.assertIn("allow_redirects=False", source[match.start():i + 1])


class SurfaceTest(unittest.TestCase):
    """每个出站点都真的接了校验（不是只在文档里写了）。"""

    def test_webhook_urls_reject_ssrf_payloads(self):
        import asyncio
        from service import notification_service
        from service.exceptions import InvalidInput
        for url in ("http://127.0.0.1:8011/hook", "http://169.254.169.254/", "http://100.100.100.200/", "http://2130706433/", "file:///etc/passwd"):
            with self.subTest(url=url), env(**PROD):
                with self.assertRaises(InvalidInput):
                    asyncio.run(notification_service._validate_webhook_url(url))

    def test_widget_http_source_rejects_ssrf_payloads(self):
        import asyncio
        from service.exceptions import InvalidInput
        from service.widgets.connectors.http_api import HttpConnector
        connector = HttpConnector()
        ctx = MagicMock()
        for url in ("http://127.0.0.1/", "http://169.254.169.254/latest", "http://[::1]/"):
            with self.subTest(url=url), env(**PROD):
                with self.assertRaises(InvalidInput):
                    asyncio.run(connector.fetch(ctx, {"url": url}))

    def test_llm_clients_reject_internal_api_urls(self):
        from service.llm.openai_compatible_client import OpenAICompatibleClient
        for url in ("http://127.0.0.1:11434/v1", "http://169.254.169.254/v1", "http://100.100.100.200/v1"):
            with self.subTest(url=url), env(**PROD):
                with self.assertRaises(Exception):
                    OpenAICompatibleClient("k", url, "gpt-4o")


class GithubImportTest(unittest.TestCase):
    """GitHub 技能导入：主机固定为 github.com / codeload.github.com，只允许同主机的重定向。"""

    def test_only_github_dot_com_urls_parse(self):
        from service.skills_core.github_import import SkillImportError, parse_github_url
        for url in ("https://evil.com/anthropics/skills", "https://github.com.evil.com/a/b", "https://evil.com/github.com/a/b", "http://127.0.0.1/a/b",
                    "https://github.com@evil.com/a/b", "file:///etc/passwd", "https://github.com/a/b/tree/main/../../etc", "https://github.com/a"):
            with self.subTest(url=url), self.assertRaises(SkillImportError):
                parse_github_url(url)
        self.assertEqual(parse_github_url("https://github.com/anthropics/skills/tree/main/skills/pdf"), ("anthropics", "skills", "main", "skills/pdf"))

    def test_redirect_to_another_host_is_refused(self):
        from service.skills_core import github_import
        from service.skills_core.github_import import SkillImportError
        redirect = MagicMock(status_code=302, headers={"Location": "http://169.254.169.254/zip"})
        with patch.object(github_import.requests, "get", return_value=redirect) as get:
            with self.assertRaises(SkillImportError):
                github_import.download_repo_zip("a", "b", "main")
        self.assertEqual(get.call_count, 1)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
