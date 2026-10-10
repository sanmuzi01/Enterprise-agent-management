"""外部 Agent 接入：协议、签名、地址安全、限制，以及管理配置和聊天运行时的端到端。

用一个本机的假 Agent 服务（线程里的 http.server）模拟企业自己部署的 Agent。
"""
import json
import os
import threading
import time
import pathlib
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from sqlalchemy import text

from models.init_db import SessionLocal
from service.exceptions import InvalidInput, NotFound
from service.runtime import external_agent as ea
from tests import _route_client as rc
from tests._async_helpers import run_async as _run

ROOT = pathlib.Path(__file__).resolve().parent.parent
SECRET_FOR_SERVER = {"value": ""}


class FakeAgent(BaseHTTPRequestHandler):
    """假的外部 Agent：按路径返回不同的行为，并且真的校验平台的签名。"""
    received = []

    def log_message(self, *args):
        pass

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self):
        body = self._body()
        signature_ok = ea.verify_signature(
            SECRET_FOR_SERVER["value"], self.headers.get(ea.TIMESTAMP_HEADER, ""), body, self.headers.get(ea.SIGNATURE_HEADER, ""))
        payload = json.loads(body or b"{}")
        FakeAgent.received.append({"path": self.path, "payload": payload, "signature_ok": signature_ok, "headers": dict(self.headers)})
        if not signature_ok and not self.path.startswith("/nosig"):
            return self._send(401, {"error": "bad signature"})
        route = self.path.split("?")[0]
        if route in ("/json", "/nosig"):
            return self._send(200, {"answer": f"收到：{payload.get('message')}", "steps": [{"title": "查询", "detail": "查了库存"}],
                                    "citations": [{"title": "手册", "page": 3}], "usage": {"total_tokens": 42}})
        if route == "/sse":
            return self._sse([
                {"type": "step", "title": "思考", "detail": "先查一下"},
                {"type": "delta", "text": "你"}, {"type": "delta", "text": "好"},
                {"type": "citations", "items": [{"title": "手册"}]},
                {"type": "done", "usage": {"total_tokens": 7}},
            ])
        if route == "/sse-error":
            return self._sse([{"type": "delta", "text": "半截"}, {"type": "error", "message": "对方内部出错"}])
        if route == "/big":
            return self._send(200, {"answer": "x" * 2_000_000})
        if route == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data")
            self.end_headers()
            return
        if route == "/boom":
            return self._send(500, {"error": "internal secret details"})
        if route == "/notjson":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"hello")
            return
        if route == "/noanswer":
            return self._send(200, {"result": "oops"})
        if route == "/empty":
            return self._send(200, {"answer": ""})
        if route == "/slow":
            time.sleep(3)
            return self._send(200, {"answer": "late"})
        self._send(404, {"error": "no route"})

    def _send(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _sse(self, events):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event in events:
            self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):     # 客户端主动断开（超时测试）不是错误
        pass


class ServerCase(unittest.TestCase):
    secret = "test-secret-123"

    @classmethod
    def setUpClass(cls):
        cls.server = QuietServer(("127.0.0.1", 0), FakeAgent)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.env = mock.patch.dict(os.environ, {"EXTERNAL_AGENT_ALLOWED_HOSTS": f"127.0.0.1:{cls.port}", "APP_ENV": "development"})
        cls.env.start()
        SECRET_FOR_SERVER["value"] = cls.secret

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeAgent.received.clear()
        SECRET_FOR_SERVER["value"] = self.secret

    def cfg(self, path, secret=None, timeout=5, headers=None):
        return ea.EndpointConfig(self.base + path, secret or self.secret, headers, timeout)

    def payload(self, message="你好"):
        return ea.build_payload(kind="chat", agent={"id": 1, "name": "a"}, user={"id": 7, "name": "u", "departments": []},
                                message=message, history=[{"role": "user", "content": "之前"}], run_id=3, conversation_id=4)


