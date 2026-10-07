"""Kafka 消费端的逻辑测试（假 Broker）：偏移量只在结果确定后提交、毒消息转死信不卡分区、死信带原因、Broker 抖动不退出循环、
同一分区保序；以及 Kafka 模式下的重试扫描只处理“已有处理记录且到期”的事件。真实 Broker 上的行为见 scripts/e2e_kafka.py。"""
import asyncio
import json
import unittest
import uuid
from datetime import timedelta
from types import SimpleNamespace

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

from sqlalchemy import text

from models.init_db import ConsumerRetry, OutboxEvent, SessionLocal
from service.events import kafka_bus, outbox
from service.events.runner import Consumer
from utils.timeutil import utcnow

_AVAILABLE, _WHY = rc.route_tests_available()


class Message:
    def __init__(self, topic, partition, offset, value, key=b"k", error=None):
        self._t, self._p, self._o, self._v, self._k, self._e = topic, partition, offset, value, key, error

    topic = lambda self: self._t          # noqa: E731
    partition = lambda self: self._p      # noqa: E731
    offset = lambda self: self._o         # noqa: E731
    value = lambda self: self._v          # noqa: E731
    key = lambda self: self._k            # noqa: E731
    error = lambda self: self._e          # noqa: E731


def envelope(i, topic="automation.job.v1"):
    return json.dumps({"event_id": f"e{i}", "event_type": "t", "topic": topic, "payload": {"i": i}, "schema_version": 1}).encode()


class FakeConsumer:
    def __init__(self, batches, stop):
        self.batches, self.stop, self.commits, self.subscribed, self.closed, self.config = list(batches), stop, [], None, False, None

    def subscribe(self, topics):
        self.subscribed = topics

    def consume(self, n, timeout):
        if not self.batches:
            self.stop.set()
            return []
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch

    def commit(self, message=None, asynchronous=True):
        self.commits.append((message.partition(), message.offset(), asynchronous))

    def close(self):
        self.closed = True


class FakeProducer:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def produce(self, topic, key=None, value=None, headers=None, on_delivery=None):
        self.sent.append({"topic": topic, "key": key, "value": value, "headers": dict(headers or [])})
        if on_delivery:
            on_delivery(RuntimeError("boom") if self.fail else None, None)

    def flush(self, timeout):
        return 0


def run_loop(batches, deliver, consumer=None, producer=None):
    consumer = consumer or Consumer(name="t", topics=["automation.job.v1"], handler=None)
    stop = asyncio.Event()
    fake = FakeConsumer(batches, stop)

    async def go():
        stop_event = asyncio.Event()
        fake.stop = stop_event
        await kafka_bus.consume_loop(consumer, deliver, stop_event, bootstrap="x:1", dlq=kafka_bus.Dlq(producer=producer or FakeProducer()),
                                     consumer_factory=lambda config: setattr(fake, "config", config) or fake, poll_seconds=0)
    asyncio.run(go())
    return fake


