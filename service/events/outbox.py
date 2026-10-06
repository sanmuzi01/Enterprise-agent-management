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
    """把到期的未发布事件送到传输层。多个进程同时跑也安全（行锁 + SKIP LOCKED）。"""
    db = SessionLocal()
    sent = failed = 0
    try:
        rows = db.execute(select(OutboxEvent).where(OutboxEvent.published_at.is_(None), or_(OutboxEvent.next_publish_at.is_(None), OutboxEvent.next_publish_at <= utcnow()))
                          .order_by(OutboxEvent.id).limit(limit).with_for_update(skip_locked=True)).scalars().all()
        for row in rows:
            try:
                transport(envelope(row))
                row.published_at, row.last_error = utcnow(), None
                sent += 1
            except Exception as exc:  # noqa: BLE001 —— 传输层故障：退避重试，事件仍在库里
                row.publish_attempts += 1
                row.last_error = redact_text(f"{type(exc).__name__}: {exc}")[:300]
                row.next_publish_at = utcnow() + timedelta(seconds=backoff_seconds(row.publish_attempts))
                failed += 1
                logger.warning("事件发布失败 %s（第 %s 次）: %s", row.event_id, row.publish_attempts, row.last_error)
        db.commit()
    finally:
        db.close()
    return {"sent": sent, "failed": failed}


def db_transport(_: Dict[str, Any]) -> None:
    """进程内/数据库模式：事件已经在发件箱表里，“发布”只是把它标记成可被消费者读取。"""
    return None


class KafkaTransport:
    """Kafka 传输（可选，需要 confluent-kafka，设置 KAFKA_BOOTSTRAP_SERVERS 启用）。

    注意：这个适配器只用假的 producer 测过消息格式和头；**没有对真实 Kafka broker 验证过**。
    消息键用 aggregate 的 event_key（同一对象的事件进同一分区，保证有序），头里带 traceparent / event_id / schema_version / producer。
    """

    def __init__(self, producer=None, bootstrap: Optional[str] = None):
        if producer is None:
            from confluent_kafka import Producer
            producer = Producer({"bootstrap.servers": bootstrap or os.environ["KAFKA_BOOTSTRAP_SERVERS"], "enable.idempotence": True,
                                 "acks": "all"})
        self.producer = producer

    def __call__(self, event: Dict[str, Any]) -> None:
        errors: List[Any] = []
        headers = [("traceparent", trace_context.traceparent_for(event.get("trace_id")).encode()),
                   ("event_id", event["event_id"].encode()), ("schema_version", str(event["schema_version"]).encode()),
                   ("producer", str(event["producer"]).encode())]
        self.producer.produce(event["topic"], key=(event.get("key") or event["aggregate_id"]).encode(),
                              value=json.dumps(event, ensure_ascii=False).encode("utf-8"), headers=headers,
                              on_delivery=lambda err, _msg: errors.append(err) if err else None)
        self.producer.flush(10)
        if errors:
            raise RuntimeError(f"Kafka 投递失败: {errors[0]}")


# ---------------------------------------------------------------- 消费

def fetch_deliverable(consumer: str, topics: List[str], limit: int = 20) -> List[Dict[str, Any]]:
    """该消费者还没成功处理、没进死信、没被别人持有租约的已发布事件（按发布顺序）。"""
    db = SessionLocal()
    try:
        now = utcnow()
        done = exists().where(and_(ConsumerInbox.event_id == OutboxEvent.event_id, ConsumerInbox.consumer == consumer))
        parked = exists().where(and_(DeadLetter.event_id == OutboxEvent.event_id, DeadLetter.consumer == consumer,
                                     DeadLetter.status.in_(("pending", "discarded"))))
        waiting = exists().where(and_(ConsumerRetry.event_id == OutboxEvent.event_id, ConsumerRetry.consumer == consumer,
                                      ConsumerRetry.next_attempt_at > now))
        rows = db.execute(select(OutboxEvent).where(OutboxEvent.published_at.is_not(None), OutboxEvent.topic.in_(topics),
                                                    ~done, ~parked, ~waiting).order_by(OutboxEvent.id).limit(limit)).scalars().all()
        return [envelope(r) for r in rows]
    finally:
        db.close()


def claim(consumer: str, event_id: str, lease_seconds: int = LEASE_SECONDS) -> Optional[int]:
    """领取一个事件的处理权（租约）。成功返回此前已失败的次数，别人持有租约/已处理返回 None。

    先 INSERT、冲突再 UPDATE（带“租约已过期”条件）：不对不存在的行加锁（那会在并发领取时产生间隙锁死锁）。"""
    db = SessionLocal()
    try:
        now = utcnow()
        if db.execute(select(ConsumerInbox.id).where(ConsumerInbox.event_id == event_id, ConsumerInbox.consumer == consumer)).first():
            return None
        lease_until = now + timedelta(seconds=lease_seconds)
        try:
            db.add(ConsumerRetry(event_id=event_id, consumer=consumer, attempts=0, next_attempt_at=lease_until))
            db.commit()
            return 0
        except IntegrityError:
            db.rollback()
        taken = db.execute(update(ConsumerRetry).where(ConsumerRetry.event_id == event_id, ConsumerRetry.consumer == consumer,
                                                       ConsumerRetry.next_attempt_at <= now).values(next_attempt_at=lease_until))
        db.commit()
        if taken.rowcount != 1:
            return None
        return db.execute(select(ConsumerRetry.attempts).where(ConsumerRetry.event_id == event_id, ConsumerRetry.consumer == consumer)).scalar() or 0
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