class UrlValidationTests(unittest.TestCase):
    def test_bad_schemes_and_credentials_are_rejected(self):
        for url in ("ftp://example.com/x", "file:///etc/passwd", "javascript:alert(1)", "http://user:pw@example.com/", "http:///x"):
            with self.assertRaises(ea.ExternalAgentError, msg=url):
                ea.validate_endpoint_url(url)

    def test_private_and_loopback_addresses_are_blocked_unless_allow_listed(self):
        with mock.patch.dict(os.environ, {"EXTERNAL_AGENT_ALLOWED_HOSTS": "", "CRAWLER_ALLOW_PRIVATE_NETWORK": "0"}):
            for url in ("http://127.0.0.1:9000/a", "http://localhost/a", "http://10.1.2.3/a", "http://192.168.1.5/a", "http://[::1]/a"):
                with self.assertRaises(ea.ExternalAgentError, msg=url):
                    ea.validate_endpoint_url(url)

    def test_allow_list_opens_exactly_the_listed_internal_host(self):
        with mock.patch.dict(os.environ, {"EXTERNAL_AGENT_ALLOWED_HOSTS": "10.1.2.3:9000, agent.corp.local", "APP_ENV": "development"}):
            self.assertEqual(ea.validate_endpoint_url("http://10.1.2.3:9000/chat"), "http://10.1.2.3:9000/chat")
            with self.assertRaises(ea.ExternalAgentError):
                ea.validate_endpoint_url("http://10.1.2.3:9001/chat")           # 端口不同不放行
            with self.assertRaises(ea.ExternalAgentError):
                ea.validate_endpoint_url("http://10.1.2.4:9000/chat")           # 其他内网主机不放行

    def test_cloud_metadata_is_blocked_even_when_listed(self):
        with mock.patch.dict(os.environ, {"EXTERNAL_AGENT_ALLOWED_HOSTS": "169.254.169.254"}):
            with self.assertRaises(ea.ExternalAgentError):
                ea.validate_endpoint_url("http://169.254.169.254/latest/meta-data")

    def test_production_requires_https_for_unlisted_hosts(self):
        with mock.patch.dict(os.environ, {"APP_ENV": "production", "EXTERNAL_AGENT_ALLOWED_HOSTS": "agent.corp.local"}):
            with mock.patch.object(ea, "_resolve", return_value=[]), \
                    mock.patch("service.web_crawler_service.validate_crawl_url", return_value="ok"):
                with self.assertRaises(ea.ExternalAgentError):
                    ea.validate_endpoint_url("http://public.example.com/a")
                self.assertTrue(ea.validate_endpoint_url("http://agent.corp.local/a"))      # 白名单内网主机可以用 http


class SignatureTests(unittest.TestCase):
    def test_signature_round_trip_and_tamper_detection(self):
        body, ts = b'{"a":1}', str(int(time.time()))
        signature = ea.sign("k", ts, body)
        self.assertTrue(ea.verify_signature("k", ts, body, signature))
        self.assertFalse(ea.verify_signature("k", ts, b'{"a":2}', signature), "改了内容必须验不过")
        self.assertFalse(ea.verify_signature("other", ts, body, signature), "密钥不对必须验不过")
        self.assertFalse(ea.verify_signature("k", ts, body, ""))
        self.assertFalse(ea.verify_signature("k", "abc", body, signature))

    def test_old_timestamps_are_rejected_to_stop_replays(self):
        body, ts = b"{}", "1000000000"
        self.assertFalse(ea.verify_signature("k", ts, body, ea.sign("k", ts, body)))

    def test_history_is_clipped_and_only_user_and_assistant_roles_pass(self):
        history = [{"role": "system", "content": "secret prompt"}] + [{"role": "user", "content": "x" * 9000}] * 30
        clipped = ea.clip_history(history)
        self.assertEqual(len(clipped), ea.MAX_HISTORY_MESSAGES)
        self.assertTrue(all(h["role"] == "user" and len(h["content"]) == ea.MAX_HISTORY_CHARS for h in clipped))


