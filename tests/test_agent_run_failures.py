"""Agent 运行失败的追踪：错误码归类、哪些进问题中心、超时阈值、运行记录带 trace_id 与问题编号、可靠性统计（真实 MySQL）。"""
import asyncio
import unittest
from unittest.mock import patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

import requests
from sqlalchemy import text

from models.init_db import Agent, AgentRun, SessionLocal
from service.exceptions import InvalidInput, PermissionDenied, QuotaExceeded, UpstreamError
from service.observability import agent_runs, context as trace_context
from service.runtime.agent_runtime import _finalize_run_async
from tests._async_helpers import run_async
from utils.timeutil import utcnow

_AVAILABLE, _WHY = rc.route_tests_available()


class ClassifyTest(unittest.TestCase):
    def test_user_and_business_errors_are_not_system_faults(self):
        cases = ((ValueError("请先在【模型配置】中配置 glm-4 的 API Key"), ("MODEL_NOT_CONFIGURED", False)),
                 (QuotaExceeded("额度用完"), ("quota_exceeded", False)), (PermissionDenied("无权"), ("permission_denied", False)),
                 (InvalidInput("x"), ("invalid_input", False)))
        for exc, expected in cases:
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(agent_runs.classify_failure(exc), expected)

    def test_system_faults(self):
        cases = ((RuntimeError("bug"), ("INTERNAL_ERROR", True)), (requests.Timeout(), ("MODEL_TIMEOUT", True)),
                 (asyncio.TimeoutError(), ("MODEL_TIMEOUT", True)), (UpstreamError("供应商出错"), ("upstream_error", True)),
                 (None, ("INTERNAL_ERROR", True)))
        for exc, expected in cases:
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(agent_runs.classify_failure(exc), expected)


