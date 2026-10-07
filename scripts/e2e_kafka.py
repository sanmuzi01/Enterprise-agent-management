"""Kafka 事件总线的真实联调：真实 Kafka（KRaft）Broker + 真实 MySQL + 真实的消费者进程（可被杀掉、可多实例）。

验证（“Kafka 负责投递，数据库负责幂等与重试状态”）：
  1. 正常路径：事件经发件箱 → Kafka → 消费者组，每个事件恰好处理一次，同一聚合对象的事件保持顺序；
  2. 重复投递：同一条消息再发两遍，处理函数不会再被调用（收件箱挡住）；
  3. 重试：处理函数前两次失败 → 按退避重试 → 第三次成功；
  4. 死信：持续失败 → 重试用尽 → 数据库死信 + 死信 Topic（带原因与原始位置）+ 问题中心；修复后重新投递 → 恰好处理一次；
  5. Broker 宕机再恢复：宕机期间事件留在发件箱（不丢），发布器快速失败不堵死，恢复后全部送达，无重复处理；
  6. 消费者进程崩溃：处理到一半被杀掉，重启后事件重新送达，租约到期后只生效一次；
  7. 多实例：两个消费者进程同一个组，分区被分摊，每个事件恰好处理一次。

前置：`docker compose -f deploy/observability/docker-compose.yml up -d`（Kafka 在 127.0.0.1:9092）；MySQL 已启动。
用法：.venv\\Scripts\\python.exe scripts\\e2e_kafka.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RUN = uuid.uuid4().hex[:8]
os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "127.0.0.1:9092"
os.environ["KAFKA_GROUP_PREFIX"] = f"e2e{RUN}"
os.environ["CONSOLE_LOG_LEVEL"] = "CRITICAL"
TOPIC = f"e2e.kafka.{RUN}.v1"
os.environ["E2E_TOPIC"] = TOPIC
WORK = pathlib.Path(tempfile.mkdtemp(prefix="e2e_kafka_"))
LOG, FLAG = WORK / "handled.jsonl", WORK / "fixed.flag"
os.environ["E2E_LOG"], os.environ["E2E_FLAG"] = str(LOG), str(FLAG)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "127.0.0.1:9092"

from sqlalchemy import text  # noqa: E402

from models.init_db import SessionLocal  # noqa: E402
from service.events import kafka_bus, outbox  # noqa: E402

outbox.TOPICS.add(TOPIC)
PASSED, FAILED = [], []
WORKERS = []


def check(condition, message):
    (PASSED if condition else FAILED).append(message)
    print(f"  {'PASS' if condition else 'FAIL'} {message}")


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)


def start_worker():
    """消费者进程的输出写到文件，不能接管道：没人读的管道写满后，进程里的日志写入会阻塞（联调时真的把发布线程卡死过一次）。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    out_path = WORK / f"worker-{len(WORKERS) + 1}.log"
    handle = open(out_path, "w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "kafka_worker_probe.py")], env=env, stdout=handle, stderr=subprocess.STDOUT, text=True)
    WORKERS.append(process)
    deadline = time.time() + 40
    while time.time() < deadline:
        if "worker ready" in out_path.read_text(encoding="utf-8", errors="replace"):
            return process
        if process.poll() is not None:
            raise RuntimeError("消费者进程启动失败：" + out_path.read_text(encoding="utf-8", errors="replace")[-500:])
        time.sleep(0.3)
    raise RuntimeError("消费者进程没有在 40 秒内就绪")


def kill_worker(process):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
    process.wait(timeout=10)


def lines():
    if not LOG.exists():
        return []
    return [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]


def done_ids():
    return [l["event_id"] for l in lines() if l["kind"] == "done"]


def wait_for(predicate, seconds, interval=0.5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def emit(count, *, prefix, aggregates=6, **payload):
    """在一个事务里登记 count 个事件（和业务数据同事务的写法一致），返回 event_id 列表。"""
    db = SessionLocal()
    ids = []
    try:
        for i in range(count):
            ids.append(outbox.emit(db, topic=TOPIC, event_type="e2e.test", aggregate_type="e2e", aggregate_id=f"{prefix}-{i % aggregates}",
                                   key=f"{prefix}-{i % aggregates}", payload={"seq": i, "tag": prefix, **payload}))
        db.commit()
    finally:
        db.close()
    return ids


def q(sql, **params):
    db = SessionLocal()
    try:
        return db.execute(text(sql), params).all()
    finally:
        db.close()


def inbox_count(ids):
    marks = ",".join(f"'{i}'" for i in ids)
    return q(f"SELECT COUNT(*) FROM consumer_inbox WHERE consumer='e2e_main' AND event_id IN ({marks})")[0][0]


def unpublished(ids):
    marks = ",".join(f"'{i}'" for i in ids)
    return q(f"SELECT COUNT(*) FROM outbox_event WHERE published_at IS NULL AND event_id IN ({marks})")[0][0]


def no_duplicates(ids):
    handled = [l["event_id"] for l in lines() if l["kind"] == "done" and l["event_id"] in ids]
    return len(handled) == len(set(handled)) == len(ids)


def dlq_messages(expected, seconds=20):
    from confluent_kafka import Consumer
    consumer = Consumer({"bootstrap.servers": "127.0.0.1:9092", "group.id": f"e2e-dlq-reader-{uuid.uuid4().hex[:6]}", "auto.offset.reset": "earliest", "enable.auto.commit": False})
    consumer.subscribe([kafka_bus.dlq_topic(TOPIC)])
    found, deadline = [], time.time() + seconds
    while time.time() < deadline and len(found) < expected:
        message = consumer.poll(1.0)
        if message is not None and not message.error():
            found.append(message)
    consumer.close()
    return found


def main() -> int:
    from confluent_kafka.admin import AdminClient
    print(f"运行编号 {RUN}，Topic {TOPIC}")
    try:
        info = docker("ps", "--filter", "name=enthub-kafka", "--format", "{{.Status}}").stdout.strip()
        if "Up" not in info:
            print("Kafka 容器没有运行：先 docker compose -f deploy/observability/docker-compose.yml up -d")
            return 2
        kafka_bus.ensure_topics(sorted(outbox.TOPICS), "127.0.0.1:9092")
        print("== 0. 启动一个消费者进程 ==")
        w1 = start_worker()
        check(True, "消费者进程就绪（发布器 + Kafka 消费者组 + 重试扫描）")

        print("\n== 1. 正常路径：30 个事件、6 个聚合对象 ==")
        ids = emit(30, prefix="a")
        check(wait_for(lambda: inbox_count(ids) == 30, 60), f"30 个事件全部处理完成（收件箱 {inbox_count(ids)}/30）")
        check(no_duplicates(ids), "每个事件恰好处理一次（没有漏、没有重复）")
        check(unpublished(ids) == 0, "发件箱里没有未发布的事件")
        order_ok = True
        for agg in {l["agg"] for l in lines() if l["tag"] == "a"}:
            seqs = [l["seq"] for l in lines() if l["kind"] == "done" and l["agg"] == agg and l["tag"] == "a"]
            order_ok = order_ok and seqs == sorted(seqs)
        check(order_ok, "同一个聚合对象的事件按发布顺序处理（消息键相同 → 同一分区 → 有序）")

        print("\n== 2. 重复投递：把已处理的消息再发两遍 ==")
        from confluent_kafka import Producer
        row = q("SELECT event_id, topic, event_type, schema_version, aggregate_type, aggregate_id, trace_id, producer, payload_json, created_at FROM outbox_event WHERE event_id=:e", e=ids[0])[0]
        event = outbox.envelope(__import__("types").SimpleNamespace(event_id=row[0], topic=row[1], event_type=row[2], schema_version=row[3], aggregate_type=row[4], aggregate_id=row[5],
                                                                     trace_id=row[6], producer=row[7], payload_json=row[8], created_at=row[9], organization_id=None, department_id=None, event_key=row[5]))
        before = len(done_ids())
        producer = Producer({"bootstrap.servers": "127.0.0.1:9092"})
        for _ in range(2):
            producer.produce(TOPIC, key=row[5].encode(), value=json.dumps(event, ensure_ascii=False).encode())
        producer.flush(10)
        time.sleep(6)
        check(len(done_ids()) == before, "重复消息没有让处理函数再次生效（收件箱挡住）")

        print("\n== 3. 重试：前两次失败，第三次成功 ==")
        flaky = emit(3, prefix="flaky", fail_times=2)
        check(wait_for(lambda: inbox_count(flaky) == 3, 60), "3 个事件在失败两次后最终都成功")
        starts = {i: sum(1 for l in lines() if l["kind"] == "start" and l["event_id"] == i) for i in flaky}
        check(all(n == 3 for n in starts.values()), f"每个事件恰好被尝试 3 次（{sorted(starts.values())}）")
        check(q("SELECT COUNT(*) FROM dead_letter WHERE topic=:t", t=TOPIC)[0][0] == 0, "没有进入死信")

        print("\n== 4. 死信：持续失败 → 死信表 + 死信 Topic + 问题中心；修复后重新投递 ==")
        bad = emit(1, prefix="dead", always_fail=True)[0]
        check(wait_for(lambda: q("SELECT COUNT(*) FROM dead_letter WHERE event_id=:e AND status='pending'", e=bad)[0][0] == 1, 60), "重试 3 次后进入死信表")
        messages = dlq_messages(1)
        check(len(messages) == 1, "死信 Topic 里有这条消息")
        if messages:
            headers = dict(messages[0].headers() or [])
            check(headers.get("dlq_consumer") == b"e2e_main" and b"\xe6" in headers.get("dlq_error", b"") and headers.get("dlq_source_topic") == TOPIC.encode(),
                  f"死信消息带着失败原因、消费者和原始位置（{headers.get('dlq_error', b'').decode('utf-8', 'replace')[:40]}）")
            check(json.loads(messages[0].value())["event_id"] == bad, "死信消息保留了原始事件，可以直接重放")
        issues = q("SELECT COUNT(*) FROM system_issue WHERE error_code='KAFKA_CONSUME_FAILED' AND operation LIKE :o", o="%e2e_main%")[0][0]
        check(issues >= 1, f"问题中心登记了故障（{issues} 个问题）")
        FLAG.write_text("fixed")                                         # “修复”：处理函数不再失败
        dead_id = q("SELECT id FROM dead_letter WHERE event_id=:e", e=bad)[0][0]
        db = SessionLocal()
        try:
            outbox.redeliver(db, dead_id, actor_id=1)
        finally:
            db.close()
        check(wait_for(lambda: inbox_count([bad]) == 1, 40), "修复后重新投递：事件经 Kafka 再次送达并成功处理")
        check(sum(1 for l in lines() if l["kind"] == "done" and l["event_id"] == bad) == 1, "恰好生效一次")

        print("\n== 5. Broker 宕机再恢复 ==")
        docker("stop", "enthub-kafka")
        outage = emit(10, prefix="outage")
        time.sleep(15)
        check(unpublished(outage) == 10, "Broker 宕机期间 10 个事件留在发件箱，没有丢（也没有被误标成已发布）")
        err = q("SELECT last_error, publish_attempts FROM outbox_event WHERE event_id=:e", e=outage[0])[0]
        check(bool(err[0]) and err[1] >= 1, f"记录了发布失败原因和次数（{err[1]} 次：{(err[0] or '')[:50]}）")
        check(w1.poll() is None, "消费者进程没有因为 Broker 不可用而退出")
        started = time.time()
        docker("start", "enthub-kafka")
        check(wait_for(lambda: inbox_count(outage) == 10, 150), f"Broker 恢复后 10 个事件全部送达并处理（用时 {time.time() - started:.0f} 秒）")
        check(no_duplicates(outage), "恢复过程中没有重复处理")

        print("\n== 6. 消费者进程崩溃：处理到一半被杀掉 ==")
        crashed = emit(1, prefix="crash", sleep=120)[0]
        check(wait_for(lambda: any(l["kind"] == "start" and l["event_id"] == crashed for l in lines()), 30), "事件开始处理（处理函数卡在里面）")
        kill_worker(w1)
        check(inbox_count([crashed]) == 0, "进程被杀时事件还没有写入收件箱")
        w2 = start_worker()
        check(wait_for(lambda: inbox_count([crashed]) == 1, 90), "重启后事件重新送达，租约到期后被处理完成")
        starts = sum(1 for l in lines() if l["kind"] == "start" and l["event_id"] == crashed)
        dones = sum(1 for l in lines() if l["kind"] == "done" and l["event_id"] == crashed)
        check(starts == 2 and dones == 1, f"处理函数被调用 {starts} 次（崩溃前 1 次 + 重启后 1 次），生效 {dones} 次")

        print("\n== 7. 多实例：两个消费者进程同一个组 ==")
        w3 = start_worker()
        time.sleep(8)                                                  # 等再平衡
        many = emit(60, prefix="many", aggregates=12)
        check(wait_for(lambda: inbox_count(many) == 60, 90), "60 个事件全部处理完成")
        check(no_duplicates(many), "两个实例之间没有重复处理")
        pids = {l["pid"] for l in lines() if l["kind"] == "done" and l["tag"] == "many"}
        check(len(pids) == 2, f"两个消费者实例都分到了分区并处理了事件（{len(pids)} 个进程）")

        print(f"\n通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
        for item in FAILED:
            print("  失败：", item)
        return 1 if FAILED else 0
    finally:
        for process in WORKERS:
            if process.poll() is None:
                kill_worker(process)
        if "Up" not in docker("ps", "--filter", "name=enthub-kafka", "--format", "{{.Status}}").stdout:
            docker("start", "enthub-kafka")
            time.sleep(8)
        try:
            admin = AdminClient({"bootstrap.servers": "127.0.0.1:9092"})
            for future in admin.delete_topics([TOPIC, kafka_bus.dlq_topic(TOPIC)], operation_timeout=15).values():
                try:
                    future.result()
                except Exception:  # noqa: BLE001
                    pass
            admin.delete_consumer_groups([f"e2e{RUN}.e2e_main"])
        except Exception:  # noqa: BLE001
            pass
        db = SessionLocal()
        try:
            ids = f"SELECT event_id FROM outbox_event WHERE topic='{TOPIC}'"
            for table in ("consumer_inbox", "consumer_retry", "dead_letter"):
                db.execute(text(f"DELETE FROM {table} WHERE event_id IN ({ids})"))
            db.execute(text(f"DELETE FROM outbox_event WHERE topic='{TOPIC}'"))
            issue_ids = "SELECT id FROM system_issue WHERE operation LIKE '%e2e_main%'"
            for table in ("issue_event", "issue_occurrence"):
                db.execute(text(f"DELETE FROM {table} WHERE issue_id IN ({issue_ids})"))
            db.execute(text("DELETE FROM system_issue WHERE operation LIKE '%e2e_main%'"))
            db.execute(text("DELETE FROM notification WHERE category='system' AND title LIKE '%e2e_main%'"))
            db.commit()
        finally:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
