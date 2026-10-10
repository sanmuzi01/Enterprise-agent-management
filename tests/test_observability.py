"""可观测性与问题中心：trace_id 传播、统一错误结构、脱敏、问题聚合与处理门槛、依赖故障只告警一次、Sentry 事件清洗。"""
import logging
import unittest
import uuid
from unittest.mock import patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import SessionLocal
from service import enterprise_hub_client as hub
from service.exceptions import Conflict, InvalidInput, NotFound, UpstreamError
from service.observability import context as ctx
from service.observability import issues
from service.observability.error_codes import CATALOG, should_report, spec_for
from service.observability.redact import REDACTED, redact, redact_text
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()
TAG = "obs-test"


class TraceContextTest(unittest.TestCase):
    def test_traceparent_wins_then_request_id_then_generated(self):
        tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        self.assertEqual(ctx.from_headers(tp, "client-req-0001"), "4bf92f3577b34da6a3ce929d0e0e4736")
        self.assertEqual(ctx.from_headers(None, "A" * 8 + "-" * 0 + "B" * 24), ("a" * 8 + "b" * 24))     # 32 位十六进制原样小写
        folded = ctx.from_headers(None, "frontend-req-123")
        self.assertRegex(folded, r"^[0-9a-f]{32}$")
        self.assertEqual(folded, ctx.from_headers(None, "frontend-req-123"))                              # 稳定，同一个 id 同一个 trace
        self.assertRegex(ctx.from_headers(None, None), r"^[0-9a-f]{32}$")

    def test_untrusted_values_are_not_used(self):
        for bad in ("00-" + "0" * 32 + "-00f067aa0ba902b7-01", "garbage", "00-xyz-1-01"):
            self.assertRegex(ctx.from_headers(bad, "bad id with spaces!"), r"^[0-9a-f]{32}$")
        self.assertIsNone(ctx.normalize("x;DROP TABLE"))

    def test_traceparent_for_keeps_the_trace_id(self):
        out = ctx.traceparent_for("a" * 32)
        self.assertRegex(out, r"^00-a{32}-[0-9a-f]{16}-01$")

    def test_context_is_per_task(self):
        token = ctx.set_trace("c" * 32, user_id=7)
        try:
            self.assertEqual(ctx.current_trace_id(), "c" * 32)
            ctx.add_fields(department_id=3)
            self.assertEqual(ctx.current_fields(), {"user_id": 7, "department_id": 3})
        finally:
            ctx.reset_trace(token)
        self.assertIsNone(ctx.current_trace_id())


class RedactTest(unittest.TestCase):
    def test_keys_and_text(self):
        data = {"Authorization": "Bearer abcdefghijkl", "user": {"phone": "13812345678", "name": "张三"},
                "source_text": "合同正文", "total_tokens": 120, "list": [{"password": "x"}, "token=abc123456"], "ok": 1}
        out = redact(data)
        self.assertEqual(out["Authorization"], REDACTED)
        self.assertEqual(out["user"]["phone"], REDACTED)
        self.assertEqual(out["user"]["name"], "张三")
        self.assertEqual(out["source_text"], REDACTED)
        self.assertEqual(out["total_tokens"], 120)            # 名字里带 token 但只是计数，不能误杀
        self.assertEqual(out["list"][0]["password"], REDACTED)
        self.assertNotIn("abc123456", str(out))
        self.assertEqual(data["user"]["phone"], "13812345678")   # 不修改入参

    def test_text_patterns(self):
        text_ = "Bearer eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4 sk-abcdef123456 手机13812345678 身份证110101199001011234 pwd=hunter2"
        out = redact_text(text_)
        for leaked in ("eyJhbGci", "sk-abcdef", "13812345678", "110101199001011234", "hunter2"):
            self.assertNotIn(leaked, out)

    def test_depth_and_length_are_bounded(self):
        deep = cur = {}
        for _ in range(20):
            cur["x"] = {}
            cur = cur["x"]
        self.assertIn(REDACTED, str(redact(deep)))
        self.assertLessEqual(len(redact_text("a" * 10000)), 2100)


