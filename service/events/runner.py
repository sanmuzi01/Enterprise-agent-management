"""事件消费者注册表与运行器：发布 → 各消费者领取 → 处理 → 收件箱 / 重试 / 死信。

运行在 API 进程的事件循环里（lifespan 启动的后台任务）；同步的数据库操作放线程池，异步的处理函数直接 await。
`run_cycle()` 是一轮完整的发布 + 消费，测试和故障演练直接调用它；`OUTBOX_RUNNER=0` 可关闭自动运行（此时事件只会堆积在发件箱里，
不会丢，下次打开后继续处理）。
"""
import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from service.events import outbox
from service.observability import context as trace_context
from utils.logger_handler import get_logger

logger = get_logger("event_runner")

Handler = Callable[[Dict[str, Any]], Awaitable[None]]


class TryAgain(Exception):
    """处理函数认为现在还不能处理（比如对象仍被另一个处理者占用）：记一次失败、稍后重试，而不是当成完成。"""


@dataclass
class Consumer:
    name: str
    topics: List[str]
    handler: Handler
    max_attempts: int = 5
    concurrency: int = 1
    timeout_seconds: float = 120
    on_dead: Optional[Callable[[Dict[str, Any], str], Awaitable[None]]] = None


REGISTRY: Dict[str, Consumer] = {}


def register(consumer: Consumer) -> Consumer:
    REGISTRY[consumer.name] = consumer
    return consumer


def consumer(name: str, topics: List[str], **kwargs):
    def wrap(fn: Handler) -> Handler:
        register(Consumer(name=name, topics=topics, handler=fn, **kwargs))
        return fn
    return wrap


async def _deliver(c: Consumer, event: Dict[str, Any], stats: Dict[str, int]) -> str:
    """处理一个事件，返回结果：done / skipped（重复或别人持有租约）/ retry / dead。"""
    previous = await asyncio.to_thread(outbox.claim, c.name, event["event_id"])
    if previous is None:
        stats["skipped"] += 1            # 已处理过（重复投递）或别的进程正在处理
        return "skipped"
    token = trace_context.set_trace(event.get("trace_id") or trace_context.new_trace_id())   # 消费者的日志和问题记录沿用事件的 trace_id
    try:
        await asyncio.wait_for(c.handler(event), c.timeout_seconds)
        await asyncio.to_thread(outbox.mark_done, c.name, event["event_id"])
        stats["done"] += 1
        return "done"
    except Exception as exc:  # noqa: BLE001
        dead = await asyncio.to_thread(outbox.mark_failed, c.name, event, f"{type(exc).__name__}: {exc}", c.max_attempts)
        stats["dead" if dead else "retry"] += 1
        if not isinstance(exc, TryAgain):
            logger.warning("消费者 %s 处理事件 %s 失败: %s", c.name, event["event_id"], exc)
        if dead:
            await _report_dead(c, event, exc)
        return "dead" if dead else "retry"
    finally:
        trace_context.reset_trace(token)