class ConsumeLoopTest(unittest.TestCase):
    def test_offsets_are_committed_synchronously_after_each_message_is_decided(self):
        order = []

        async def deliver(c, event, stats):
            order.append(("deliver", event["event_id"]))
            return "done"
        fake = run_loop([[Message("automation.job.v1", 0, 5, envelope(1))]], deliver)
        self.assertEqual(order, [("deliver", "e1")])
        self.assertEqual(fake.commits, [(0, 5, False)])                       # 同步提交
        self.assertIs(fake.config["enable.auto.commit"], False)
        self.assertEqual(fake.config["group.id"], "enterprise-agent.t")
        self.assertEqual(fake.subscribed, ["automation.job.v1"])
        self.assertTrue(fake.closed)

    def test_every_outcome_that_is_settled_commits_the_offset(self):
        async def deliver(c, event, stats):
            return {"e1": "done", "e2": "skipped", "e3": "retry"}[event["event_id"]]
        fake = run_loop([[Message("automation.job.v1", 0, i, envelope(i)) for i in (1, 2, 3)]], deliver)
        self.assertEqual([c[1] for c in fake.commits], [1, 2, 3])

    def test_same_partition_is_processed_in_order(self):
        seen = []

        async def deliver(c, event, stats):
            await asyncio.sleep(0.01 if event["event_id"] == "e1" else 0)     # 先到的处理得更慢，也不能被后面的超过
            seen.append(event["event_id"])
            return "done"
        run_loop([[Message("automation.job.v1", 0, i, envelope(i)) for i in (1, 2, 3)]], deliver)
        self.assertEqual(seen, ["e1", "e2", "e3"])

    def test_different_partitions_run_concurrently(self):
        running, peak = [0], [0]

        async def deliver(c, event, stats):
            running[0] += 1
            peak[0] = max(peak[0], running[0])
            await asyncio.sleep(0.05)
            running[0] -= 1
            return "done"
        run_loop([[Message("automation.job.v1", p, 0, envelope(p)) for p in (0, 1, 2)]], deliver)
        self.assertEqual(peak[0], 3)

    def test_dead_events_are_written_to_the_dlq_topic_with_the_reason_whichever_path_exhausted_the_retries(self):
        producer = FakeProducer()
        event = json.loads(envelope(9))
        event["key"] = "batch-1"
        kafka_bus.publish_dead("t", event, "RuntimeError: 对象不存在", 3, dlq=kafka_bus.Dlq(producer=producer))
        self.assertEqual(len(producer.sent), 1)
        sent = producer.sent[0]
        self.assertEqual((sent["topic"], sent["key"]), ("automation.job.v1.dlq", b"batch-1"))
        self.assertEqual((sent["headers"]["dlq_consumer"], sent["headers"]["dlq_attempts"]), (b"t", b"3"))
        self.assertIn("对象不存在".encode(), sent["headers"]["dlq_error"])
        self.assertEqual(json.loads(sent["value"])["event_id"], "e9")          # 原始事件原样保留，可直接重放

    def test_a_dlq_topic_failure_never_raises_into_the_consumer(self):
        kafka_bus.publish_dead("t", json.loads(envelope(1)), "x", 3, dlq=kafka_bus.Dlq(producer=FakeProducer(fail=True)))      # 只记日志

    def test_the_runner_publishes_exhausted_events_to_the_dlq_only_in_kafka_mode(self):
        from unittest.mock import AsyncMock, patch
        from service.events import runner
        c = Consumer(name="t", topics=["automation.job.v1"], handler=None, max_attempts=3)
        event = json.loads(envelope(1))
        with patch.object(runner, "kafka_mode", return_value=True), patch.object(kafka_bus, "publish_dead") as dead, \
                patch("service.observability.issues.record_occurrence"):
            asyncio.run(runner._report_dead(c, event, RuntimeError("boom")))
        dead.assert_called_once()
        self.assertEqual(dead.call_args.args[0], "t")
        with patch.object(runner, "kafka_mode", return_value=False), patch.object(kafka_bus, "publish_dead") as dead, \
                patch("service.observability.issues.record_occurrence"):
            asyncio.run(runner._report_dead(c, event, RuntimeError("boom")))
        dead.assert_not_called()

    def test_poison_messages_are_parked_and_skipped_instead_of_blocking_the_partition(self):
        producer = FakeProducer()
        delivered = []

        async def deliver(c, event, stats):
            delivered.append(event["event_id"])
            return "done"
        fake = run_loop([[Message("automation.job.v1", 0, 1, b"not json"), Message("automation.job.v1", 0, 2, b'{"event_id": "x"}'),
                          Message("automation.job.v1", 0, 3, envelope(3))]], deliver, producer=producer)
        self.assertEqual(delivered, ["e3"])                                    # 后面的正常消息照常处理
        self.assertEqual(len(producer.sent), 2)
        self.assertTrue(all(m["topic"].endswith(".dlq") for m in producer.sent))
        self.assertEqual([c[1] for c in fake.commits], [1, 2, 3])

    def test_a_dlq_write_failure_for_a_poison_message_does_not_commit_so_it_comes_back(self):
        async def deliver(c, event, stats):
            return "done"
        fake = None
        with self.assertRaises(RuntimeError):
            fake = run_loop([[Message("automation.job.v1", 0, 7, b"not json")]], deliver, producer=FakeProducer(fail=True))
        self.assertIsNone(fake)

    def test_a_broker_hiccup_does_not_end_the_loop(self):
        async def deliver(c, event, stats):
            return "done"
        fake = run_loop([RuntimeError("broker down"), [Message("automation.job.v1", 0, 1, envelope(1))]], deliver)
        self.assertEqual([c[1] for c in fake.commits], [1])

    def test_error_messages_from_the_client_are_ignored(self):
        async def deliver(c, event, stats):
            return "done"
        fake = run_loop([[Message("automation.job.v1", 0, 1, b"", error="PARTITION_EOF"), Message("automation.job.v1", 0, 2, envelope(2))]], deliver)
        self.assertEqual([c[1] for c in fake.commits], [2])


