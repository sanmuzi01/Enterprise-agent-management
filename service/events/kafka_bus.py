"""Kafka 消费端：消费者组 + 手动提交偏移量 + 重试 + 死信 Topic。

分工（“Kafka 负责投递，数据库负责幂等与重试状态”）：
- 生产：发件箱发布器把事件写进 Kafka（`outbox.KafkaTransport`，消息键是聚合对象，同一对象的事件进同一分区、保证有序）；
- 消费：每个已注册的消费者（`runner.Consumer`）一个独立的消费者组（`<前缀>.<消费者名>`），所以每个消费者都能收到自己订阅 Topic 的全部事件；
  处理流程和数据库模式完全相同（领取租约 → 处理 → 写收件箱 / 记失败 → 重试 / 死信），重复投递由收件箱挡住；
- 偏移量：处理结果确定之后才提交（成功、重复、已进入重试、已进入死信都算“确定”）。失败的重试状态在数据库里（退避时间），
  由 runner 的重试扫描驱动，不靠 Kafka 反复投递；进程在处理中崩溃 → 偏移量没提交 → 重启后同一条消息再来一次，
  租约到期后由收件箱保证只生效一次；
- 死信：重试用尽的事件除了进 `dead_letter` 表（管理后台可重新投递 / 丢弃），还会写到 `<topic>.dlq`，头里带失败原因、消费者、次数和原始位置，
  方便用 Kafka 工具排查或交给别的系统；无法解析的“毒消息”不阻塞分区：原样转到死信 Topic 后跳过。
"""
import asyncio
import json
import os
from typing import Any, Callable, Dict, List, Optional

from utils.logger_handler import get_logger

logger = get_logger("kafka_bus")

DLQ_SUFFIX = ".dlq"
REQUIRED_KEYS = ("event_id", "event_type", "topic", "payload")


def bootstrap_servers() -> Optional[str]:
    return os.getenv("KAFKA_BOOTSTRAP_SERVERS") or None


def group_id(consumer_name: str) -> str:
    return f"{os.getenv('KAFKA_GROUP_PREFIX', 'enterprise-agent')}.{consumer_name}"


def dlq_topic(topic: str) -> str:
    return topic + DLQ_SUFFIX


def ensure_topics(topics: List[str], bootstrap: Optional[str] = None, partitions: int = 3, replication: int = 1) -> List[str]:
    """创建业务 Topic 和对应的死信 Topic（已存在就跳过）。返回本次新建的 Topic。
    生产环境的 Broker 建议关闭自动建 Topic（拼写错误会悄悄产生没人消费的 Topic），所以应用启动时显式创建。"""
    from confluent_kafka.admin import AdminClient, NewTopic
    admin = AdminClient({"bootstrap.servers": bootstrap or bootstrap_servers()})
    existing = set(admin.list_topics(timeout=10).topics)
    wanted = []
    for topic in topics:
        if topic not in existing:
            wanted.append(NewTopic(topic, num_partitions=partitions, replication_factor=replication))
        if dlq_topic(topic) not in existing:
            wanted.append(NewTopic(dlq_topic(topic), num_partitions=1, replication_factor=replication))
    created = []
    for name, future in admin.create_topics(wanted).items() if wanted else []:
        try:
            future.result()
            created.append(name)
        except Exception as exc:  # noqa: BLE001
            if "TOPIC_ALREADY_EXISTS" not in str(exc):
                raise
    return created


def decode(message_value: bytes) -> Dict[str, Any]:
    """消息 → 事件信封；不是合法信封就抛 ValueError（调用方当毒消息处理）。"""
    event = json.loads(message_value.decode("utf-8"))
    if not isinstance(event, dict) or any(key not in event for key in REQUIRED_KEYS):
        raise ValueError("消息不是合法的事件信封")
    return event


class Dlq:
    """把无法继续处理的消息写到死信 Topic（同步，调用方在线程里用）。"""

    def __init__(self, producer=None, bootstrap: Optional[str] = None):
        if producer is None:
            from confluent_kafka import Producer
            producer = Producer({"bootstrap.servers": bootstrap or bootstrap_servers(), "enable.idempotence": True, "acks": "all"})
        self.producer = producer

    def send(self, topic: str, key: Optional[bytes], value: bytes, *, consumer: str, error: str, attempts: int,
             source: Optional[Dict[str, Any]] = None) -> None:
        errors: List[Any] = []
        headers = [("dlq_consumer", consumer.encode()), ("dlq_error", error[:500].encode("utf-8", "replace")), ("dlq_attempts", str(attempts).encode())]
        for name in ("topic", "partition", "offset"):
            if source and source.get(name) is not None:          # 重试扫描里用尽重试的事件不知道原始分区/偏移，只有主题
                headers.append((f"dlq_source_{name}", str(source[name]).encode()))
        self.producer.produce(dlq_topic(topic), key=key, value=value, headers=headers, on_delivery=lambda err, _m: errors.append(err) if err else None)
        remaining = self.producer.flush(10)
        if errors or remaining:
            raise RuntimeError(f"死信投递失败: {errors[0] if errors else '超时'}")


