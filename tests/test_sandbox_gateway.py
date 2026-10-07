"""沙箱网关：只放行 GET /health、POST /run，只转发给固定上游，其余一律拒绝。

用一个假的"沙箱"HTTP 服务当上游，网关和上游都跑在本机随机端口上，不需要 Docker。
容器网络本身（沙箱到不了 api）在这里测不到，要靠 scripts/sandbox_acceptance.py 在部署后验证。
"""
import http.client
import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent


def _load_gateway():
    spec = importlib.util.spec_from_file_location("sandbox_gateway_under_test", ROOT / "sandbox" / "gateway.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gateway = _load_gateway()


class FakeSandbox(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {"ok": True})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        FakeSandbox.seen.append({"path": self.path, "token": self.headers.get("X-Sandbox-Token"), "body": body})
        if self.headers.get("X-Sandbox-Token") != "good":
            return self._send(401, {"detail": "invalid token"})
        self._send(200, {"echo": len(body)})


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class GatewayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = _serve(FakeSandbox)
        gateway.UPSTREAM = urlparse(f"http://127.0.0.1:{cls.upstream.server_port}")
        cls.gw = _serve(gateway.Handler)
        cls.port = cls.gw.server_port

    @classmethod
    def tearDownClass(cls):
        for server in (cls.gw, cls.upstream):
            server.shutdown()
            server.server_close()        # shutdown 只停止循环；不 server_close 监听 socket 会一直开到进程退出（ResourceWarning 的真正来源）

    def setUp(self):
        FakeSandbox.seen.clear()

    def _call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_health_is_forwarded(self):
        status, data = self._call("GET", "/health")
        self.assertEqual((status, json.loads(data)), (200, {"ok": True}))

    def test_run_is_forwarded_with_body_and_token(self):
        status, data = self._call("POST", "/run", b'{"x":1}', {"X-Sandbox-Token": "good", "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {"echo": 7})
        self.assertEqual(FakeSandbox.seen[0]["token"], "good")
        self.assertEqual(FakeSandbox.seen[0]["body"], b'{"x":1}')

    def test_upstream_rejection_is_passed_through(self):
        status, _ = self._call("POST", "/run", b"{}", {"X-Sandbox-Token": "bad"})
        self.assertEqual(status, 401)

    def test_everything_else_is_refused_and_never_reaches_the_upstream(self):
        for method, path in (("GET", "/run"), ("GET", "/"), ("GET", "/docs"), ("GET", "/openapi.json"),
                             ("POST", "/health"), ("POST", "/admin"), ("POST", "http://evil.example/run")):
            status, _ = self._call(method, path, b"{}" if method == "POST" else None)
            self.assertIn(status, (404, 405), f"{method} {path}")
        self.assertEqual(FakeSandbox.seen, [])

    def test_oversized_body_is_refused_before_reading_it(self):
        old = gateway.MAX_BODY
        gateway.MAX_BODY = 10
        try:
            status, _ = self._call("POST", "/run", b"x" * 100, {"X-Sandbox-Token": "good"})
        finally:
            gateway.MAX_BODY = old
        self.assertEqual(status, 413)
        self.assertEqual(FakeSandbox.seen, [])

    def test_unreachable_upstream_gives_502_not_a_hang(self):
        old = gateway.UPSTREAM
        gateway.UPSTREAM = urlparse("http://127.0.0.1:9")
        try:
            status, _ = self._call("GET", "/health")
        finally:
            gateway.UPSTREAM = old
        self.assertEqual(status, 502)

    def test_upstream_host_cannot_be_chosen_by_the_caller(self):
        # 请求里带 Host 头或绝对地址，都不能改变转发目标
        status, _ = self._call("GET", "/health", headers={"Host": "evil.example"})
        self.assertEqual(status, 200)
        self.assertEqual(gateway.UPSTREAM.hostname, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