class HelperTest(unittest.TestCase):
    def test_names_and_decode(self):
        self.assertEqual(kafka_bus.dlq_topic("a.v1"), "a.v1.dlq")
        self.assertEqual(kafka_bus.decode(envelope(1))["payload"], {"i": 1})
        for bad in (b"", b"[]", b"{}", b'{"event_id": "1"}', "不是".encode()):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                kafka_bus.decode(bad)

    def test_transport_treats_an_unconfirmed_flush_as_a_failure(self):
        """flush 超时（还有消息没确认）不能当成发送成功：否则发件箱标记已发布，消息之后又失败 = 丢事件。"""
        class Slow(FakeProducer):
            def flush(self, timeout):
                return 1
        transport = outbox.KafkaTransport(producer=Slow())
        with self.assertRaises(RuntimeError) as ctx:
            transport({"event_id": "e", "schema_version": 1, "producer": "p", "topic": "automation.job.v1", "aggregate_id": "a", "trace_id": None})
        self.assertIn("没有确认", str(ctx.exception))


@unittest.skipUnless(_AVAILABLE, _WHY)
class RetrySweepTest(unittest.TestCase):
    """Kafka 模式下首次投递归 Kafka，扫描只管已经有处理记录且到期的事件。"""

    def setUp(self):
        self.db = SessionLocal()
        self.db.execute(text("DELETE FROM outbox_event WHERE producer='kafka-sweep-test'"))
        self.db.commit()
        self.ids = []

    def tearDown(self):
        self.db.rollback()
        for event_id in self.ids:
            self.db.execute(text("DELETE FROM consumer_retry WHERE event_id=:e"), {"e": event_id})
            self.db.execute(text("DELETE FROM outbox_event WHERE event_id=:e"), {"e": event_id})
        self.db.commit()
        self.db.close()

    def publish(self, **retry):
        event_id = str(uuid.uuid4())
        self.ids.append(event_id)
        self.db.add(OutboxEvent(event_id=event_id, topic="automation.job.v1", event_type="t", schema_version=1, aggregate_type="x", aggregate_id="1",
                                producer="kafka-sweep-test", payload_json="{}", created_at=utcnow(), published_at=utcnow()))
        if retry:
            self.db.add(ConsumerRetry(event_id=event_id, consumer="sweeper", attempts=retry["attempts"], next_attempt_at=utcnow() + timedelta(seconds=retry["in_seconds"])))
        self.db.commit()
        return event_id

    def fetch(self, retry_only):
        return {e["event_id"] for e in outbox.fetch_deliverable("sweeper", ["automation.job.v1"], 100, retry_only) if e["event_id"] in self.ids}

    def test_database_mode_still_takes_every_unprocessed_event(self):
        fresh, due, waiting = self.publish(), self.publish(attempts=1, in_seconds=-60), self.publish(attempts=1, in_seconds=600)
        self.assertEqual(self.fetch(retry_only=False), {fresh, due})

    def test_kafka_mode_only_sweeps_due_retries_and_expired_leases(self):
        fresh, due, waiting, lease_expired = self.publish(), self.publish(attempts=2, in_seconds=-60), self.publish(attempts=1, in_seconds=600), self.publish(attempts=0, in_seconds=-5)
        self.assertEqual(self.fetch(retry_only=True), {due, lease_expired})           # 没送到的 fresh 归 Kafka，退避中的 waiting 还没到时间