class LoggingTest(unittest.TestCase):
    def test_every_record_carries_trace_id_and_is_redacted(self):
        from utils.logger_handler import get_logger
        logger = get_logger("obs_log_test")
        captured = []

        class Grab(logging.Handler):
            def emit(self, record):
                captured.append(self.format(record))
        grab = Grab()
        from utils.logger_handler import DEFAULT_LOG_FORMAT, _REDACT
        grab.setFormatter(DEFAULT_LOG_FORMAT)
        grab.addFilter(_REDACT)
        logger.addHandler(grab)
        token = ctx.set_trace("d" * 32)
        try:
            logger.error("登录失败 Bearer abcdefghijkl1234 password=%s", "hunter2")
        finally:
            ctx.reset_trace(token)
            logger.removeHandler(grab)
        self.assertIn("d" * 32, captured[0])
        self.assertNotIn("hunter2", captured[0])
        self.assertNotIn("abcdefghijkl1234", captured[0])

    def test_json_formatter(self):
        import json
        from utils.logger_handler import JsonFormatter
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "token=abc12345 失败", None, None)
        record.trace_id = "e" * 32
        data = json.loads(JsonFormatter().format(record))
        self.assertEqual((data["level"], data["trace_id"]), ("ERROR", "e" * 32))
        self.assertNotIn("abc12345", data["message"])
        self.assertTrue(data["timestamp"].endswith("Z"))


class CatalogTest(unittest.TestCase):
    def test_business_errors_are_not_reported_but_system_faults_are(self):
        for code, status in (("invalid_input", 400), ("permission_denied", 403), ("not_found", 404), ("conflict", 409),
                             ("rate_limited", 429), ("quota_exceeded", 429)):
            self.assertFalse(should_report(code, status), code)
        for code, status in (("JAVA_SERVICE_UNAVAILABLE", 503), ("INTERNAL_ERROR", 500), ("upstream_error", 502),
                             ("some_new_code", 500), ("app_error", 500)):
            self.assertTrue(should_report(code, status), code)
        self.assertTrue(should_report("SECURITY_ANOMALY", 403))

    def test_every_spec_is_complete_and_user_safe(self):
        for spec in CATALOG.values():
            self.assertTrue(spec.message and spec.suggestion, spec.code)
            self.assertNotRegex(spec.message + spec.suggestion, r"Traceback|Exception|SQL|java\.")
        self.assertTrue(spec_for("JAVA_SERVICE_UNAVAILABLE").retryable)
        self.assertEqual(spec_for("whatever", 500).code, "INTERNAL_ERROR")
        self.assertFalse(spec_for("whatever", 404).retryable)


