"""事务性发件箱 / 收件箱 / 重试 / 死信：事件不丢、不重复处理、崩溃后继续、失败进死信并可人工处理（真实 MySQL）。"""
import asyncio
import json
import unittest
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import SessionLocal
from service.events import handlers as _handlers  # noqa: F401  —— 注册生产消费者
from service.events import outbox, runner
from service.events.runner import Consumer, TryAgain
from service.exceptions import Conflict, InvalidInput, NotFound
from service.observability import issues
from tests._async_helpers import run_async
from utils.timeutil import utcnow

_AVAILABLE, _WHY = rc.route_tests_available()
TOPIC = "platform.audit.v1"


class FakeProducer:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def produce(self, topic, key, value, headers, on_delivery):
        self.sent.append({"topic": topic, "key": key, "value": value, "headers": dict(headers)})
        on_delivery(self.fail, None)

    def flush(self, timeout):
        return 0


@unittest.skipUnless(_AVAILABLE, _WHY)
class OutboxTest(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.calls = []
        self.fail_for = set()
        self.dead_calls = []
        self.name = "t_" + uuid.uuid4().hex[:8]

        async def handle(event):
            self.calls.append(event["event_id"])
            if event["payload"].get("n") in self.fail_for:
                raise RuntimeError(f"处理失败 password=hunter2 n={event['payload'].get('n')}")

        async def dead(event, error):
            self.dead_calls.append((event["event_id"], error))
        self.consumer = runner.register(Consumer(name=self.name, topics=[TOPIC], handler=handle, max_attempts=3, on_dead=dead))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        runner.REGISTRY.pop(self.name, None)
        self.db.rollback()
        for table in ("consumer_inbox", "consumer_retry", "dead_letter"):
            self.db.execute(text(f"DELETE FROM {table} WHERE consumer LIKE 't\\_%'"))
        self.db.execute(text("DELETE FROM outbox_event WHERE topic=:t"), {"t": TOPIC})
        self.db.execute(text("DELETE FROM system_issue WHERE service='agent-service' AND error_code='KAFKA_CONSUME_FAILED' AND operation LIKE :o"),
                        {"o": f"消费者 {self.name}%"})
        self.db.commit()
        self.db.close()

    def emit(self, n=1, commit=True, **over):
        kwargs = dict(topic=TOPIC, event_type="test.happened", aggregate_type="thing", aggregate_id="7", payload={"n": n})
        kwargs.update(over)
        event_id = outbox.emit(self.db, **kwargs)
        if commit:
            self.db.commit()
        return event_id

    def cycle(self, n=1):
        for _ in range(n):
            result = run_async(runner.run_cycle())
        return result

    def row(self, table, **where):
        self.db.commit()   # 结束上一个事务快照，才能看到其它会话刚提交的数据
        clause = " AND ".join(f"{k}=:{k}" for k in where)
        found = self.db.execute(text(f"SELECT * FROM {table} WHERE {clause}"), where).mappings().all()
        self.db.commit()
        return found

    # ---- 生产：和业务事务一起 ----

    def test_event_exists_only_if_the_business_transaction_commits(self):
        rolled = self.emit(commit=False)
        self.db.rollback()
        self.assertEqual(self.row("outbox_event", event_id=rolled), [])
        kept = self.emit()
        self.assertEqual(len(self.row("outbox_event", event_id=kept)), 1)

    def test_emit_guards(self):
        with self.assertRaises(outbox.UnknownTopic):
            self.emit(topic="platform.typo.v1")
        with self.assertRaises(ValueError):
            self.emit(payload={f"k{i}": "x" * 1500 for i in range(20)})

    def test_payload_never_carries_secrets(self):
        event_id = self.emit(payload={"work_id": "w1", "authorization": "Bearer abcdefghijkl", "source_text": "合同正文", "note": "token=abc123456"})
        stored = self.row("outbox_event", event_id=event_id)[0]["payload_json"]
        for leaked in ("abcdefghijkl", "合同正文", "abc123456"):
            self.assertNotIn(leaked, stored)
        self.assertIn("w1", stored)

    def test_envelope_follows_the_contract(self):
        event_id = self.emit(organization_id=1, department_id=3, key="k1", trace_id="a" * 32)
        seen = []
        outbox.publish_pending(seen.append)
        envelope = next(e for e in seen if e["event_id"] == event_id)
        self.assertEqual({k for k in envelope}, {"event_id", "event_type", "schema_version", "occurred_at", "organization_id", "department_id",
                                                 "aggregate_type", "aggregate_id", "trace_id", "producer", "topic", "key", "payload"})
        self.assertEqual((envelope["schema_version"], envelope["department_id"], envelope["trace_id"]), (1, 3, "a" * 32))

    # ---- 发布：失败不丢、退避重试 ----

    def test_transport_failure_keeps_the_event_and_backs_off(self):
        event_id = self.emit()

        def broken(_event):
            raise ConnectionError("broker down password=hunter2")
        outbox.publish_pending(broken)
        row = self.row("outbox_event", event_id=event_id)[0]
        self.assertIsNone(row["published_at"])
        self.assertEqual(row["publish_attempts"], 1)
        self.assertNotIn("hunter2", row["last_error"])
        self.assertGreater(row["next_publish_at"], utcnow() - timedelta(seconds=2))
        # 退避期内不会再被发布；到期后恢复，事件还在，补发成功
        sent = []
        outbox.publish_pending(sent.append)
        self.assertNotIn(event_id, [e["event_id"] for e in sent])
        self.db.execute(text("UPDATE outbox_event SET next_publish_at=:t WHERE event_id=:e"), {"t": utcnow() - timedelta(seconds=5), "e": event_id})
        self.db.commit()
        outbox.publish_pending(sent.append)
        self.assertIn(event_id, [e["event_id"] for e in sent])
        self.assertIsNotNone(self.row("outbox_event", event_id=event_id)[0]["published_at"])

    def test_backoff_grows_and_is_capped(self):
        self.assertEqual([outbox.backoff_seconds(n) for n in (1, 2, 3)], [2, 4, 8])
        self.assertEqual(outbox.backoff_seconds(50), outbox.MAX_PUBLISH_BACKOFF)

    def test_kafka_adapter_sends_key_headers_and_raises_on_delivery_error(self):
        producer = FakeProducer()
        transport = outbox.KafkaTransport(producer=producer)
        event = {"event_id": "e1", "topic": TOPIC, "key": "k", "aggregate_id": "7", "schema_version": 1, "producer": "agent-service",
                 "trace_id": "b" * 32, "payload": {}}
        transport(event)
        sent = producer.sent[0]
        self.assertEqual((sent["topic"], sent["key"]), (TOPIC, b"k"))
        self.assertTrue(sent["headers"]["traceparent"].startswith(b"00-" + b"b" * 32))
        self.assertEqual(sent["headers"]["event_id"], b"e1")
        self.assertEqual(json.loads(sent["value"])["event_id"], "e1")
        with self.assertRaises(RuntimeError):
            outbox.KafkaTransport(producer=FakeProducer(fail="timeout"))(event)

    # ---- 消费：幂等 ----

    def test_each_event_is_handled_once_even_if_delivered_again(self):
        event_id = self.emit()
        self.cycle(3)
        self.assertEqual(self.calls, [event_id])
        # 模拟 Kafka 重复投递同一个事件：收件箱里已有，领取直接失败
        self.assertIsNone(outbox.claim(self.name, event_id))
        run_async(runner._deliver(self.consumer, {"event_id": event_id, "topic": TOPIC, "event_type": "x", "payload": {}},
                                  {"done": 0, "retry": 0, "dead": 0, "skipped": 0}))
        self.assertEqual(self.calls, [event_id])
        self.assertEqual(len(self.row("consumer_inbox", event_id=event_id, consumer=self.name)), 1)

    def test_two_workers_cannot_hold_the_same_event(self):
        event_id = self.emit()
        outbox.publish_pending(outbox.db_transport)
        self.assertEqual(outbox.claim(self.name, event_id), 0)
        self.assertIsNone(outbox.claim(self.name, event_id))          # 第二个进程领不到
        self.assertEqual(outbox.fetch_deliverable(self.name, [TOPIC]), [])

    def test_crash_mid_processing_is_resumed_after_the_lease_expires(self):
        event_id = self.emit()
        outbox.publish_pending(outbox.db_transport)
        outbox.claim(self.name, event_id)                              # 领取后进程崩溃：没有 mark_done
        self.cycle()
        self.assertEqual(self.calls, [])                               # 租约还没到期，不会抢
        self.db.execute(text("UPDATE consumer_retry SET next_attempt_at=:t WHERE event_id=:e AND consumer=:c"),
                        {"t": utcnow() - timedelta(seconds=1), "e": event_id, "c": self.name})
        self.db.commit()
        self.cycle()
        self.assertEqual(self.calls, [event_id])                       # 租约到期后接着处理，没有丢

    def test_events_are_delivered_in_publish_order(self):
        ids = [self.emit(n=i) for i in range(5)]
        self.cycle(2)
        self.assertEqual(self.calls, ids)

    # ---- 失败：重试 → 死信 → 人工处理 ----

    def test_failures_retry_then_dead_letter_and_report_a_problem(self):
        event_id = self.emit(n=99)
        self.fail_for = {99}
        for _ in range(3):
            self.cycle()
            self.db.execute(text("UPDATE consumer_retry SET next_attempt_at=:t WHERE event_id=:e"), {"t": utcnow() - timedelta(seconds=1), "e": event_id})
            self.db.commit()
        dead = self.row("dead_letter", event_id=event_id, consumer=self.name)
        self.assertEqual(len(dead), 1)
        self.assertEqual((dead[0]["status"], dead[0]["attempts"]), ("pending", 3))
        self.assertNotIn("hunter2", dead[0]["error"])
        self.assertEqual(self.row("consumer_retry", event_id=event_id), [])
        self.assertEqual(len(self.calls), 3)
        self.cycle(2)
        self.assertEqual(len(self.calls), 3)                           # 进了死信就不再自动重试
        self.assertEqual(len(self.dead_calls), 1)                      # 消费者有机会收尾
        problem = self.row("system_issue", error_code="KAFKA_CONSUME_FAILED", operation=f"消费者 {self.name} · test.happened")
        self.assertEqual(len(problem), 1)                              # 进入问题中心

    def test_one_bad_event_does_not_block_the_others(self):
        bad, good = self.emit(n=1), self.emit(n=2)
        self.fail_for = {1}
        self.cycle(2)
        self.assertIn(good, self.calls)
        self.assertEqual(len(self.row("consumer_inbox", event_id=good, consumer=self.name)), 1)
        self.assertEqual(self.row("consumer_inbox", event_id=bad, consumer=self.name), [])

    def _kill(self, event_id):
        self.fail_for = {5}
        for _ in range(3):
            self.cycle()
            self.db.execute(text("UPDATE consumer_retry SET next_attempt_at=:t WHERE event_id=:e"), {"t": utcnow() - timedelta(seconds=1), "e": event_id})
            self.db.commit()
        return self.row("dead_letter", event_id=event_id)[0]["id"]

    def test_redeliver_fixes_and_reprocesses_with_audit_trail(self):
        event_id = self.emit(n=5)
        dead_id = self._kill(event_id)
        self.fail_for = set()                                          # 问题修好了
        result = outbox.redeliver(self.db, dead_id, 42)
        self.assertEqual((result["status"], result["handled_by"]), ("redelivered", 42))
        self.cycle(2)
        self.assertEqual(len(self.row("consumer_inbox", event_id=event_id, consumer=self.name)), 1)
        with self.assertRaises(Conflict):
            outbox.redeliver(self.db, dead_id, 42)                     # 处理过的不能再处理

    def test_redelivered_event_that_fails_again_becomes_a_new_dead_letter(self):
        event_id = self.emit(n=5)
        dead_id = self._kill(event_id)
        outbox.redeliver(self.db, dead_id, 42)                         # 没修好就重投
        for _ in range(3):
            self.cycle()
            self.db.execute(text("UPDATE consumer_retry SET next_attempt_at=:t WHERE event_id=:e"), {"t": utcnow() - timedelta(seconds=1), "e": event_id})
            self.db.commit()
        statuses = sorted(r["status"] for r in self.row("dead_letter", event_id=event_id))
        self.assertEqual(statuses, ["pending", "redelivered"])

    def test_discard_needs_a_reason_and_stops_delivery_for_good(self):
        event_id = self.emit(n=5)
        dead_id = self._kill(event_id)
        with self.assertRaises(InvalidInput):
            outbox.discard(self.db, dead_id, 42, " ")
        result = outbox.discard(self.db, dead_id, 42, "数据已在别处手工补录")
        self.assertEqual((result["status"], result["discard_reason"]), ("discarded", "数据已在别处手工补录"))
        self.fail_for = set()
        self.cycle(2)
        self.assertEqual(self.calls.count(event_id), 3)                # 丢弃后不会再被投递
        with self.assertRaises(NotFound):
            outbox.discard(self.db, 987654321, 42, "不存在")

    def test_dead_letter_listing_and_detail(self):
        event_id = self.emit(n=5)
        dead_id = self._kill(event_id)
        listed = [d for d in outbox.list_dead_letters(self.db) if d["consumer"] == self.name]
        self.assertEqual([d["id"] for d in listed], [dead_id])
        detail = outbox.get_dead_letter(self.db, dead_id)
        self.assertEqual(detail["event"]["event_id"], event_id)
        self.assertEqual(detail["event"]["payload"], {"n": 5})
        stats = outbox.stats(self.db)
        self.assertGreaterEqual(stats["dead_letters"], 1)

    def test_runner_loop_survives_a_failing_cycle(self):
        async def go():
            r = runner.Runner(interval=0.05)
            with patch.object(runner, "run_cycle", AsyncMock(side_effect=[RuntimeError("boom"), {"published": {"sent": 0}, "consumed": {"done": 0, "retry": 0}}])) as cycle:
                r.start()
                await asyncio.sleep(0.4)
                await r.stop()
                return cycle.await_count
        self.assertGreaterEqual(run_async(go()), 2)


@unittest.skipUnless(_AVAILABLE, _WHY)
class IssueEventsTest(unittest.TestCase):
    TAG = "obs-evt"

    def setUp(self):
        self.db = SessionLocal()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.db.rollback()
        self.db.execute(text("DELETE FROM outbox_event WHERE topic='platform.issue.v1' AND payload_json LIKE :p"), {"p": f"%{self.TAG}%"})
        self.db.execute(text("DELETE FROM issue_event WHERE issue_id IN (SELECT id FROM system_issue WHERE service=:s)"), {"s": self.TAG})
        self.db.execute(text("DELETE FROM issue_occurrence WHERE issue_id IN (SELECT id FROM system_issue WHERE service=:s)"), {"s": self.TAG})
        self.db.execute(text("DELETE FROM system_issue WHERE service=:s"), {"s": self.TAG})
        self.db.commit()
        self.db.close()

    def events(self):
        self.db.commit()
        rows = self.db.execute(text("SELECT event_type, payload_json FROM outbox_event WHERE topic='platform.issue.v1' ORDER BY id")).all()
        self.db.commit()
        return [(t, json.loads(p)) for t, p in rows if self.TAG in p or True]

    def record(self, op, code="INTERNAL_ERROR"):
        return issues.record_occurrence(error_code=code, operation=f"{op} {self.TAG}", service=self.TAG, message="m")

    def mine(self):
        return [(t, p) for t, p in self.events() if self.TAG in (p.get("title") or "")]

    def test_one_event_for_a_new_problem_none_for_repeats_and_one_for_regression(self):
        first = self.record("GET /a")
        for _ in range(5):
            self.record("GET /a")
        self.assertEqual([t for t, _ in self.mine()], ["issue.created"])
        issues.transition(self.db, first["id"], 1, "acknowledge")
        issues.transition(self.db, first["id"], 1, "resolve", root_cause="a", resolution="b", fix_version="v1")
        self.record("GET /a")
        self.assertEqual([t for t, _ in self.mine()], ["issue.created", "issue.regressed"])
        self.assertEqual(self.mine()[1][1]["regress_count"], 1)

    def test_notifier_alerts_admins_only_for_serious_or_regressed_problems(self):
        high = self.record("GET /high")            # INTERNAL_ERROR 严重度 high
        low = issues.record_occurrence(error_code="MODEL_TIMEOUT", operation=f"GET /low {self.TAG}", service=self.TAG, message="m")   # medium
        admin = rc.create_user("evt-adm")
        self.addCleanup(rc.cleanup)
        with patch.dict("os.environ", {"ADMIN_USER_NAMES": admin["name"]}):
            run_async(runner.run_cycle())
            run_async(runner.run_cycle())
        rows = self.db.execute(text("SELECT title, link FROM notification WHERE user_id=:u"), {"u": admin["id"]}).all()
        self.db.commit()
        titles = [r[0] for r in rows]
        self.assertTrue(any(t.startswith("新问题：") and "GET /high" in t for t in titles), titles)
        self.assertFalse(any("/low" in t for t in titles), titles)               # 中等严重度的新问题不打扰管理员
        self.assertEqual({r[1] for r in rows}, {"/admin/issues"})
        self.assertTrue(high["is_new"] and low["is_new"])


if __name__ == "__main__":
    unittest.main()