@unittest.skipUnless(_AVAILABLE, _WHY)
class PublisherLockingTest(unittest.TestCase):
    """发布器调用传输层（Kafka）期间不能占着数据库事务：真实联调里 Broker 一慢，它持有的间隙锁就让业务事务的 emit() 插入超时。"""

    def setUp(self):
        self.db = SessionLocal()
        self.db.execute(text("DELETE FROM outbox_event WHERE producer='publisher-lock-test'"))
        self.db.commit()
        self.ids = []

    def tearDown(self):
        self.db.rollback()
        self.db.execute(text("DELETE FROM outbox_event WHERE producer='publisher-lock-test'"))
        self.db.commit()
        self.db.close()

    def add(self, n=1, **extra):
        rows = []
        for _ in range(n):
            event_id = str(uuid.uuid4())
            self.ids.append(event_id)
            rows.append(OutboxEvent(event_id=event_id, topic="automation.job.v1", event_type="t", schema_version=1, aggregate_type="x", aggregate_id="1",
                                    producer="publisher-lock-test", payload_json="{}", created_at=utcnow(), **extra))
        self.db.add_all(rows)
        self.db.commit()
        return [r.event_id for r in rows]

    def row(self, event_id):
        self.db.commit()
        return self.db.execute(text("SELECT published_at, publish_attempts, next_publish_at, last_error FROM outbox_event WHERE event_id=:e"), {"e": event_id}).first()

    def test_other_transactions_can_insert_while_the_transport_is_busy(self):
        self.add(3)
        observed = {}

        def slow_transport(event):
            other = SessionLocal()
            try:
                other.execute(text("SET SESSION innodb_lock_wait_timeout = 2"))
                other.add(OutboxEvent(event_id=str(uuid.uuid4()), topic="automation.job.v1", event_type="t", schema_version=1, aggregate_type="x", aggregate_id="1",
                                      producer="publisher-lock-test", payload_json="{}", created_at=utcnow()))
                other.commit()                                    # 如果发布器还占着事务，这里会等 2 秒后抛 OperationalError(1205)
                observed["inserted"] = observed.get("inserted", 0) + 1
            finally:
                other.close()
        result = outbox.publish_pending(slow_transport)
        self.assertEqual((result["sent"], observed["inserted"]), (3, 3))

    def test_success_and_failure_are_settled_per_event(self):
        ok, bad = self.add(2)

        def transport(event):
            if event["event_id"] == bad:
                raise RuntimeError("Broker 不可用")
        result = outbox.publish_pending(transport)
        self.assertEqual((result["sent"], result["failed"]), (1, 1))
        sent_row, failed_row = self.row(ok), self.row(bad)
        self.assertIsNotNone(sent_row[0])
        self.assertIsNone(failed_row[0])
        self.assertEqual(failed_row[1], 1)
        self.assertIn("Broker 不可用", failed_row[3])
        self.assertGreater(failed_row[2], utcnow())              # 退避到未来

    def test_a_claimed_batch_is_not_republished_by_another_publisher_until_the_lease_expires(self):
        ids = self.add(2)
        calls = []

        def crashing_transport(event):
            calls.append(event["event_id"])
            # 模拟发布器在传输层调用期间进程崩溃：第二个发布器此刻运行，不能抢到同一批（它们被租约占着）
            if len(calls) == 1:
                again = []
                outbox.publish_pending(lambda e: again.append(e["event_id"]))
                self.assertEqual(set(again) & set(ids), set())
        outbox.publish_pending(crashing_transport)
        self.assertEqual(len(calls), 2)

    def test_fail_fast_releases_the_rest_of_the_batch_without_counting_a_failure(self):
        ids = self.add(6)

        def down(event):
            raise RuntimeError("down")
        result = outbox.publish_pending(down)
        self.assertEqual(result, {"sent": 0, "failed": outbox.FAIL_FAST_AFTER})
        untouched = [self.row(i) for i in ids if self.row(i)[1] == 0]
        self.assertEqual(len(untouched), 6 - outbox.FAIL_FAST_AFTER)
        self.assertTrue(all(r[2] is None for r in untouched))     # 租约已释放，下一轮立刻可再试


@unittest.skipUnless(_AVAILABLE, _WHY)
class ClaimRaceTest(unittest.TestCase):
    """真实 Kafka 多实例联调发现：领取前的“已处理”检查和后面的租约插入不是原子的，另一个实例恰好在两步之间处理完成，同一事件会被处理第二次。"""

    def setUp(self):
        self.event_id = str(uuid.uuid4())

    def tearDown(self):
        db = SessionLocal()
        for table in ("consumer_inbox", "consumer_retry"):
            db.execute(text(f"DELETE FROM {table} WHERE event_id=:e"), {"e": self.event_id})
        db.commit()
        db.close()

    def rows(self, table):
        db = SessionLocal()
        try:
            return db.execute(text(f"SELECT COUNT(*) FROM {table} WHERE event_id=:e AND consumer='racer'"), {"e": self.event_id}).scalar()
        finally:
            db.close()

    def test_another_instance_finishing_between_the_check_and_the_insert_does_not_get_the_event_processed_twice(self):
        from unittest.mock import patch
        real_factory = outbox.SessionLocal
        fired = {"done": False}

        class RacingSession:
            def __init__(self, inner):
                self.inner, self.count = inner, 0

            def execute(self, *args, **kwargs):
                result = self.inner.execute(*args, **kwargs)
                self.count += 1
                if self.count == 1 and not fired["done"]:          # 我们的“已处理？”检查刚返回空……
                    fired["done"] = True
                    outbox.mark_done("racer", self.event_id_holder)  # ……另一个实例恰好处理完成（写收件箱 + 删租约）
                return result

            def __getattr__(self, name):
                return getattr(self.inner, name)
        RacingSession.event_id_holder = self.event_id
        with patch.object(outbox, "SessionLocal", lambda: RacingSession(real_factory())):
            claimed = outbox.claim("racer", self.event_id)
        self.assertIsNone(claimed)                                  # 不能再领取：事件已经被处理过
        self.assertEqual((self.rows("consumer_inbox"), self.rows("consumer_retry")), (1, 0))   # 收件箱只有一条，没有遗留的租约

    def test_a_normal_claim_still_works_and_blocks_a_second_claimer(self):
        self.assertEqual(outbox.claim("racer", self.event_id), 0)
        self.assertIsNone(outbox.claim("racer", self.event_id))     # 租约还有效
        outbox.mark_done("racer", self.event_id)
        self.assertIsNone(outbox.claim("racer", self.event_id))     # 已处理


if __name__ == "__main__":
    unittest.main()