@unittest.skipUnless(_AVAILABLE, _WHY)
class IssueServiceTest(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.addCleanup(self.cleanup)
        self.admin = rc.create_user("obs-adm")
        self.admin2 = rc.create_user("obs-adm2")

    def cleanup(self):
        self.db.rollback()
        self.db.execute(text("DELETE FROM issue_event WHERE issue_id IN (SELECT id FROM system_issue WHERE service=:s)"), {"s": TAG})
        self.db.execute(text("DELETE FROM issue_occurrence WHERE issue_id IN (SELECT id FROM system_issue WHERE service=:s)"), {"s": TAG})
        self.db.execute(text("DELETE FROM system_issue WHERE service=:s"), {"s": TAG})
        self.db.commit()
        self.db.close()
        rc.cleanup()

    def record(self, op="POST /a/{id}", code="INTERNAL_ERROR", exc=None, **kw):
        self.db.commit()   # 结束当前事务快照：服务里每个请求都是新会话，测试里的长会话要手动刷新才看得到别处提交的数据
        self.db.expire_all()
        return issues.record_occurrence(error_code=code, operation=op, exc=exc, service=TAG, message="boom", **kw)

    def test_100_occurrences_make_one_issue(self):
        first = self.record(trace_id="t" * 8 + "1")
        for i in range(99):
            self.record(trace_id=f"trace-{i:05d}")
        rows = issues.list_issues(self.db)
        mine = [r for r in rows if r["service"] == TAG]
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0]["occurrence_count"], first["is_new"]), (100, True))
        self.assertRegex(mine[0]["issue_no"], r"^ISSUE-\d{8}-\d{4}$")
        detail = issues.get_issue(self.db, mine[0]["id"])
        self.assertLessEqual(len(detail["occurrences"]), issues.MAX_OCCURRENCES_KEPT)       # 只保留最近一批
        self.assertEqual(detail["last_trace_id"], "trace-00098")

    def test_fingerprint_ignores_trace_user_and_line_numbers_but_not_operation_or_code(self):
        def boom():
            raise ValueError("x")
        errors = []
        for _ in range(2):
            try:
                boom()
            except ValueError as e:
                errors.append(e)
        a = issues.make_fingerprint(service=TAG, error_code="INTERNAL_ERROR", operation="GET /x", exc=errors[0])
        b = issues.make_fingerprint(service=TAG, error_code="INTERNAL_ERROR", operation="GET /x", exc=errors[1])
        self.assertEqual(a, b)
        self.assertNotEqual(a, issues.make_fingerprint(service=TAG, error_code="INTERNAL_ERROR", operation="GET /y", exc=errors[0]))
        self.assertNotEqual(a, issues.make_fingerprint(service=TAG, error_code="MODEL_TIMEOUT", operation="GET /x", exc=errors[0]))
        self.assertNotEqual(a, issues.make_fingerprint(service=TAG, error_code="INTERNAL_ERROR", operation="GET /x", exc=KeyError("k")))

    def test_occurrence_details_are_redacted(self):
        self.record(extra={"authorization": "Bearer abcdefghijkl", "path": "/x"}, resource_type="voucher", resource_id=5)
        issue = next(r for r in issues.list_issues(self.db) if r["service"] == TAG)
        occ = issues.get_issue(self.db, issue["id"])["occurrences"][0]
        self.assertEqual(occ["detail"]["authorization"], REDACTED)
        self.assertNotIn("abcdefghijkl", str(occ))

    def test_failures_never_raise(self):
        with patch.object(issues, "SessionLocal", side_effect=RuntimeError("db down")):
            self.assertIsNone(self.record())

    def _issue(self):
        self.record()
        self.db.commit()
        self.db.expire_all()
        return next(r for r in issues.list_issues(self.db) if r["service"] == TAG)

    def test_lifecycle_requires_owner_root_cause_version_and_a_different_verifier(self):
        issue = self._issue()
        iid = issue["id"]
        step = lambda action, **kw: issues.transition(self.db, iid, self.admin["id"], action, **kw)   # noqa: E731
        self.assertEqual(step("acknowledge")["status"], "ACKNOWLEDGED")
        self.assertEqual(issues.get_issue(self.db, iid)["responsible_user_id"], self.admin["id"])
        with self.assertRaises(InvalidInput) as cm:
            step("resolve", root_cause="连接池泄漏")
        self.assertIn("处理说明", cm.exception.message)
        self.assertIn("修复版本", cm.exception.message)
        step("investigate")
        resolved = step("resolve", root_cause="连接池泄漏", resolution="关闭未释放的会话", fix_version="v1.2.3")
        self.assertEqual((resolved["status"], resolved["fix_version"]), ("RESOLVED", "v1.2.3"))
        with self.assertRaises(InvalidInput):
            step("verify")                                                       # 处理人不能自己验收
        verified = issues.transition(self.db, iid, self.admin2["id"], "verify")
        self.assertEqual(verified["verified_by"], self.admin2["id"])
        with self.assertRaises(Conflict):
            issues.transition(self.db, iid, self.admin2["id"], "verify")
        actions = [e["action"] for e in verified["events"]]
        self.assertEqual(actions, ["acknowledge", "investigate", "resolve", "verify"])

    def test_invalid_transitions_are_rejected(self):
        iid = self._issue()["id"]
        with self.assertRaises(Conflict):
            issues.transition(self.db, iid, self.admin["id"], "verify")           # 没解决不能验收
        with self.assertRaises(Conflict):
            issues.transition(self.db, iid, self.admin["id"], "resolve", root_cause="a", resolution="b", fix_version="c")   # 没确认不能直接关闭
        issues.transition(self.db, iid, self.admin["id"], "acknowledge")
        issues.transition(self.db, iid, self.admin["id"], "resolve", root_cause="a", resolution="b", fix_version="c")
        with self.assertRaises(Conflict):
            issues.transition(self.db, iid, self.admin["id"], "investigate")      # 已解决不能直接回排查，只能复发后处理
        with self.assertRaises(InvalidInput):
            issues.transition(self.db, iid, self.admin["id"], "bogus")
        with self.assertRaises(NotFound):
            issues.transition(self.db, 987654321, self.admin["id"], "note", note="x")

    def test_a_resolved_issue_that_happens_again_regresses_and_needs_new_verification(self):
        iid = self._issue()["id"]
        issues.transition(self.db, iid, self.admin["id"], "acknowledge")
        issues.transition(self.db, iid, self.admin["id"], "resolve", root_cause="a", resolution="b", fix_version="v1")
        issues.transition(self.db, iid, self.admin2["id"], "verify")
        self.record()
        again = issues.get_issue(self.db, iid)
        self.assertEqual((again["status"], again["regress_count"], again["verified_by"]), ("REGRESSED", 1, None))
        self.assertTrue(any(e["action"] == "regressed" and e["actor_name"] == "系统" for e in again["events"]))
        done = issues.transition(self.db, iid, self.admin["id"], "resolve", root_cause="c", resolution="d", fix_version="v2")
        self.assertEqual(done["status"], "RESOLVED")

    def test_summary_and_filters(self):
        self.record(op="GET /a")
        self.record(op="GET /b", code="MODEL_TIMEOUT")
        rows = [r for r in issues.list_issues(self.db, keyword="MODEL_TIMEOUT") if r["service"] == TAG]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "medium")
        iid = rows[0]["id"]
        issues.transition(self.db, iid, self.admin["id"], "acknowledge")
        issues.transition(self.db, iid, self.admin["id"], "resolve", root_cause="a", resolution="b", fix_version="v")
        active = [r for r in issues.list_issues(self.db, status="active") if r["service"] == TAG]
        self.assertEqual(len(active), 1)
        data = issues.summary(self.db)
        self.assertGreaterEqual(data["by_status"]["RESOLVED"], 1)
        self.assertIsNotNone(data["mttr_minutes"])

    def test_external_links_only_when_configured(self):
        iid = self._issue()["id"]
        self.assertEqual(issues.get_issue(self.db, iid)["links"], {"trace": None, "sentry": None, "logs": None})
        with patch.dict("os.environ", {"GRAFANA_URL": "http://g:3000/", "LOG_SEARCH_URL": "http://logs"}):
            self.record(trace_id="f" * 32)
            self.db.commit()
            self.db.expire_all()
            links = issues.get_issue(self.db, iid)["links"]
        self.assertEqual(links["trace"], "http://g:3000/explore?trace_id=" + "f" * 32)
        self.assertIsNone(links["sentry"])