async def consume_loop(c, deliver: Callable, stop: asyncio.Event, *, bootstrap: Optional[str] = None, dlq: Optional[Dlq] = None,
                       consumer_factory: Optional[Callable] = None, poll_seconds: float = 1.0) -> None:
    """一个已注册消费者的 Kafka 消费循环。deliver(c, event, stats) -> 结果字符串（done / skipped / retry / dead）。"""
    if consumer_factory is None:
        from confluent_kafka import Consumer as KafkaConsumer

        def consumer_factory(config):
            return KafkaConsumer(config)
    kc = consumer_factory({
        "bootstrap.servers": bootstrap or bootstrap_servers(), "group.id": group_id(c.name), "enable.auto.commit": False,
        "auto.offset.reset": "earliest", "session.timeout.ms": 10000, "max.poll.interval.ms": int(c.timeout_seconds * 1000 + 60000),
        "partition.assignment.strategy": "cooperative-sticky",
    })
    dlq = dlq or Dlq(bootstrap=bootstrap)
    kc.subscribe(list(c.topics))
    logger.info("Kafka 消费者 %s 已订阅 %s（组 %s）", c.name, c.topics, group_id(c.name))
    try:
        while not stop.is_set():
            try:
                messages = await asyncio.to_thread(kc.consume, max(1, c.concurrency), poll_seconds)
            except Exception:  # noqa: BLE001 —— 连不上 Broker：不能让循环退出，等恢复
                logger.warning("Kafka 消费者 %s 拉取失败，稍后重试", c.name, exc_info=True)
                await asyncio.sleep(2)
                continue
            if not messages:
                continue
            by_partition: Dict[Any, list] = {}
            for message in messages:
                if message.error():
                    logger.warning("Kafka 消费者 %s 收到错误消息: %s", c.name, message.error())
                    continue
                by_partition.setdefault((message.topic(), message.partition()), []).append(message)

            async def handle_partition(items):
                for message in items:                      # 同一分区内严格按顺序处理
                    await _handle(c, message, deliver, dlq, kc)
            await asyncio.gather(*(handle_partition(items) for items in by_partition.values()))
    finally:
        await asyncio.to_thread(kc.close)


async def _handle(c, message, deliver, dlq: Dlq, kc) -> None:
    source = {"topic": message.topic(), "partition": message.partition(), "offset": message.offset()}
    try:
        event = decode(message.value() or b"")
    except Exception as exc:  # noqa: BLE001 —— 毒消息：转到死信 Topic 后跳过，不能卡住整个分区
        logger.error("Kafka 消费者 %s 丢弃无法解析的消息 %s: %s", c.name, source, exc)
        await asyncio.to_thread(dlq.send, message.topic(), message.key(), message.value() or b"", consumer=c.name, error=f"无法解析: {exc}", attempts=0, source=source)
        await asyncio.to_thread(kc.commit, message=message, asynchronous=False)
        return
    stats = {"done": 0, "retry": 0, "dead": 0, "skipped": 0}
    await deliver(c, event, stats)        # 重试用尽时，死信登记（runner._report_dead → publish_dead）会把消息写进死信 Topic
    await asyncio.to_thread(kc.commit, message=message, asynchronous=False)       # 结果已确定（成功 / 重复 / 已记重试 / 已进死信）才提交偏移量


_shared_dlq: Optional[Dlq] = None


def publish_dead(consumer: str, event: Dict[str, Any], error: str, attempts: int, dlq: Optional[Dlq] = None) -> None:
    """重试用尽的事件写到死信 Topic（同步）。消息原样保留（可直接重放），头里带失败原因、消费者、次数。
    死信 Topic 写失败只记日志：死信表已经有这条记录（权威），Topic 里的副本只是方便用 Kafka 工具排查。"""
    global _shared_dlq
    try:
        sender = dlq or _shared_dlq or Dlq()
        _shared_dlq = sender if dlq is None else _shared_dlq
        sender.send(event["topic"], (event.get("key") or event.get("aggregate_id") or "").encode(), json.dumps(event, ensure_ascii=False).encode("utf-8"),
                    consumer=consumer, error=error, attempts=attempts, source={"topic": event["topic"]})
    except Exception:  # noqa: BLE001
        logger.warning("事件 %s 写入死信 Topic 失败（死信表里仍有记录）", event.get("event_id"), exc_info=True)
