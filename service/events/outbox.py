"""事务性发件箱 + 消费者收件箱 + 重试 + 死信（同步实现，调用方在线程里用）。

可靠性约定（“至少一次投递 + 消费者幂等”）：
1. 生产：业务数据和事件在**同一个数据库事务**里写入（`emit` 只 add 不 commit）。事务回滚事件就不存在，事务提交事件就一定存在——
   不会出现“数据库成功、事件丢了”。
2. 发布：发布器把未发布的事件送到传输层（Kafka 或进程内的 DB 模式），成功才标记 published_at；失败按指数退避重试，不丢。
3. 消费：消费者先领取（租约，防止多个进程同时处理），处理成功后写收件箱；已在收件箱里的事件再次投递直接跳过。
   租约到期还没处理完（进程崩溃）→ 事件自动重新可领取，所以重启后会继续处理而不是丢失。
4. 失败：按退避重试，达到上限进入死信，不再自动重试；管理员修复后可以重新投递，或写明原因丢弃（都留审计）。
消息里不放敏感数据：payload 入库前统一脱敏，消息只带业务对象的编号，消费者按编号回查。
"""
import json
import os
import uuid
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.exc import IntegrityError

from models.init_db import ConsumerInbox, ConsumerRetry, DeadLetter, OutboxEvent, SessionLocal
from service.observability import context as trace_context
from service.observability.redact import redact, redact_text
from utils.logger_handler import get_logger
from utils.timeutil import utcnow

logger = get_logger("outbox")

PRODUCER = os.getenv("SERVICE_NAME", "agent-service")
LEASE_SECONDS = int(os.getenv("OUTBOX_LEASE_SECONDS", "120"))
MAX_PUBLISH_BACKOFF = 300
FAIL_FAST_AFTER = int(os.getenv("OUTBOX_FAIL_FAST_AFTER", "3"))
PUBLISH_LEASE_SECONDS = int(os.getenv("OUTBOX_PUBLISH_LEASE_SECONDS", "60"))     # 发布一批事件的租约：进程崩溃后多久会被别的发布器接手
MAX_PAYLOAD_BYTES = 16 * 1024

# Topic 目录（与方案一致）。新增 Topic 在这里登记，emit 会拒绝未登记的名字，避免拼写错误悄悄产生没人消费的消息。
TOPICS = {
    "platform.issue.v1", "platform.audit.v1", "enterprise.business-event.v1", "agent.run-event.v1",
    "automation.job.v1", "notification.command.v1",
}


class UnknownTopic(ValueError):
    pass


def backoff_seconds(attempts: int) -> int:
    return min(MAX_PUBLISH_BACKOFF, 2 ** min(attempts, 9))


