"""service/enterprise_hub_client.py::resolve_caller_context 的单测（真实 DB）。

核心场景：一个人同时属于两个部门时，`team_id` 不能靠"在职的第一个部门"瞎猜——
应该优先用当前对话所在的部门 Agent 自己的 team_id（`agent.team_id`，仅当
`agent_type='department'`），没有部门 Agent 上下文时才退回"用户自己在职的
第一个部门"（旧行为，作为兜底保留）。修复记录见 docs/enterprise-rbac-plan.md。
"""
import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from models.init_db import Agent, SessionLocal
from service import enterprise_hub_client as hub
from tests import _route_client as rc
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team

_AVAILABLE, _WHY = rc.route_tests_available()


class CallSignsRealRequestBytesTest(unittest.TestCase):
    """P1-8（第四轮审计）：`call()` 签名里的 body_sha256 必须是真正发到线上的那份
    字节的哈希，不能是另外序列化的一份——哪怕语义相同，字段顺序不一样字节就不
    一样，Java 侧重新算的哈希就对不上，会把所有带请求体的合法请求都拒了。这里
    直接 mock `requests.request`，拦下真正发出去的 `data=` 参数，反过来验证
    X-Context 里签的 body_sha256/method/path 是不是跟它一致。"""

    def setUp(self):
        # 熔断器是进程级状态：前面的测试（Java 没启动时的首页等）可能已经把它打开，会让这里的签名断言误报
        from service.http_resilience import circuit_breaker
        circuit_breaker.record_success("enterprise_hub")
        self.addCleanup(circuit_breaker.record_success, "enterprise_hub")

    def _do_call(self, method, path, json_body):
        captured = {}

        def fake_request(m, url, headers=None, data=None, timeout=None):
            captured["method"] = m
            captured["url"] = url
            captured["headers"] = headers
            captured["data"] = data
            resp = Mock()
            resp.status_code = 200
            resp.content = b'{"ok": true}'
            resp.json.return_value = {"ok": True}
            return resp

        with patch("service.enterprise_hub_client._secret", return_value="unit-test-secret"), \
                patch("requests.request", side_effect=fake_request):
            hub.call(method, path, 1, 2, ["oa.leave.write"], "op", json_body=json_body)
        return captured

    def test_body_sha256_matches_actual_bytes_sent(self):
        import base64

        captured = self._do_call("POST", "/oa/leave/requests", {"reason": "测试", "days": 2})
        expected_hash = hashlib.sha256(captured["data"]).hexdigest()

        context = json.loads(base64.b64decode(captured["headers"]["X-Context"]))
        self.assertEqual(context["body_sha256"], expected_hash)
        self.assertEqual(context["method"], "POST")
        self.assertEqual(context["path"], "/oa/leave/requests")
        # data= 发的必须是 json_body 序列化后的原始字节，不是 requests 自己另外
        # 序列化的一份（用 json= 参数会绕开我们控制的那份字节，见 call() 的实现）。
        self.assertEqual(json.loads(captured["data"]), {"reason": "测试", "days": 2})

    def test_get_request_with_no_body_hashes_empty_bytes(self):
        import base64

        captured = self._do_call("GET", "/oa/leave/balance", None)
        context = json.loads(base64.b64decode(captured["headers"]["X-Context"]))
        self.assertEqual(context["body_sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(captured["data"], b"")


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class ResolveCallerContextDepartmentTest(unittest.TestCase):
    """一人一部门：部门 Agent 上下文与用户唯一的部门归属保持一致。"""

    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.user = rc.create_user("ehc-multi-team-user")
        cls.org_id = _create_org(cls.db, "ehc-multi-team-org", cls.user["id"])
        _add_org_member(cls.db, cls.org_id, cls.user["id"], "member")

        cls.team_a = _create_team(cls.db, cls.org_id, "ehc-team-a", cls.user["id"])
        cls.team_b = _create_team(cls.db, cls.org_id, "ehc-team-b", cls.user["id"])
        # 用户只属于一个部门；team_a 是同企业里的无关部门。
        _add_team_member(cls.db, cls.team_b, cls.user["id"], "admin")

        dept_agent_b = Agent(user_id=cls.user["id"], name="ehc-dept-agent-b",
                              agent_type="department", department_code="procurement", team_id=cls.team_b)
        personal_agent = Agent(user_id=cls.user["id"], name="ehc-personal-agent")  # 默认 personal，无 team_id
        cls.db.add_all([dept_agent_b, personal_agent])
        cls.db.commit()
        cls.dept_agent_b_id = dept_agent_b.id
        cls.personal_agent_id = personal_agent.id

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()
        cls.db.execute(__import__("sqlalchemy").text("DELETE FROM teams WHERE id IN (:a, :b)"),
                        {"a": cls.team_a, "b": cls.team_b})
        cls.db.execute(__import__("sqlalchemy").text("DELETE FROM organizations WHERE id=:i"), {"i": cls.org_id})
        cls.db.commit()
        cls.db.close()

    def test_department_agent_context_uses_agents_own_team(self):
        # 通过所属部门的 Agent 操作，必须拿到用户唯一的部门。
        auth = hub.resolve_caller_context(self.user["id"], self.dept_agent_b_id)
        self.assertEqual(auth["team_id"], self.team_b)
        self.assertTrue(auth["is_team_admin"])

    def test_no_agent_context_falls_back_to_users_department(self):
        auth = hub.resolve_caller_context(self.user["id"], None)
        self.assertEqual(auth["team_id"], self.team_b)

    def test_personal_agent_context_falls_back_to_users_department(self):
        auth = hub.resolve_caller_context(self.user["id"], self.personal_agent_id)
        self.assertEqual(auth["team_id"], self.team_b)

    def test_nonexistent_agent_id_falls_back_to_users_department(self):
        auth = hub.resolve_caller_context(self.user["id"], 999_999_999)
        self.assertEqual(auth["team_id"], self.team_b)

    def test_disabled_department_agent_team_is_skipped(self):
        # 唯一部门被停用后，不能再解析出任何部门。
        from sqlalchemy import text as _sql
        self.db.execute(_sql("UPDATE teams SET status='disabled' WHERE id=:i"), {"i": self.team_b})
        self.db.commit()
        try:
            auth = hub.resolve_caller_context(self.user["id"], self.dept_agent_b_id)
            self.assertIsNone(auth["team_id"])
        finally:
            self.db.execute(_sql("UPDATE teams SET status='active' WHERE id=:i"), {"i": self.team_b})
            self.db.commit()

    def test_unrelated_disabled_team_does_not_affect_fallback(self):
        from sqlalchemy import text as _sql
        self.db.execute(_sql("UPDATE teams SET status='disabled' WHERE id=:i"), {"i": self.team_a})
        self.db.commit()
        try:
            auth = hub.resolve_caller_context(self.user["id"], None)
            self.assertEqual(auth["team_id"], self.team_b)
        finally:
            self.db.execute(_sql("UPDATE teams SET status='active' WHERE id=:i"), {"i": self.team_a})
            self.db.commit()


class ErrorDetailTest(unittest.TestCase):
    def response(self, status, body=None, text=""):
        resp = Mock(status_code=status, text=text, content=b"x")
        if body is None:
            resp.json.side_effect = ValueError
        else:
            resp.json.return_value = body
        return resp

    def raised(self, resp):
        with patch.object(hub, "request_with_retry", return_value=resp), \
             self.assertRaises(hub.EnterpriseHubError) as ctx:
            hub.call("POST", "/procurement/requests", 1, 2, ["procurement.write"], "x", json_body={})
        return ctx.exception

    def test_business_message_is_surfaced(self):
        exc = self.raised(self.response(400, {"status": 400, "message": "预算不足：还剩 10，申请了 20"}))
        self.assertEqual((exc.status_code, exc.detail), (400, "预算不足：还剩 10，申请了 20"))

    def test_spring_default_body_is_not_leaked(self):
        body = {"timestamp": "2026-10-01T00:00:00Z", "status": 404, "error": "Not Found", "path": "/internal/x"}
        exc = self.raised(self.response(404, body))
        self.assertNotIn("/internal/x", exc.detail)
        self.assertIn("404", exc.detail)

    def test_non_json_body_is_not_leaked(self):
        exc = self.raised(self.response(502, text="<html>stack trace</html>"))
        self.assertNotIn("stack", exc.detail)


if __name__ == "__main__":
    unittest.main()
