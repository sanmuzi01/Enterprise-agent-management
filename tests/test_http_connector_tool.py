"""build_http_connector_tool 的回归测试：把一条连接器配置变成可调用工具。

不发真实网络请求：mock 掉 validate_crawl_url（避免真实 DNS 解析）和
requests.request（避免真实 HTTP 调用）。
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from service.tools.http_connector_tool import build_http_connector_tool


def _fake_connector(**overrides):
    defaults = dict(
        id=1, name="query_order", description="查询订单状态",
        url="https://api.example.com/orders", method="GET",
        headers_encrypted=None,
        param_schema_json=json.dumps({
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        }),
        static_query_json=json.dumps({"region": "cn"}),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class BuildToolShapeTest(unittest.TestCase):
    def test_tool_name_and_description_come_from_connector(self):
        with patch("service.tools.http_connector_tool.validate_crawl_url", lambda u: u):
            tool = build_http_connector_tool(_fake_connector())
        self.assertEqual(tool.name, "query_order")
        self.assertEqual(tool.description, "查询订单状态")

    def test_zero_param_connector_gets_empty_args_schema(self):
        connector = _fake_connector(param_schema_json=json.dumps({"type": "object", "properties": {}}))
        with patch("service.tools.http_connector_tool.validate_crawl_url", lambda u: u):
            tool = build_http_connector_tool(connector)
        self.assertIsNotNone(tool.args_schema)


class ToolInvocationTest(unittest.TestCase):
    def _mock_response(self, status=200, content=b'{"status":"shipped"}', encoding="utf-8"):
        resp = MagicMock()
        resp.status_code = status
        resp.content = content
        resp.iter_content.side_effect = lambda **kwargs: iter([content[i:i + 16384] for i in range(0, len(content), 16384)] or [b""])   # 现在流式读取并限制大小
        resp.encoding = encoding
        resp.raise_for_status.return_value = None
        return resp

    def test_get_merges_static_query_and_llm_params(self):
        connector = _fake_connector()
        response = self._mock_response()
        with patch("service.tools.http_connector_tool.validate_crawl_url", lambda u: u), \
             patch("service.tools.http_connector_tool.requests.request", return_value=response) as mock_req:
            tool = build_http_connector_tool(connector)
            result = tool.func(order_id="ORD-1")

        self.assertIn("shipped", result)
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["params"], {"region": "cn", "order_id": "ORD-1"})
        self.assertIsNone(kwargs["json"])

    def test_post_sends_body_not_query_params(self):
        connector = _fake_connector(method="POST")
        response = self._mock_response()
        with patch("service.tools.http_connector_tool.validate_crawl_url", lambda u: u), \
             patch("service.tools.http_connector_tool.requests.request", return_value=response) as mock_req:
            tool = build_http_connector_tool(connector)
            tool.func(order_id="ORD-1")

        _, kwargs = mock_req.call_args
        self.assertIsNone(kwargs["params"])
        self.assertEqual(kwargs["json"], {"region": "cn", "order_id": "ORD-1"})

    def test_headers_are_decrypted_and_sent(self):
        from utils.crypto import encrypt
        connector = _fake_connector(headers_encrypted=encrypt(json.dumps({"Authorization": "Bearer tok"})))
        response = self._mock_response()
        with patch("service.tools.http_connector_tool.validate_crawl_url", lambda u: u), \
             patch("service.tools.http_connector_tool.requests.request", return_value=response) as mock_req:
            tool = build_http_connector_tool(connector)
            tool.func(order_id="ORD-1")

        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer tok"})

    def test_blocked_url_returns_error_string_not_exception(self):
        from service.tools.http_connector_tool import CrawlerError
        connector = _fake_connector()
        with patch("service.tools.http_connector_tool.validate_crawl_url",
                   side_effect=CrawlerError("不允许抓取内网")):
            tool = build_http_connector_tool(connector)
            result = tool.func(order_id="ORD-1")
        self.assertIn("不允许访问", result)

    def test_request_failure_returns_error_string_not_exception(self):
        connector = _fake_connector()
        with patch("service.tools.http_connector_tool.validate_crawl_url", lambda u: u), \
             patch("service.tools.http_connector_tool.requests.request", side_effect=RuntimeError("超时")):
            tool = build_http_connector_tool(connector)
            result = tool.func(order_id="ORD-1")
        self.assertIn("接口调用失败", result)

    def test_oversized_response_is_rejected_not_forwarded(self):
        connector = _fake_connector()
        huge = self._mock_response(content=b"x" * (300 * 1024))
        with patch("service.tools.http_connector_tool.validate_crawl_url", lambda u: u), \
             patch("service.tools.http_connector_tool.requests.request", return_value=huge), \
             patch("service.tools.http_connector_tool._max_bytes", return_value=1024):
            tool = build_http_connector_tool(connector)
            result = tool.func(order_id="ORD-1")
        self.assertIn("过大", result)

    def test_long_response_is_truncated(self):
        connector = _fake_connector()
        long_text = "A" * 10000
        response = self._mock_response(content=long_text.encode())
        with patch("service.tools.http_connector_tool.validate_crawl_url", lambda u: u), \
             patch("service.tools.http_connector_tool.requests.request", return_value=response), \
             patch("service.tools.http_connector_tool._max_response_chars", return_value=100):
            tool = build_http_connector_tool(connector)
            result = tool.func(order_id="ORD-1")
        self.assertIn("已截断", result)
        self.assertLess(len(result), 200)


class ToolExecutorLoadsConnectorsTest(unittest.TestCase):
    """ToolExecutor._load_api_connector_tools：不走完整 __init__（需要真实 LLM 配置），
    直接构造一个裸实例只测这一小段装配逻辑。"""

    def test_appends_built_tool_for_each_enabled_connector(self):
        from service.tools.executor import ToolExecutor

        executor = object.__new__(ToolExecutor)
        executor.lc_tools = []
        fake_connector = _fake_connector()
        fake_tool = object()

        with patch("models.agent_api_connector_dao.list_connectors_by_agent", return_value=[fake_connector]), \
             patch("service.tools.http_connector_tool.build_http_connector_tool", return_value=fake_tool):
            executor._load_api_connector_tools(db=MagicMock(), agent_id=1)

        self.assertEqual(executor.lc_tools, [fake_tool])

    def test_one_bad_connector_does_not_break_the_others(self):
        from service.tools.executor import ToolExecutor

        executor = object.__new__(ToolExecutor)
        executor.lc_tools = []
        good, bad = _fake_connector(id=1), _fake_connector(id=2)

        def _build(connector):
            if connector.id == 2:
                raise RuntimeError("坏配置")
            return "good-tool"

        with patch("models.agent_api_connector_dao.list_connectors_by_agent", return_value=[good, bad]), \
             patch("service.tools.http_connector_tool.build_http_connector_tool", side_effect=_build):
            executor._load_api_connector_tools(db=MagicMock(), agent_id=1)

        self.assertEqual(executor.lc_tools, ["good-tool"])


if __name__ == "__main__":
    unittest.main()