class TransportTests(ServerCase):
    def test_json_reply_is_normalised_and_the_request_is_signed(self):
        result = ea.call(self.cfg("/json"), self.payload())
        self.assertEqual(result["answer"], "收到：你好")
        self.assertEqual(result["usage"], {"total_tokens": 42})
        self.assertEqual(result["steps"], [{"title": "查询", "detail": "查了库存"}])
        sent = FakeAgent.received[0]
        self.assertTrue(sent["signature_ok"])
        self.assertEqual(sent["payload"]["protocol"], ea.PROTOCOL)
        self.assertEqual(sent["payload"]["user"]["id"], 7)
        self.assertEqual(sent["payload"]["history"], [{"role": "user", "content": "之前"}])

    def test_custom_headers_are_sent_but_cannot_override_signature_headers(self):
        ea.call(self.cfg("/json", headers={"Authorization": "Bearer abc", ea.SIGNATURE_HEADER: "forged"}), self.payload())
        headers = {k.lower(): v for k, v in FakeAgent.received[0]["headers"].items()}
        self.assertEqual(headers["authorization"], "Bearer abc")
        self.assertTrue(FakeAgent.received[0]["signature_ok"], "附加请求头不能覆盖平台的签名")

    def test_wrong_secret_is_rejected_by_the_other_side(self):
        with self.assertRaises(ea.ExternalAgentError) as ctx:
            ea.call(self.cfg("/json", secret="wrong"), self.payload())
        self.assertIn("401", str(ctx.exception))

    def test_event_stream_is_parsed_into_deltas_steps_and_result(self):
        events = list(ea.stream(self.cfg("/sse"), self.payload()))
        self.assertEqual([e["text"] for e in events if e["type"] == "delta"], ["你", "好"])
        self.assertEqual([e["title"] for e in events if e["type"] == "step"], ["思考"])
        self.assertEqual(events[-1]["type"], "result")
        self.assertEqual(events[-1]["data"]["answer"], "你好")
        self.assertEqual(events[-1]["data"]["usage"], {"total_tokens": 7})

    def test_event_stream_error_event_becomes_a_clean_error(self):
        with self.assertRaises(ea.ExternalAgentError) as ctx:
            list(ea.stream(self.cfg("/sse-error"), self.payload()))
        self.assertIn("对方内部出错", str(ctx.exception))

    def test_redirects_are_not_followed(self):
        with self.assertRaises(ea.ExternalAgentError) as ctx:
            ea.call(self.cfg("/redirect"), self.payload())
        self.assertIn("重定向", str(ctx.exception))

    def test_oversized_replies_are_cut_off(self):
        with mock.patch.dict(os.environ, {"EXTERNAL_AGENT_MAX_RESPONSE_BYTES": "10000"}):
            with self.assertRaises(ea.ExternalAgentError) as ctx:
                ea.call(self.cfg("/big"), self.payload())
        self.assertIn("过大", str(ctx.exception))

    def test_server_errors_do_not_leak_the_other_sides_details(self):
        with self.assertRaises(ea.ExternalAgentError) as ctx:
            ea.call(self.cfg("/boom"), self.payload())
        self.assertNotIn("internal secret details", str(ctx.exception))
        self.assertIn("500", str(ctx.exception))

    def test_malformed_replies_are_rejected(self):
        for path in ("/notjson", "/noanswer"):
            with self.assertRaises(ea.ExternalAgentError, msg=path):
                ea.call(self.cfg(path), self.payload())

    def test_timeout(self):
        started = time.monotonic()
        with self.assertRaises(ea.ExternalAgentError) as ctx:
            ea.call(self.cfg("/slow", timeout=1), self.payload())
        self.assertIn("超时", str(ctx.exception))
        self.assertLess(time.monotonic() - started, 3)

    def test_answer_length_is_capped(self):
        with mock.patch.dict(os.environ, {"EXTERNAL_AGENT_MAX_ANSWER_CHARS": "5"}):
            self.assertEqual(ea.call(self.cfg("/json"), self.payload())["answer"], "收到：你好"[:5])

    def test_unreachable_server_gives_a_friendly_message(self):
        with mock.patch.dict(os.environ, {"EXTERNAL_AGENT_ALLOWED_HOSTS": "127.0.0.1:1"}):
            with self.assertRaises(ea.ExternalAgentError) as ctx:
                ea.call(ea.EndpointConfig("http://127.0.0.1:1/x", "k", None, 2), self.payload())
        self.assertIn("无法连接", str(ctx.exception))