async def _report_dead(c: Consumer, event: Dict[str, Any], exc: BaseException) -> None:
    """重试用尽：进问题中心（同一个消费者的同一类失败聚合成一条），并让消费者收尾（比如把批量材料标成失败）。"""
    from service.observability.issues import record_occurrence
    await asyncio.to_thread(lambda: record_occurrence(
        error_code="KAFKA_CONSUME_FAILED", http_status=500, operation=f"消费者 {c.name} · {event['event_type']}", exc=exc,
        message=f"事件 {event['event_id']} 重试 {c.max_attempts} 次仍失败：{exc}", trace_id=event.get("trace_id"),
        department_id=event.get("department_id"), extra={"topic": event["topic"], "event_id": event["event_id"]},
        resource_type=event.get("aggregate_type"), resource_id=event.get("aggregate_id")))
    if kafka_mode():
        from service.events import kafka_bus
        await asyncio.to_thread(kafka_bus.publish_dead, c.name, event, f"{type(exc).__name__}: {exc}", c.max_attempts)
    if c.on_dead:
        try:
            await c.on_dead(event, f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            logger.exception("消费者 %s 的死信收尾失败", c.name)


async def run_consumers_once(limit: int = 20, retry_only: bool = False) -> Dict[str, int]:
    """数据库模式：领取并处理全部可处理的事件。retry_only（Kafka 模式）：只处理已到期的重试 / 过期租约，首次投递由 Kafka 消费者负责。"""
    stats = {"done": 0, "retry": 0, "dead": 0, "skipped": 0}
    for c in list(REGISTRY.values()):
        events = await asyncio.to_thread(outbox.fetch_deliverable, c.name, c.topics, limit, retry_only)
        semaphore = asyncio.Semaphore(max(1, c.concurrency))

        async def guarded(event, c=c, semaphore=semaphore):
            async with semaphore:
                await _deliver(c, event, stats)
        await asyncio.gather(*(guarded(e) for e in events))
    return stats


def default_transport():
    if os.getenv("KAFKA_BOOTSTRAP_SERVERS"):
        return outbox.KafkaTransport()
    return outbox.db_transport


def kafka_mode() -> bool:
    return bool(os.getenv("KAFKA_BOOTSTRAP_SERVERS"))


async def run_cycle(transport=None, retry_only: Optional[bool] = None) -> Dict[str, Any]:
    published = await asyncio.to_thread(outbox.publish_pending, transport or default_transport())
    consumed = await run_consumers_once(retry_only=kafka_mode() if retry_only is None else retry_only)
    return {"published": published, "consumed": consumed}


class Runner:
    """后台循环。启动时不需要等待：第一轮会处理上次进程退出时遗留的事件（重启后继续）。"""

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self.task: Optional[asyncio.Task] = None
        self.kafka_tasks: List[asyncio.Task] = []
        self._stop = asyncio.Event()

    async def _loop(self) -> None:
        last_purge = 0.0
        while not self._stop.is_set():
            try:
                if time.monotonic() - last_purge > 3600:   # 每小时清理一次已发布很久的事件
                    last_purge = time.monotonic()
                    await asyncio.to_thread(outbox.purge_old, int(os.getenv("OUTBOX_RETENTION_DAYS", "7")))
                result = await run_cycle()
                busy = result["published"]["sent"] or result["consumed"]["done"] or result["consumed"]["retry"]
            except Exception:  # noqa: BLE001 —— 一轮失败不能让整个运行器停掉
                logger.exception("事件运行器本轮出错")
                busy = False
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=0.2 if busy else self.interval)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        self.task = asyncio.create_task(self._loop(), name="event-runner")
        if kafka_mode():
            from service.events import kafka_bus
            for c in list(REGISTRY.values()):
                self.kafka_tasks.append(asyncio.create_task(self._kafka_consumer(c, kafka_bus), name=f"kafka-consumer-{c.name}"))

    async def _kafka_consumer(self, c: Consumer, kafka_bus) -> None:
        """一个消费者的 Kafka 循环：Broker 不可用时不退出，等恢复后继续（创建 Topic、订阅都放在重试里）。"""
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(kafka_bus.ensure_topics, sorted(outbox.TOPICS))
                await kafka_bus.consume_loop(c, _deliver, self._stop)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("Kafka 消费者 %s 异常退出，5 秒后重连", c.name, exc_info=True)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass

    async def stop(self) -> None:
        self._stop.set()
        tasks = [t for t in [self.task, *self.kafka_tasks] if t]
        for task in tasks:
            try:
                await asyncio.wait_for(task, 15)
            except Exception:  # noqa: BLE001
                task.cancel()


def enabled() -> bool:
    return os.getenv("OUTBOX_RUNNER", "1").strip().lower() not in ("0", "false", "no") and os.getenv("APP_ENV") != "test"