@unittest.skipUnless(_AVAILABLE, _WHY)
class FailureTrackingTest(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.user = rc.create_user("arf-usr")
        self.agent = Agent(user_id=self.user["id"], name="arf-agent")
        self.db.add(self.agent)
        self.db.commit()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.db.rollback()
        self.db.execute(text("DELETE FROM agent_run WHERE agent_id=:a"), {"a": self.agent.id})
        self.db.execute(text("DELETE FROM agent WHERE id=:a"), {"a": self.agent.id})
        op = f"Agent 运行 #{self.agent.id}"
        self.db.execute(text("DELETE FROM issue_event WHERE issue_id IN (SELECT id FROM system_issue WHERE operation=:o)"), {"o": op})
        self.db.execute(text("DELETE FROM issue_occurrence WHERE issue_id IN (SELECT id FROM system_issue WHERE operation=:o)"), {"o": op})
        self.db.execute(text("DELETE FROM system_issue WHERE operation=:o"), {"o": op})
        self.db.execute(text("DELETE FROM outbox_event WHERE topic='agent.run-event.v1' AND payload_json LIKE :p"), {"p": f'%"agent_id": {self.agent.id},%'})
        self.db.commit()
        self.db.close()
        rc.cleanup()

    def fail(self, exc, trace="c" * 32, message=None):
        from models.async_db import AsyncSessionLocal

        async def go():
            async with AsyncSessionLocal() as db:
                from models.agent_run_async_dao import create_run_async
                token = trace_context.set_trace(trace)
                try:
                    run = await create_run_async(db, self.user["id"], self.agent.id, "你好")
                    await db.commit()
                    await _finalize_run_async(db, run.id, "failed", error_msg=message or str(exc), exc=exc)
                    return run.id
                finally:
                    trace_context.reset_trace(token)
        return run_async(go())

    def run_row(self, run_id):
        self.db.commit()
        return self.db.execute(text("SELECT status, trace_id, error_code, issue_no, error_msg FROM agent_run WHERE id=:i"), {"i": run_id}).first()

    def issues(self):
        self.db.commit()
        return self.db.execute(text("SELECT issue_no, occurrence_count, error_code, category FROM system_issue WHERE operation=:o"),
                               {"o": f"Agent 运行 #{self.agent.id}"}).all()

    def events(self):
        self.db.commit()
        return self.db.execute(text("SELECT event_type FROM outbox_event WHERE topic='agent.run-event.v1' AND payload_json LIKE :p"),
                               {"p": f'%"agent_id": {self.agent.id},%'}).all()

    def test_code_bug_gets_trace_error_code_issue_and_event(self):
        run_id = self.fail(RuntimeError("内部缺陷 password=hunter2"))
        status, trace, code, issue_no, _msg = self.run_row(run_id)
        self.assertEqual((status, trace, code), ("failed", "c" * 32, "INTERNAL_ERROR"))
        self.assertRegex(issue_no, r"^ISSUE-\d{8}-\d{4}$")
        self.assertEqual([(i[2], i[3]) for i in self.issues()], [("INTERNAL_ERROR", "code")])
        self.assertEqual([e[0] for e in self.events()], ["agent.run.failed"])

    def test_same_bug_many_times_is_one_problem_with_a_count(self):
        for _ in range(4):
            self.fail(RuntimeError("同一个缺陷"))
        rows = self.issues()
        self.assertEqual((len(rows), rows[0][1]), (1, 4))

    def test_user_configuration_errors_are_recorded_but_open_no_problem(self):
        run_id = self.fail(ValueError("请先在【模型配置】中配置 glm-4 的 API Key"))
        status, _trace, code, issue_no, _msg = self.run_row(run_id)
        self.assertEqual((status, code, issue_no), ("failed", "MODEL_NOT_CONFIGURED", None))
        self.assertEqual(self.issues(), [])
        self.assertEqual([e[0] for e in self.events()], ["agent.run.failed"])    # 事件里 system_fault 为 false，方便下游过滤

    def test_model_timeouts_open_a_problem_only_after_the_threshold(self):
        with patch.object(agent_runs, "THRESHOLD", 3):
            first = self.fail(requests.Timeout("慢"))
            second = self.fail(requests.Timeout("慢"))
            self.assertEqual((self.issues(), self.run_row(first)[3], self.run_row(second)[3]), ([], None, None))
            third = self.fail(requests.Timeout("慢"))
            rows = self.issues()
            self.assertEqual([(r[2], r[3]) for r in rows], [("MODEL_TIMEOUT", "dependency")])
            self.assertIsNotNone(self.run_row(third)[3])

    def test_tracking_failure_never_breaks_finalizing_the_run(self):
        with patch("service.observability.agent_runs.report_failure", side_effect=RuntimeError("tracker down")):
            run_id = self.fail(RuntimeError("x"))
        self.assertEqual(self.run_row(run_id)[0], "failed")

    def test_health_report(self):
        for _ in range(2):
            self.fail(RuntimeError("bug"))
        self.fail(requests.Timeout("慢"))
        self.db.execute(text("INSERT INTO agent_run (user_id, agent_id, user_message, status, total_steps, total_tokens, started_at) "
                             "VALUES (:u, :a, 'ok', 'finished', 4, 100, :t)"), {"u": self.user["id"], "a": self.agent.id, "t": utcnow()})
        self.db.commit()
        health = agent_runs.agent_health(self.db, 7)
        mine = next(a for a in health["agents"] if a["agent_id"] == self.agent.id)
        self.db.commit()
        self.assertEqual((mine["runs"], mine["failed"], mine["failure_rate"]), (4, 3, 75.0))
        self.assertEqual(mine["error_codes"], {"INTERNAL_ERROR": 2, "MODEL_TIMEOUT": 1})
        self.assertEqual(mine["open_issues"], 1)
        self.assertEqual(mine["agent_name"], "arf-agent")

    def test_admin_endpoint_is_admin_only(self):
        client = rc.make_client()
        admin = rc.create_user("arf-adm", admin=True)
        self.assertEqual(client.get("/admin/issues/agent-health", headers=self.user["headers"]).status_code, 403)
        with rc.admin_env(admin["name"]):
            response = client.get("/admin/issues/agent-health?days=7", headers=admin["headers"])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("agents", response.json())
            self.assertEqual(client.get("/admin/issues/agent-health?days=0", headers=admin["headers"]).status_code, 422)

    def test_run_listing_exposes_trace_and_problem_number(self):
        from service.agent_run_async_service import _run_to_dict
        run_id = self.fail(RuntimeError("x"))
        self.db.commit()
        run = self.db.get(AgentRun, run_id)
        data = _run_to_dict(run)
        self.assertEqual((data["trace_id"], data["error_code"]), ("c" * 32, "INTERNAL_ERROR"))
        self.assertTrue(data["issue_no"].startswith("ISSUE-"))


class OutboxRetentionTest(unittest.TestCase):
    @unittest.skipUnless(_AVAILABLE, _WHY)
    def test_old_published_events_are_purged_but_parked_ones_are_kept(self):
        from datetime import timedelta
        from service.events import outbox
        db = SessionLocal()
        try:
            old, parked, fresh = (outbox.emit(db, topic="platform.audit.v1", event_type="t", aggregate_type="x", aggregate_id=1, payload={"n": i}) for i in range(3))
            db.commit()
            long_ago = utcnow() - timedelta(days=30)
            db.execute(text("UPDATE outbox_event SET published_at=:t WHERE event_id IN (:a, :b)"), {"t": long_ago, "a": old, "b": parked})
            db.execute(text("UPDATE outbox_event SET published_at=:t WHERE event_id=:a"), {"t": utcnow(), "a": fresh})
            db.execute(text("INSERT INTO dead_letter (event_id, consumer, topic, event_type, payload_json, attempts, status, created_at) "
                            "VALUES (:e, 't_purge', 'platform.audit.v1', 't', '{}', 5, 'pending', :c)"), {"e": parked, "c": utcnow()})
            db.commit()
            outbox.purge_old(7)
            left = {r[0] for r in db.execute(text("SELECT event_id FROM outbox_event WHERE event_id IN (:a, :b, :c)"), {"a": old, "b": parked, "c": fresh}).all()}
            db.commit()
            self.assertEqual(left, {parked, fresh})
        finally:
            db.execute(text("DELETE FROM dead_letter WHERE consumer='t_purge'"))
            db.execute(text("DELETE FROM outbox_event WHERE topic='platform.audit.v1'"))
            db.commit()
            db.close()


if __name__ == "__main__":
    unittest.main()