def emit(db, *, topic: str, event_type: str, aggregate_type: str, aggregate_id: Any, payload: Dict[str, Any],
         key: Optional[str] = None, organization_id: Optional[int] = None, department_id: Optional[int] = None,
         trace_id: Optional[str] = None, schema_version: int = 1) -> str:
    """在调用方的事务里登记一个事件（不 commit）。返回 event_id。"""
    if topic not in TOPICS:
        raise UnknownTopic(f"未登记的 Topic: {topic}")
    body = json.dumps(redact(payload), ensure_ascii=False)
    if len(body.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("事件内容过大：消息里只放业务对象的编号，不要放正文")
    event_id = str(uuid.uuid4())
    db.add(OutboxEvent(event_id=event_id, topic=topic, event_key=None if key is None else str(key)[:64], event_type=event_type,
                       schema_version=schema_version, aggregate_type=aggregate_type, aggregate_id=str(aggregate_id)[:64],
                       organization_id=organization_id, department_id=department_id,
                       trace_id=trace_id or trace_context.current_trace_id(), producer=PRODUCER, payload_json=body,
                       created_at=utcnow()))   # next_publish_at 留空 = 立即可发布（MySQL 的 DATETIME 会四舍五入到秒，写“现在”可能比读取时刻还晚）
    return event_id


def envelope(row: OutboxEvent) -> Dict[str, Any]:
    return {"event_id": row.event_id, "event_type": row.event_type, "schema_version": row.schema_version,
            "occurred_at": row.created_at.isoformat() + "Z", "organization_id": row.organization_id,
            "department_id": row.department_id, "aggregate_type": row.aggregate_type, "aggregate_id": row.aggregate_id,
            "trace_id": row.trace_id, "producer": row.producer, "topic": row.topic, "key": row.event_key,
            "payload": json.loads(row.payload_json)}


# ---------------------------------------------------------------- 发布

def publish_pending(transport: Callable[[Dict[str, Any]], None], limit: int = 100) -> Dict[str, int]:
    """把到期的未发布事件送到传输层。多个进程同时跑也安全。

    **不能在调用传输层期间持有数据库事务**（真实 Kafka 联调时发现的问题）：以前是 `SELECT … FOR UPDATE` 之后在同一个事务里逐条调用 Kafka，
    Broker 变慢或刚恢复时这个事务要开几十秒，期间它持有的间隙锁会让业务事务里的 `emit()` 插入超时失败——Kafka 的故障反过来拖垮业务写入。
    现在分三步：① 很短的事务里领取一批事件并给它们打上发布租约（next_publish_at = 现在 + 租约）；② 事务外逐条调用传输层；
    ③ 每条结果用各自的短事务回写。进程在②崩溃：租约到期后别的发布器会重新发布（至少一次，消费者的收件箱挡重复）。"""
    claimed = []
    db = SessionLocal()
    try:
        now = utcnow()
        rows = db.execute(select(OutboxEvent).where(OutboxEvent.published_at.is_(None), or_(OutboxEvent.next_publish_at.is_(None), OutboxEvent.next_publish_at <= now))
                          .order_by(OutboxEvent.id).limit(limit).with_for_update(skip_locked=True)).scalars().all()
        for row in rows:
            claimed.append((row.id, row.event_id, row.publish_attempts, envelope(row)))
            row.next_publish_at = now + timedelta(seconds=PUBLISH_LEASE_SECONDS)
        db.commit()
    finally:
        db.close()

    sent = failed = streak = 0
    for index, (row_id, event_id, attempts, event) in enumerate(claimed):
        if streak >= FAIL_FAST_AFTER:
            # 传输层连续失败：多半是 Broker 整体不可用，本轮不再逐条等超时；剩下的事件释放租约（不增加失败次数），下一轮再试
            _release([c[0] for c in claimed[index:]])
            break
        try:
            transport(event)
        except Exception as exc:  # noqa: BLE001 —— 传输层故障：退避重试，事件仍在库里
            message = redact_text(f"{type(exc).__name__}: {exc}")[:300]
            _settle(row_id, ok=False, attempts=attempts + 1, error=message)
            failed += 1
            streak += 1
            logger.warning("事件发布失败 %s（第 %s 次）: %s", event_id, attempts + 1, message)
        else:
            _settle(row_id, ok=True, attempts=attempts)
            sent += 1
            streak = 0
    return {"sent": sent, "failed": failed}


def _settle(row_id: int, *, ok: bool, attempts: int, error: Optional[str] = None) -> None:
    db = SessionLocal()
    try:
        if ok:
            db.execute(update(OutboxEvent).where(OutboxEvent.id == row_id, OutboxEvent.published_at.is_(None))
                       .values(published_at=utcnow(), last_error=None, next_publish_at=None))
        else:
            db.execute(update(OutboxEvent).where(OutboxEvent.id == row_id, OutboxEvent.published_at.is_(None))
                       .values(publish_attempts=attempts, last_error=error, next_publish_at=utcnow() + timedelta(seconds=backoff_seconds(attempts))))
        db.commit()
    finally:
        db.close()


def _release(row_ids: List[int]) -> None:
    if not row_ids:
        return
    db = SessionLocal()
    try:
        db.execute(update(OutboxEvent).where(OutboxEvent.id.in_(row_ids), OutboxEvent.published_at.is_(None)).values(next_publish_at=None))
        db.commit()
    finally:
        db.close()


def db_transport(_: Dict[str, Any]) -> None:
    """进程内/数据库模式：事件已经在发件箱表里，“发布”只是把它标记成可被消费者读取。"""
    return None


class KafkaTransport:
    """Kafka 传输（可选，需要 confluent-kafka，设置 KAFKA_BOOTSTRAP_SERVERS 启用）。

    已在真实 Kafka（KRaft）Broker 上验证：投递、Broker 停机后重试不丢、恢复后继续（scripts/e2e_kafka.py）。
    消息键用 aggregate 的 event_key（同一对象的事件进同一分区，保证有序），头里带 traceparent / event_id / schema_version / producer。
    """

    def __init__(self, producer=None, bootstrap: Optional[str] = None, ensure_topics: bool = True):
        self.bootstrap = bootstrap or os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
        self._topics_ready = not ensure_topics or producer is not None      # 传入假 producer（测试）时不去连 Broker 建 Topic
        if producer is None:
            from confluent_kafka import Producer
            producer = Producer({"bootstrap.servers": self.bootstrap, "enable.idempotence": True, "acks": "all",
                                 "message.timeout.ms": 15000, "socket.timeout.ms": 10000})
        self.producer = producer

    def _ensure_topics(self) -> None:
        if not self._topics_ready:
            from service.events import kafka_bus
            kafka_bus.ensure_topics(sorted(TOPICS), self.bootstrap)
            self._topics_ready = True

    def __call__(self, event: Dict[str, Any]) -> None:
        self._ensure_topics()
        errors: List[Any] = []
        headers = [("traceparent", trace_context.traceparent_for(event.get("trace_id")).encode()),
                   ("event_id", event["event_id"].encode()), ("schema_version", str(event["schema_version"]).encode()),
                   ("producer", str(event["producer"]).encode())]
        self.producer.produce(event["topic"], key=(event.get("key") or event["aggregate_id"]).encode(),
                              value=json.dumps(event, ensure_ascii=False).encode("utf-8"), headers=headers,
                              on_delivery=lambda err, _msg: errors.append(err) if err else None)
        remaining = self.producer.flush(10)
        if errors:
            raise RuntimeError(f"Kafka 投递失败: {errors[0]}")
        if remaining:
            # 10 秒内没有收到 Broker 的确认：不能当成发送成功（否则发件箱会标记已发布，之后这条消息又投递失败 = 丢事件）；
            # 消息可能已经在 producer 队列里稍后送达，所以这里抛错重试，重复由消费者的收件箱挡住（至少一次）。
            raise RuntimeError("Kafka 投递超时：Broker 没有确认")


# ---------------------------------------------------------------- 消费

def fetch_deliverable(consumer: str, topics: List[str], limit: int = 20, retry_only: bool = False) -> List[Dict[str, Any]]:
    """该消费者还没成功处理、没进死信、没被别人持有租约的已发布事件（按发布顺序）。

    retry_only=True（Kafka 模式的重试扫描）：只要“已经有过处理记录（失败退避 / 租约）且已到期”的事件——
    首次投递由 Kafka 负责，这里不能抢着处理 Kafka 还没送到的事件。"""
    db = SessionLocal()
    try:
        now = utcnow()
        done = exists().where(and_(ConsumerInbox.event_id == OutboxEvent.event_id, ConsumerInbox.consumer == consumer))
        parked = exists().where(and_(DeadLetter.event_id == OutboxEvent.event_id, DeadLetter.consumer == consumer,
                                     DeadLetter.status.in_(("pending", "discarded"))))
        waiting = exists().where(and_(ConsumerRetry.event_id == OutboxEvent.event_id, ConsumerRetry.consumer == consumer,
                                      ConsumerRetry.next_attempt_at > now))
        due_retry = exists().where(and_(ConsumerRetry.event_id == OutboxEvent.event_id, ConsumerRetry.consumer == consumer,
                                        ConsumerRetry.next_attempt_at <= now))
        rows = db.execute(select(OutboxEvent).where(OutboxEvent.published_at.is_not(None), OutboxEvent.topic.in_(topics),
                                                    ~done, ~parked, due_retry if retry_only else ~waiting).order_by(OutboxEvent.id).limit(limit)).scalars().all()
        return [envelope(r) for r in rows]
    finally:
        db.close()


def claim(consumer: str, event_id: str, lease_seconds: int = LEASE_SECONDS) -> Optional[int]:
    """领取一个事件的处理权（租约）。成功返回此前已失败的次数，别人持有租约/已处理返回 None。

    先 INSERT、冲突再 UPDATE（带“租约已过期”条件）：不对不存在的行加锁（那会在并发领取时产生间隙锁死锁）。

    拿到租约之后必须**再确认一次收件箱**（真实 Kafka 多实例联调时发现的竞态）：开头的“已处理”检查和后面的插入不是原子的——
    另一个实例恰好在两步之间处理完成（写收件箱 + 删租约），我们的 INSERT 就会成功，同一个事件被处理第二次。
    mark_done 在同一个事务里“写收件箱 + 删租约”，所以只要我们的租约插入成功，要么对方还没做完（我们看不到收件箱，但它仍持有它自己的租约→
    我们的 INSERT 会冲突），要么对方已经提交（我们此刻一定能在收件箱里看到它）。"""
    db = SessionLocal()
    try:
        now = utcnow()
        if db.execute(select(ConsumerInbox.id).where(ConsumerInbox.event_id == event_id, ConsumerInbox.consumer == consumer)).first():
            return None
        lease_until = now + timedelta(seconds=lease_seconds)
        previous = None
        try:
            db.add(ConsumerRetry(event_id=event_id, consumer=consumer, attempts=0, next_attempt_at=lease_until))
            db.commit()
            previous = 0
        except IntegrityError:
            db.rollback()
        if previous is None:
            taken = db.execute(update(ConsumerRetry).where(ConsumerRetry.event_id == event_id, ConsumerRetry.consumer == consumer,
                                                           ConsumerRetry.next_attempt_at <= now).values(next_attempt_at=lease_until))
            db.commit()
            if taken.rowcount != 1:
                return None
            previous = db.execute(select(ConsumerRetry.attempts).where(ConsumerRetry.event_id == event_id, ConsumerRetry.consumer == consumer)).scalar() or 0
        if db.execute(select(ConsumerInbox.id).where(ConsumerInbox.event_id == event_id, ConsumerInbox.consumer == consumer)).first():
            db.execute(ConsumerRetry.__table__.delete().where(ConsumerRetry.event_id == event_id, ConsumerRetry.consumer == consumer))
            db.commit()
            return None           # 在我们领取之前已经被别人处理完了：把刚插入的租约撤掉
        return previous
    finally:
        db.close()


def mark_done(consumer: str, event_id: str) -> None:
    db = SessionLocal()
    try:
        try:
            db.add(ConsumerInbox(event_id=event_id, consumer=consumer, processed_at=utcnow()))
            db.flush()
        except IntegrityError:
            db.rollback()   # 已经记过了（重复投递）
        db.execute(ConsumerRetry.__table__.delete().where(ConsumerRetry.event_id == event_id, ConsumerRetry.consumer == consumer))
        db.commit()
    finally:
        db.close()


def mark_failed(consumer: str, event: Dict[str, Any], error: str, max_attempts: int) -> bool:
    """记录一次失败。返回 True 表示已达上限进入死信。"""
    db = SessionLocal()
    try:
        message = redact_text(error)[:300]
        row = db.execute(select(ConsumerRetry).where(ConsumerRetry.event_id == event["event_id"], ConsumerRetry.consumer == consumer)
                         .with_for_update()).scalar_one_or_none()
        attempts = (row.attempts if row else 0) + 1
        if attempts >= max_attempts:
            db.add(DeadLetter(event_id=event["event_id"], consumer=consumer, topic=event["topic"], event_type=event["event_type"],
                              trace_id=event.get("trace_id"), payload_json=json.dumps(event, ensure_ascii=False), error=message,
                              attempts=attempts, status="pending", created_at=utcnow()))
            if row:
                db.delete(row)
            db.commit()
            logger.error("事件进入死信 %s consumer=%s: %s", event["event_id"], consumer, message)
            return True
        wait = utcnow() + timedelta(seconds=backoff_seconds(attempts))
        if row:
            row.attempts, row.next_attempt_at, row.last_error = attempts, wait, message
        else:
            db.add(ConsumerRetry(event_id=event["event_id"], consumer=consumer, attempts=attempts, next_attempt_at=wait, last_error=message))
        db.commit()
        return False
    finally:
        db.close()


# ---------------------------------------------------------------- 死信管理

def _dead_row(d: DeadLetter) -> Dict[str, Any]:
    return {"id": d.id, "event_id": d.event_id, "consumer": d.consumer, "topic": d.topic, "event_type": d.event_type, "trace_id": d.trace_id,
            "error": d.error, "attempts": d.attempts, "status": d.status, "discard_reason": d.discard_reason, "handled_by": d.handled_by,
            "handled_at": d.handled_at and d.handled_at.isoformat() + "Z", "created_at": d.created_at.isoformat() + "Z"}


def list_dead_letters(db, status: Optional[str] = "pending", limit: int = 100) -> List[Dict[str, Any]]:
    query = select(DeadLetter)
    if status:
        query = query.where(DeadLetter.status == status)
    return [_dead_row(d) for d in db.execute(query.order_by(DeadLetter.id.desc()).limit(max(1, min(limit, 300)))).scalars().all()]


def get_dead_letter(db, dead_id: int) -> Dict[str, Any]:
    from service.exceptions import NotFound
    d = db.get(DeadLetter, dead_id)
    if d is None:
        raise NotFound("死信不存在")
    data = _dead_row(d)
    data["event"] = json.loads(d.payload_json)
    return data


def redeliver(db, dead_id: int, actor_id: int) -> Dict[str, Any]:
    """重新投递：清掉重试状态，让消费者重新领取这个事件（失败次数从头算）。"""
    from service.exceptions import Conflict, NotFound
    d = db.execute(select(DeadLetter).where(DeadLetter.id == dead_id).with_for_update()).scalar_one_or_none()
    if d is None:
        raise NotFound("死信不存在")
    if d.status != "pending":
        raise Conflict("这条死信已经处理过了")
    d.status, d.handled_by, d.handled_at = "redelivered", actor_id, utcnow()
    db.execute(ConsumerRetry.__table__.delete().where(ConsumerRetry.event_id == d.event_id, ConsumerRetry.consumer == d.consumer))
    # 清掉“已发布”标记，让发布器把事件重新发布一次：数据库模式下等于重新可消费；Kafka 模式下事件重新进 Topic，消费者会再收到。
    # 其他消费者再收到同一事件时，由各自的收件箱挡住，不会重复处理。
    db.execute(update(OutboxEvent).where(OutboxEvent.event_id == d.event_id).values(published_at=None, next_publish_at=None, publish_attempts=0))
    db.commit()
    return _dead_row(d)


def discard(db, dead_id: int, actor_id: int, reason: str) -> Dict[str, Any]:
    from service.exceptions import Conflict, InvalidInput, NotFound
    if len((reason or "").strip()) < 2:
        raise InvalidInput("丢弃死信必须写明原因")
    d = db.execute(select(DeadLetter).where(DeadLetter.id == dead_id).with_for_update()).scalar_one_or_none()
    if d is None:
        raise NotFound("死信不存在")
    if d.status != "pending":
        raise Conflict("这条死信已经处理过了")
    d.status, d.discard_reason, d.handled_by, d.handled_at = "discarded", reason.strip()[:500], actor_id, utcnow()
    db.commit()
    return _dead_row(d)


def stats(db) -> Dict[str, Any]:
    from sqlalchemy import func
    unpublished = db.execute(select(func.count()).where(OutboxEvent.published_at.is_(None))).scalar() or 0
    oldest = db.execute(select(func.min(OutboxEvent.created_at)).where(OutboxEvent.published_at.is_(None))).scalar()
    dead = db.execute(select(func.count()).where(DeadLetter.status == "pending")).scalar() or 0
    retrying = db.execute(select(func.count()).where(ConsumerRetry.attempts > 0)).scalar() or 0
    return {"unpublished": int(unpublished), "oldest_unpublished_seconds": int((utcnow() - oldest).total_seconds()) if oldest else 0,
            "dead_letters": int(dead), "retrying": int(retrying)}


def purge_old(days: int = 7) -> int:
    """清理已发布超过 N 天的事件及其收件箱/重试记录（有待处理死信的事件保留）。事件是传输用的，不是长期存档；审计另有专门的表。"""
    from sqlalchemy import delete
    db = SessionLocal()
    try:
        cutoff = utcnow() - timedelta(days=days)
        old = select(OutboxEvent.event_id).where(OutboxEvent.published_at.is_not(None), OutboxEvent.published_at < cutoff)
        parked = select(DeadLetter.event_id).where(DeadLetter.status == "pending")
        ids = [r[0] for r in db.execute(old.where(OutboxEvent.event_id.not_in(parked)).limit(1000)).all()]
        if not ids:
            return 0
        db.execute(delete(ConsumerInbox).where(ConsumerInbox.event_id.in_(ids)))
        db.execute(delete(ConsumerRetry).where(ConsumerRetry.event_id.in_(ids)))
        db.execute(delete(OutboxEvent).where(OutboxEvent.event_id.in_(ids)))
        db.commit()
        return len(ids)
    finally:
        db.close()
