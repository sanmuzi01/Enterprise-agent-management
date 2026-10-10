"""scripts/e2e_kafka.py 启动的消费者进程（可以被杀掉再重启，用来验证崩溃恢复和多实例消费）。

和应用里的 Runner 用的是同一套代码（发布器 + Kafka 消费者组 + 重试扫描），只是注册的是测试用的消费者：
每次处理都往日志文件里追加一行（start / done），好让主脚本数清楚“每个事件被处理了几次、被谁处理”。
行为由事件的 payload 控制：fail_times（前 N 次失败）、always_fail（除非存在 fixed.flag）、sleep（第一次处理时睡很久，给“杀进程”留出窗口）。
环境变量：KAFKA_BOOTSTRAP_SERVERS、KAFKA_GROUP_PREFIX、E2E_TOPIC、E2E_LOG、E2E_FLAG。
"""
import asyncio
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OUTBOX_LEASE_SECONDS", "3")          # 必须在导入 outbox 之前：崩溃后租约 3 秒就过期，便于验证

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from service.events import outbox, runner  # noqa: E402

TOPIC = os.environ["E2E_TOPIC"]
LOG = pathlib.Path(os.environ["E2E_LOG"])
FLAG = pathlib.Path(os.environ["E2E_FLAG"])
outbox.TOPICS.add(TOPIC)


def log(kind: str, event: dict) -> None:
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": kind, "event_id": event["event_id"], "pid": os.getpid(), "t": time.time(), "seq": event["payload"].get("seq"),
                                 "agg": event["aggregate_id"], "tag": event["payload"].get("tag")}) + "\n")


def starts_of(event_id: str) -> int:
    if not LOG.exists():
        return 0
    return sum(1 for line in LOG.read_text(encoding="utf-8").splitlines() if line and json.loads(line)["event_id"] == event_id and json.loads(line)["kind"] == "start")


async def handle(event: dict) -> None:
    payload = event["payload"]
    before = starts_of(event["event_id"])
    log("start", event)
    if payload.get("sleep") and before == 0:
        await asyncio.sleep(payload["sleep"])                # 第一次处理时卡住：主脚本在这期间杀掉本进程
    if payload.get("fail_times") and before < payload["fail_times"]:
        raise RuntimeError(f"模拟瞬时故障（第 {before + 1} 次）")
    if payload.get("always_fail") and not FLAG.exists():
        raise RuntimeError("模拟持续故障：依赖的数据不存在")
    log("done", event)


async def main() -> None:
    if os.getenv("E2E_DUMP"):                                  # 排查卡死：每 20 秒把所有线程的调用栈写到文件
        import faulthandler
        faulthandler.dump_traceback_later(20, repeat=True, file=open(os.environ["E2E_DUMP"], "w"))
    runner.REGISTRY.clear()
    runner.register(runner.Consumer(name="e2e_main", topics=[TOPIC], handler=handle, max_attempts=3, concurrency=4, timeout_seconds=40))
    loop = runner.Runner(interval=0.3)
    loop.start()
    print("worker ready", os.getpid(), flush=True)
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