@unittest.skipUnless(_AVAILABLE, _WHY)
class HttpErrorContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.client_normal = rc.make_client()
        from FasdtApi.main import app
        cls.app = app
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.tag = uuid.uuid4().hex[:6]

        async def boom():
            raise RuntimeError(f"secret internal detail {cls.tag} password=hunter2")

        async def bad_input():
            raise InvalidInput("请输入金额")

        async def upstream():
            raise UpstreamError("供应商接口出错")

        for name, fn in (("boom", boom), ("bad", bad_input), ("upstream", upstream)):
            app.add_api_route(f"/__obs_{cls.tag}/{name}", fn, methods=["GET"])
        cls.admin = rc.create_user("obs-http-adm", admin=True)
        cls.member = rc.create_user("obs-http-mem")
        cls.owner = rc.create_user("obs-http-own")
        cls.db = SessionLocal()
        cls.org = _create_org(cls.db, "obs-org-" + cls.tag, cls.owner["id"])
        cls.team = _create_team(cls.db, cls.org, "obs-team", cls.owner["id"])
        cls.db.commit()
        _add_org_member(cls.db, cls.org, cls.member["id"], "member")
        _add_team_member(cls.db, cls.team, cls.member["id"], "admin")

    @classmethod
    def tearDownClass(cls):
        cls.db.execute(text("DELETE FROM issue_event WHERE issue_id IN (SELECT id FROM system_issue WHERE operation LIKE :p)"), {"p": f"%__obs_{cls.tag}%"})
        cls.db.execute(text("DELETE FROM issue_occurrence WHERE issue_id IN (SELECT id FROM system_issue WHERE operation LIKE :p)"), {"p": f"%__obs_{cls.tag}%"})
        cls.db.execute(text("DELETE FROM system_issue WHERE operation LIKE :p"), {"p": f"%__obs_{cls.tag}%"})
        cls.db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()
        rc.cleanup()

    def issues_for(self, name):
        rows = self.db.execute(text("SELECT id, occurrence_count, error_code, issue_no FROM system_issue WHERE operation=:o"),
                               {"o": f"GET /__obs_{self.tag}/{name}"}).all()
        self.db.commit()
        return rows

    def test_unhandled_exception_returns_the_unified_contract_without_internals(self):
        response = self.client.get(f"/__obs_{self.tag}/boom", headers={"X-Request-ID": "front-req-000001"})
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertEqual(body["code"], "INTERNAL_ERROR")
        self.assertEqual(body["trace_id"], response.headers["X-Trace-ID"])
        self.assertRegex(body["trace_id"], r"^[0-9a-f]{32}$")
        self.assertRegex(body["issue_no"], r"^ISSUE-\d{8}-\d{4}$")
        self.assertTrue(body["retryable"])
        self.assertIn("suggestion", body)
        self.assertNotIn("secret internal detail", response.text)
        self.assertNotIn("hunter2", response.text)
        self.assertNotIn("Traceback", response.text)
        self.assertEqual(response.headers["X-Request-ID"], "front-req-000001")

    def test_same_trace_id_for_the_same_request_id_and_traceparent_wins(self):
        a = self.client.get(f"/__obs_{self.tag}/bad", headers={"X-Request-ID": "front-req-000002"})
        b = self.client.get(f"/__obs_{self.tag}/bad", headers={"X-Request-ID": "front-req-000002"})
        self.assertEqual(a.json()["trace_id"], b.json()["trace_id"])
        tp = self.client.get(f"/__obs_{self.tag}/bad", headers={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"})
        self.assertEqual(tp.json()["trace_id"], "4bf92f3577b34da6a3ce929d0e0e4736")

    def test_repeated_failures_make_one_issue_and_business_errors_make_none(self):
        self.client.get(f"/__obs_{self.tag}/boom")
        before = self.issues_for("boom")[0][1]
        for _ in range(5):
            self.client.get(f"/__obs_{self.tag}/boom")
        rows = self.issues_for("boom")
        self.assertEqual((len(rows), rows[0][1] - before), (1, 5))
        self.client.get(f"/__obs_{self.tag}/bad")
        self.assertEqual(self.issues_for("bad"), [])
        response = self.client.get(f"/__obs_{self.tag}/bad")
        self.assertEqual((response.status_code, response.json()["code"], response.json()["detail"]), (400, "invalid_input", "请输入金额"))
        self.assertNotIn("issue_no", response.json())

    def test_upstream_error_is_reported_with_retry_hint(self):
        response = self.client.get(f"/__obs_{self.tag}/upstream")
        body = response.json()
        self.assertEqual((response.status_code, body["code"], body["retryable"]), (502, "upstream_error", True))
        self.assertEqual(response.headers["Retry-After"], str(body["retry_after"]))
        self.assertTrue(self.issues_for("upstream"))

    def test_dependency_classification(self):
        import asyncio
        import requests
        from service.http_resilience import CircuitOpenError
        from service.observability.dependency_errors import classify
        cases = ((hub.HubUnavailable(), ("JAVA_SERVICE_UNAVAILABLE", 503)), (CircuitOpenError("enterprise_hub 暂时不可用"), ("JAVA_SERVICE_UNAVAILABLE", 503)),
                 (CircuitOpenError("other 暂时不可用"), ("upstream_error", 503)), (requests.Timeout(), ("MODEL_TIMEOUT", 504)),
                 (asyncio.TimeoutError(), ("MODEL_TIMEOUT", 504)), (requests.ConnectionError(), ("upstream_error", 502)),
                 (KeyError("x"), ("INTERNAL_ERROR", 500)))
        for exc, expected in cases:
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(classify(exc), expected)

    def test_hub_client_turns_network_failures_into_one_clean_error(self):
        import requests
        from service import http_resilience
        http_resilience.circuit_breaker.record_success(hub.SERVICE_NAME)
        with patch("requests.request", side_effect=requests.ConnectionError("refused")), patch("time.sleep"), \
                patch.dict("os.environ", {"HTTP_CLIENT_MAX_RETRIES": "0", "HTTP_CIRCUIT_FAILURE_THRESHOLD": "1000"}):
            with self.assertRaises(hub.HubUnavailable) as cm:
                hub.call("GET", "/x", 1, 2, ["s"], "op")
        self.assertIsNone(cm.exception.__cause__)                  # 不带底层堆栈
        self.assertEqual(cm.exception.status_code, 503)
        error = None
        from service.hub_gateway import translate_hub_error
        error = translate_hub_error(cm.exception)
        self.assertEqual((error.code, error.http_status), ("JAVA_SERVICE_UNAVAILABLE", 503))

    def test_hub_client_keeps_real_status_for_retried_4xx(self):
        from unittest.mock import Mock
        from service import http_resilience
        http_resilience.circuit_breaker.record_success(hub.SERVICE_NAME)
        response = Mock(status_code=409, content=b'{"message":"in flight"}')
        response.json.return_value = {"message": "in flight"}
        response.text = '{"message":"in flight"}'
        with patch("requests.request", return_value=response), patch("time.sleep"), patch.dict("os.environ", {"HTTP_CLIENT_MAX_RETRIES": "1"}):
            with self.assertRaises(hub.EnterpriseHubError) as cm:
                hub.call("POST", "/x", 1, 2, ["s"], "op", json_body={})
        self.assertEqual(cm.exception.status_code, 409)
        self.assertNotIsInstance(cm.exception, hub.HubUnavailable)

    def test_hub_gets_the_request_trace_id_in_its_signed_context(self):
        import base64
        import json
        token = ctx.set_trace("9" * 32)
        try:
            headers = hub.sign_context(1, 2, ["s"], "op", method="GET", path="/x", body_sha256="0")
        finally:
            ctx.reset_trace(token)
        self.assertEqual(json.loads(base64.b64decode(headers["X-Context"]))["trace_id"], "9" * 32)

    def test_circuit_changes_alert_once_and_record_recovery(self):
        from service import http_resilience
        from service.observability import dependency_events
        breaker = http_resilience.CircuitBreaker()
        changes = []
        breaker.listener = lambda name, state: changes.append((name, state))
        with patch.dict("os.environ", {"HTTP_CIRCUIT_FAILURE_THRESHOLD": "3", "HTTP_CIRCUIT_COOLDOWN_SECONDS": "30"}):
            for _ in range(50):
                breaker.record_failure("svc_x")
            breaker.record_success("svc_x")
            breaker.record_success("svc_x")
        self.assertEqual(changes, [("svc_x", "opened"), ("svc_x", "recovered")])     # 50 次失败只有一次告警
        with patch.object(issues, "SERVICE_NAME", TAG), patch.object(dependency_events, "SERVICE_NAME", TAG):
            dependency_events.on_circuit_change("enterprise_hub", "opened")
            dependency_events.on_circuit_change("enterprise_hub", "opened")
            dependency_events.on_circuit_change("enterprise_hub", "recovered")
        row = self.db.execute(text("SELECT id, occurrence_count FROM system_issue WHERE service=:s AND error_code='JAVA_SERVICE_UNAVAILABLE'"), {"s": TAG}).first()
        self.db.commit()
        events = [e[0] for e in self.db.execute(text("SELECT action FROM issue_event WHERE issue_id=:i"), {"i": row[0]}).all()]
        self.db.commit()
        self.assertIn("dependency_recovered", events)
        for table in ("issue_event", "issue_occurrence"):
            self.db.execute(text(f"DELETE FROM {table} WHERE issue_id=:i"), {"i": row[0]})
        self.db.execute(text("DELETE FROM system_issue WHERE id=:i"), {"i": row[0]})
        self.db.commit()

    # ---- 管理端与部门负责人的访问边界 ----

    def test_admin_api_is_admin_only_and_actions_are_audited(self):
        self.client.get(f"/__obs_{self.tag}/boom")
        iid = self.issues_for("boom")[0][0]
        self.assertEqual(self.client.get("/admin/issues", headers=self.member["headers"]).status_code, 403)
        with rc.admin_env(self.admin["name"]):
            listed = self.client.get(f"/admin/issues?keyword=__obs_{self.tag}", headers=self.admin["headers"])
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(len(listed.json()), 1)
            early = self.client.post(f"/admin/issues/{iid}/actions", json={"action": "resolve", "root_cause": "x"}, headers=self.admin["headers"])
            self.assertEqual(early.status_code, 409)
            ok = self.client.post(f"/admin/issues/{iid}/actions", json={"action": "acknowledge", "note": "我来看"}, headers=self.admin["headers"])
            self.assertEqual((ok.status_code, ok.json()["status"]), (200, "ACKNOWLEDGED"))
            bad = self.client.post(f"/admin/issues/{iid}/actions", json={"action": "resolve", "root_cause": "x"}, headers=self.admin["headers"])
            self.assertEqual(bad.status_code, 400)
            self.assertEqual(self.client.post(f"/admin/issues/{iid}/actions", json={"action": "hack"}, headers=self.admin["headers"]).status_code, 422)
            self.assertEqual(self.client.get("/admin/issues/summary", headers=self.admin["headers"]).status_code, 200)

    def test_department_head_sees_only_their_department_without_technical_detail(self):
        from service.observability import issues as svc
        svc.record_occurrence(error_code="INTERNAL_ERROR", operation=f"GET /__obs_{self.tag}/dept", service=TAG, message="m",
                              department_id=self.team, extra={"path": "/secret"}, trace_id="a" * 32)
        svc.record_occurrence(error_code="SECURITY_ANOMALY", operation=f"GET /__obs_{self.tag}/sec", service=TAG, message="m",
                              department_id=self.team)
        svc.record_occurrence(error_code="INTERNAL_ERROR", operation=f"GET /__obs_{self.tag}/other", service=TAG, message="m",
                              department_id=self.team + 999999)
        listed = self.client.get(f"/enterprise/issues?team_id={self.team}", headers=self.member["headers"])
        self.assertEqual(listed.status_code, 200, listed.text)
        ops = [r["operation"] for r in listed.json() if self.tag in (r["operation"] or "")]
        self.assertEqual(ops, [f"GET /__obs_{self.tag}/dept"])                       # 不含安全类，不含别的部门
        iid = next(r["id"] for r in listed.json() if self.tag in (r["operation"] or ""))
        detail = self.client.get(f"/enterprise/issues/{iid}?team_id={self.team}", headers=self.member["headers"]).json()
        self.assertNotIn("links", detail)
        self.assertTrue(all("detail" not in o for o in detail["occurrences"]))
        self.assertEqual(self.client.get(f"/enterprise/issues?team_id={self.team}", headers=self.owner["headers"]).status_code, 403)   # 不是该部门负责人
        sec = self.db.execute(text("SELECT id FROM system_issue WHERE operation=:o"), {"o": f"GET /__obs_{self.tag}/sec"}).scalar()
        self.db.commit()
        self.assertEqual(self.client.get(f"/enterprise/issues/{sec}?team_id={self.team}", headers=self.member["headers"]).status_code, 404)
        self.assertEqual(self.client.get(f"/enterprise/issues/{iid}?team_id={self.team + 1}", headers=self.member["headers"]).status_code, 403)


class SentryScrubTest(unittest.TestCase):
    def test_expected_business_errors_are_dropped_and_events_are_scrubbed(self):
        from service.observability import sentry_setup
        self.assertIsNone(sentry_setup.scrub_event({"x": 1}, {"exc_info": (InvalidInput, InvalidInput("x"), None)}))
        self.assertIsNotNone(sentry_setup.scrub_event({"x": 1}, {"exc_info": (UpstreamError, UpstreamError("x"), None)}))
        event = {"request": {"headers": {"Authorization": "Bearer abcdefghijkl", "User-Agent": "ua", "Cookie": "s=1"}, "data": {"password": "x"},
                             "cookies": {"s": "1"}, "query_string": "phone=13812345678"},
                 "user": {"id": 1, "email": "a@b.c"}, "extra": {"source_text": "合同", "note": "token=abc123456"}, "tags": {"phone": "13812345678"}}
        out = sentry_setup.scrub_event(event, {})
        self.assertEqual(out["request"]["headers"], {"User-Agent": "ua"})
        for removed in ("data", "cookies", "query_string"):
            self.assertNotIn(removed, out["request"])
        self.assertNotIn("user", out)
        self.assertEqual(out["extra"]["source_text"], REDACTED)
        self.assertNotIn("abc123456", str(out))

    def test_disabled_without_dsn(self):
        from service.observability import sentry_setup
        with patch.dict("os.environ", {"SENTRY_DSN": ""}):
            self.assertFalse(sentry_setup.init())
        self.assertIsNone(sentry_setup.capture(RuntimeError("x"), trace_id="a", operation="o", error_code="c"))


class DemoPidTest(unittest.TestCase):
    def test_pid_alive_matches_exact_pid_only(self):
        import importlib.util
        import os
        import pathlib
        spec = importlib.util.spec_from_file_location("demo_launcher_obs", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "demo.py")
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        self.assertTrue(demo.pid_alive(os.getpid()))
        self.assertFalse(demo.pid_alive(999999))
        self.assertFalse(demo.pid_alive(int(str(os.getpid())[:-1] or "1") + 10 ** 7))


if __name__ == "__main__":
    unittest.main()