class ReferenceExampleTests(unittest.TestCase):
    """docs/external-agent-protocol.md 里的示例服务（examples/external_agent_example.py）必须真的能和平台对上。"""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("external_agent_example", ROOT / "examples" / "external_agent_example.py")
        self.example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.example)
        self.server = self.example.make_server("example-secret", port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        patcher = mock.patch.dict(os.environ, {"EXTERNAL_AGENT_ALLOWED_HOSTS": f"127.0.0.1:{self.port}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _payload(self, kind="chat"):
        return ea.build_payload(kind=kind, agent={"id": 1, "name": "a"},
                                user={"id": 7, "name": "张三", "departments": [{"id": 3, "name": "销售一部"}]},
                                message="报价多少", history=[], run_id=None, conversation_id=None)

    def test_example_answers_a_signed_request(self):
        cfg = ea.EndpointConfig(f"http://127.0.0.1:{self.port}/chat", "example-secret", None, 5)
        result = ea.call(cfg, self._payload())
        self.assertIn("报价多少", result["answer"])
        self.assertIn("销售一部", result["answer"])

    def test_example_answers_ping(self):
        cfg = ea.EndpointConfig(f"http://127.0.0.1:{self.port}/chat", "example-secret", None, 5)
        self.assertEqual(ea.call(cfg, self._payload("ping"))["answer"], "pong")

    def test_example_rejects_a_wrong_secret(self):
        cfg = ea.EndpointConfig(f"http://127.0.0.1:{self.port}/chat", "not-the-secret", None, 5)
        with self.assertRaises(ea.ExternalAgentError) as ctx:
            ea.call(cfg, self._payload())
        self.assertIn("401", str(ctx.exception))

    def test_example_verify_agrees_with_the_platform_signer(self):
        body, ts = b'{"x":1}', str(int(time.time()))
        self.assertTrue(self.example.verify("k", ts, body, ea.sign("k", ts, body)))
        self.assertFalse(self.example.verify("k", "1000000000", body, ea.sign("k", "1000000000", body)))


class EgressTests(unittest.TestCase):
    def test_confidential_and_restricted_content_is_never_sent_to_external_agents(self):
        """文档承诺：机密、绝密空间的内容不会发给外部服务（对外部服务按“外部模型”处理）。"""
        from service import data_egress_policy
        from service.runtime.external_runtime import EGRESS_MODEL_NAME
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONFIDENTIAL_ALLOWED_MODELS", None)
            self.assertTrue(data_egress_policy.is_model_allowed(EGRESS_MODEL_NAME, "internal"))
            self.assertFalse(data_egress_policy.is_model_allowed(EGRESS_MODEL_NAME, "confidential"))
            self.assertFalse(data_egress_policy.is_model_allowed(EGRESS_MODEL_NAME, "restricted"))


_AVAILABLE, _WHY = rc.route_tests_available()


def _run_db(fn):
    from models.async_db import AsyncSessionLocal

    async def _wrapper():
        async with AsyncSessionLocal() as db:
            return await fn(db)

    return _run(_wrapper())


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class EndToEndTests(ServerCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sdb = SessionLocal()
        cls.admin = rc.create_user("extagent-admin")
        cls.asker = rc.create_user("extagent-asker")

    @classmethod
    def tearDownClass(cls):
        cls.sdb.execute(text("DELETE FROM agent_step WHERE run_id IN (SELECT id FROM agent_run WHERE agent_id IN"
                             " (SELECT id FROM agent WHERE name LIKE 'ext-test-%'))"))
        cls.sdb.execute(text("DELETE FROM agent_run WHERE agent_id IN (SELECT id FROM agent WHERE name LIKE 'ext-test-%')"))
        cls.sdb.execute(text("DELETE FROM agent_external_endpoint WHERE agent_id IN (SELECT id FROM agent WHERE name LIKE 'ext-test-%')"))
        cls.sdb.execute(text("DELETE FROM agent WHERE name LIKE 'ext-test-%'"))
        cls.sdb.commit()
        cls.sdb.close()
        rc.cleanup()
        super().tearDownClass()

    @staticmethod
    def _query(sql, params=None):
        with SessionLocal() as db:
            return db.execute(text(sql), params or {})

    def _scalar(self, sql, params=None):
        with SessionLocal() as db:
            return db.execute(text(sql), params or {}).scalar()

    def _first(self, sql, params=None):
        with SessionLocal() as db:
            return db.execute(text(sql), params or {}).first()

    def _make_agent(self, name):
        import service.agent_admin_service as svc
        return _run_db(lambda db: svc.create_managed_agent(db, self.admin["id"], name, "central"))["id"]

    def _configure(self, agent_id, path="/json", **kwargs):
        import service.external_agent_admin_service as ext
        info = _run_db(lambda db: ext.configure_runtime(db, agent_id, self.admin["id"], "external", url=self.base + path, **kwargs))
        if info.get("secret"):
            SECRET_FOR_SERVER["value"] = info["secret"]
        return info

    def test_configuration_returns_the_secret_once_and_never_again(self):
        import service.external_agent_admin_service as ext
        agent_id = self._make_agent("ext-test-config")
        first = self._configure(agent_id)
        self.assertGreater(len(first["secret"]), 30)
        self.assertEqual(first["runtime_type"], "external")
        again = _run_db(lambda db: ext.get_runtime(db, agent_id))
        self.assertNotIn("secret", again)
        self.assertNotIn("secret", json.dumps(again))
        row = self._scalar("SELECT secret_encrypted FROM agent_external_endpoint WHERE agent_id=:i", {"i": agent_id})
        self.assertNotIn(first["secret"], row, "库里只能是密文")
        rotated = self._configure(agent_id, rotate_secret=True)
        self.assertNotEqual(rotated["secret"], first["secret"])

    def test_headers_are_stored_encrypted_and_only_names_are_returned(self):
        agent_id = self._make_agent("ext-test-headers")
        info = self._configure(agent_id, headers={"Authorization": "Bearer top-secret-token"})
        self.assertEqual(info["endpoint"]["header_names"], ["Authorization"])
        self.assertNotIn("top-secret-token", json.dumps(info))
        raw = self._scalar("SELECT headers_encrypted FROM agent_external_endpoint WHERE agent_id=:i", {"i": agent_id})
        self.assertNotIn("top-secret-token", raw)

    def test_bad_configuration_is_rejected(self):
        import service.external_agent_admin_service as ext
        agent_id = self._make_agent("ext-test-bad")
        with mock.patch.dict(os.environ, {"EXTERNAL_AGENT_ALLOWED_HOSTS": ""}):
            with self.assertRaises(InvalidInput):
                self._configure(agent_id)                                       # 本机地址没进白名单
        with self.assertRaises(InvalidInput):
            _run_db(lambda db: ext.configure_runtime(db, agent_id, self.admin["id"], "external", url=""))
        with self.assertRaises(InvalidInput):
            self._configure(agent_id, headers={"Bad Header": "x"})
        with self.assertRaises(InvalidInput):
            self._configure(agent_id, headers={"X-A": "line\nbreak"})
        with self.assertRaises(InvalidInput):
            self._configure(agent_id, timeout_seconds=100000)
        with self.assertRaises(InvalidInput):
            _run_db(lambda db: ext.configure_runtime(db, agent_id, self.admin["id"], "plugin"))

    def test_personal_agents_cannot_be_external(self):
        import service.external_agent_admin_service as ext
        with SessionLocal() as db:
            db.execute(text("INSERT INTO agent (user_id, name, model_name, rag_enabled, memory_enabled, temperature, kb_top_k, kb_rerank_enabled,"
                            " kb_force_citation, kb_refuse_when_empty, scope_type, sensitivity, row_version, lifecycle_status, agent_type, runtime_type)"
                            " VALUES (:u,'ext-test-personal','glm-4',0,1,70,5,0,1,1,'personal','internal',0,'draft','personal','builtin')"),
                       {"u": self.asker["id"]})
            db.commit()
            personal_id = db.execute(text("SELECT id FROM agent WHERE name='ext-test-personal'")).scalar()
        with self.assertRaises(NotFound):
            _run_db(lambda db: ext.configure_runtime(db, personal_id, self.admin["id"], "external", url=self.base + "/json"))

    def test_connection_test_records_the_result(self):
        import service.external_agent_admin_service as ext
        agent_id = self._make_agent("ext-test-ping")
        self._configure(agent_id)
        result = _run_db(lambda db: ext.test_runtime(db, agent_id, self.admin["id"]))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["endpoint"]["last_test_ok"])
        ping = [r for r in FakeAgent.received if r["payload"].get("type") == "ping"][0]
        self.assertEqual(ping["payload"]["history"], [])
        self.assertEqual(ping["payload"]["user"]["departments"], [])
        self._configure(agent_id, path="/boom")
        failed = _run_db(lambda db: ext.test_runtime(db, agent_id, self.admin["id"]))
        self.assertFalse(failed["ok"])
        self.assertIn("500", failed["message"])

    def test_an_unconfigured_external_agent_cannot_be_published(self):
        import service.agent_admin_service as svc
        import service.external_agent_admin_service as ext
        agent_id = self._make_agent("ext-test-publish")
        _run_db(lambda db: self._to_external_without_endpoint(db, agent_id))
        with self.assertRaises(InvalidInput):
            _run_db(lambda db: svc.update_managed_agent(db, agent_id, self.admin["id"], lifecycle_status="published"))
        self._configure(agent_id)
        published = _run_db(lambda db: svc.update_managed_agent(db, agent_id, self.admin["id"], lifecycle_status="published"))
        self.assertEqual(published["lifecycle_status"], "published")
        self.assertEqual(published["runtime_type"], "external")
        del ext

    @staticmethod
    async def _to_external_without_endpoint(db, agent_id):
        await db.execute(text("UPDATE agent SET runtime_type='external' WHERE id=:i"), {"i": agent_id})
        await db.commit()

    def test_chat_runs_through_the_external_service_and_leaves_a_run_record(self):
        from service.runtime.agent_runtime import run_with_history_async
        agent_id = self._make_agent("ext-test-chat")
        self._configure(agent_id)
        result = _run_db(lambda db: run_with_history_async(db, self.admin["id"], agent_id, "库存还有多少", history=[
            {"role": "user", "content": "你好"}, {"role": "assistant", "content": "您好"}], conversation_id=None))
        self.assertEqual(result["answer"], "收到：库存还有多少")
        self.assertEqual(result["usage"], {"total_tokens": 42})
        chat = [r for r in FakeAgent.received if r["payload"].get("type") == "chat"][-1]
        self.assertTrue(chat["signature_ok"])
        self.assertEqual(chat["payload"]["user"]["id"], self.admin["id"])
        self.assertNotIn("knowledge", chat["payload"], "没开启 send_knowledge 就不能发资料")
        run = self._first("SELECT status, final_answer, total_tokens FROM agent_run WHERE id=:i", {"i": result["run_id"]})
        self.assertEqual((run[0], run[1], run[2]), ("finished", "收到：库存还有多少", 42))

    def test_streaming_chat_emits_deltas_and_finishes(self):
        from service.runtime.agent_runtime import run_stream_with_history_async
        agent_id = self._make_agent("ext-test-stream")
        self._configure(agent_id, path="/sse")

        async def collect(db):
            return [chunk async for chunk in run_stream_with_history_async(db, self.admin["id"], agent_id, "你好", history=[])]
        chunks = _run_db(collect)
        text_all = "".join(chunks)
        self.assertIn("event: ready", text_all)
        self.assertEqual(text_all.count("event: answer_delta"), 2)
        self.assertIn("event: answer\n", text_all)
        self.assertTrue(chunks[-1].startswith("event: done"), chunks[-1])

    def test_failures_become_failed_runs_with_a_friendly_message(self):
        from service.runtime.agent_runtime import run_with_history_async
        agent_id = self._make_agent("ext-test-fail")
        self._configure(agent_id, path="/boom")
        with self.assertRaises(ValueError) as ctx:
            _run_db(lambda db: run_with_history_async(db, self.admin["id"], agent_id, "你好", history=[], conversation_id=None))
        self.assertNotIn("internal secret details", str(ctx.exception))
        status = self._scalar("SELECT status FROM agent_run WHERE agent_id=:i ORDER BY id DESC LIMIT 1", {"i": agent_id})
        self.assertEqual(status, "failed")

    def test_stream_failure_emits_an_error_event_not_an_exception(self):
        from service.runtime.agent_runtime import run_stream_with_history_async
        agent_id = self._make_agent("ext-test-streamfail")
        self._configure(agent_id, path="/sse-error")

        async def collect(db):
            return [chunk async for chunk in run_stream_with_history_async(db, self.admin["id"], agent_id, "你好", history=[])]
        chunks = _run_db(collect)
        self.assertTrue(chunks[-1].startswith("event: error"), chunks[-1])
        self.assertIn("对方内部出错", chunks[-1])

    def test_a_user_who_cannot_use_the_agent_never_reaches_the_external_service(self):
        from service.runtime.agent_runtime import run_with_history_async
        agent_id = self._make_agent("ext-test-denied")
        self._configure(agent_id)
        FakeAgent.received.clear()
        with self.assertRaises(ValueError):
            _run_db(lambda db: run_with_history_async(db, self.asker["id"], agent_id, "你好", history=[], conversation_id=None))
        self.assertEqual(FakeAgent.received, [])


if __name__ == "__main__":
    unittest.main()
