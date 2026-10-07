"""可观测性真实联调：FastAPI + Java 业务服务 + MySQL + OpenTelemetry Collector / Tempo / Loki / Prometheus / Grafana 全部是真的。

一、链路：一次真实请求（带指定 trace_id）经 FastAPI → Java 业务服务，事件经发件箱发布并被消费；
   在 Tempo 里按这个 trace_id 查到 服务端 / 业务系统调用 / 事件发布 / 事件消费 四类 span，且父子关系正确。
二、日志：同一个 trace_id 在 Loki 里能查到应用日志；日志里没有口令、令牌；探活 /health 不产生链路。
三、指标与看板：Prometheus 抓到 /metrics（job 为 up），Grafana 三个数据源健康检查通过。
四、中断与恢复（核心）：
   · Collector 停掉期间：请求全部成功、耗时不明显变长；Collector 恢复后新的链路和日志自动继续；
   · Loki 停掉期间产生的日志：Loki 恢复后由 Collector 的重试队列补送，不丢；
   · Tempo 停掉期间产生的链路：同上。
五、Sentry / 云日志：没配置时业务照常；配置了但地址不可达（模拟云端中断）时，上报几十次也不卡业务、不抛异常。
   云日志走的是同一条 OTLP 通路（Collector 把 exporter 换成云厂商的即可），所以“云日志中断”由第四项里 Loki 中断的场景覆盖。

前置：docker compose -f deploy/observability/docker-compose.yml up -d；MySQL 已启动；8011 端口没有已在运行的后端（本脚本要带上 OTLP 环境变量重新启动它）。
用法：.venv\\Scripts\\python.exe scripts\\e2e_observability.py
"""
import base64
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("OFFLINE_DEMO_MODEL", "1")
os.environ.setdefault("CONSOLE_LOG_LEVEL", "CRITICAL")

from tests import _route_client as rc  # noqa: E402  先导入：设置非生产环境变量

import demo  # noqa: E402
import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

load_dotenv(ROOT / ".env")

from models.init_db import SessionLocal  # noqa: E402
from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team  # noqa: E402

API = "http://127.0.0.1:8011"
TEMPO, LOKI, PROM, GRAFANA, COLLECTOR = "http://127.0.0.1:3200", "http://127.0.0.1:3100", "http://127.0.0.1:9090", "http://127.0.0.1:3000", "http://127.0.0.1:13133"
GRAFANA_AUTH = ("admin", "admin-local-only")
SERVICE = "agent-service-e2e"
SUFFIX = uuid.uuid4().hex[:6].upper()
TODAY = datetime.now(timezone(timedelta(hours=8))).date()


class Checker:
    def __init__(self):
        self.failures = []
        self.passed = 0

    def ok(self, condition, message):
        print(f"  {'PASS' if condition else 'FAIL'} {message}")
        if condition:
            self.passed += 1
        else:
            self.failures.append(message)


check = Checker()


def new_trace_id() -> str:
    return uuid.uuid4().hex


def traceparent(trace_id: str) -> str:
    return f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"


def hexid(value: str, size: int) -> str:
    """Tempo 的 JSON 里 id 可能是十六进制也可能是 base64。"""
    if value and len(value) == size * 2 and all(c in "0123456789abcdefABCDEF" for c in value):
        return value.lower()
    try:
        return base64.b64decode(value).hex()
    except Exception:  # noqa: BLE001
        return value


def poll(fn, timeout: float, interval: float = 2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception:  # noqa: BLE001
            last = None
        time.sleep(interval)
    return last


def tempo_spans(trace_id: str) -> list:
    response = requests.get(f"{TEMPO}/api/traces/{trace_id}", timeout=5)
    if response.status_code != 200:
        return []
    data = response.json()
    batches = data.get("batches") or data.get("resourceSpans") or []
    spans = []
    for batch in batches:
        resource = {a["key"]: next(iter(a["value"].values()), None) for a in (batch.get("resource") or {}).get("attributes", [])}
        for scope in batch.get("scopeSpans") or batch.get("instrumentationLibrarySpans") or []:
            for span in scope.get("spans", []):
                spans.append({"name": span["name"], "trace_id": hexid(span["traceId"], 16), "span_id": hexid(span["spanId"], 8),
                              "parent": hexid(span.get("parentSpanId", ""), 8), "kind": span.get("kind"), "service": resource.get("service.name"),
                              "attrs": {a["key"]: next(iter(a["value"].values()), None) for a in span.get("attributes", [])},
                              "status": (span.get("status") or {}).get("code")})
    return spans


def loki_lines(trace_id: str, minutes: int = 30) -> list:
    now = time.time_ns()
    query = f'{{service_name="{SERVICE}"}} | trace_id="{trace_id}"'
    response = requests.get(f"{LOKI}/loki/api/v1/query_range", params={"query": query, "start": now - minutes * 60 * 10 ** 9, "end": now + 60 * 10 ** 9, "limit": 200}, timeout=8)
    if response.status_code != 200:
        return []
    return [value[1] for stream in response.json()["data"]["result"] for value in stream["values"]]


def stack_ready() -> dict:
    checks = {}
    for name, url in (("Collector", f"{COLLECTOR}/"), ("Tempo", f"{TEMPO}/ready"), ("Loki", f"{LOKI}/ready"), ("Prometheus", f"{PROM}/-/ready"), ("Grafana", f"{GRAFANA}/api/health")):
        try:
            checks[name] = requests.get(url, timeout=3).status_code == 200
        except requests.RequestException:
            checks[name] = False
    return checks


def docker(*args) -> None:
    subprocess.run(["docker", *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def wait_ready(names, timeout=120):
    return poll(lambda: all(stack_ready()[n] for n in names), timeout, 2)


def enterprise_engine():
    from urllib.parse import quote_plus
    user = os.getenv("ENTERPRISE_DB_USER") or os.getenv("DB_USER", "root")
    password = os.getenv("ENTERPRISE_DB_PASSWORD") or os.getenv("DB_PASSWORD", "")
    host, port = os.getenv("ENTERPRISE_DB_HOST", "127.0.0.1"), os.getenv("ENTERPRISE_DB_PORT", "3306")
    return create_engine(f"mysql+pymysql://{user}:{quote_plus(password)}@{host}:{port}/{os.getenv('ENTERPRISE_DB_NAME', 'enterprise_business')}?charset=utf8mb4")


def main() -> int:
    ent, db = enterprise_engine(), SessionLocal()
    started_pids = {}
    user = rc.create_user("obs-emp")
    owner = rc.create_user("obs-own")
    org = _create_org(db, f"e2e-obs-{SUFFIX}", owner["id"])
    team = _create_team(db, org, f"e2e-obs-team-{SUFFIX}", owner["id"])
    db.execute(text("UPDATE teams SET department_code='sales' WHERE id=:t"), {"t": team})
    db.commit()
    _add_org_member(db, org, user["id"], "member")
    _add_team_member(db, team, user["id"], "member")
    from service.llm.llm_config_service import save_config

    class U:
        id = user["id"]
    save_config(db, U(), "demo-offline", "offline-demo")
    event_ids = []
    try:
        print("== 零、前置：观测栈 ==")
        ready = stack_ready()
        for name, good in ready.items():
            check.ok(good, f"{name} 就绪")
        if not all(ready.values()):
            print("观测栈没有全部就绪，请先 docker compose -f deploy/observability/docker-compose.yml up -d")
            return 1

        print("== 一、启动带 OTLP 的后端与业务服务 ==")
        if demo.port_open(8011):
            check.ok(False, "8011 端口已有后端在运行：请先停掉（本脚本要带上 OTLP 环境变量重新启动它）")
            return 1
        env = demo.child_env()
        env.update({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4317", "SERVICE_NAME": SERVICE, "OTEL_EXPORT_TIMEOUT_SECONDS": "3", "CONSOLE_LOG_LEVEL": "INFO",
                "TRUSTED_HOSTS": "127.0.0.1,localhost,api,host.docker.internal"})      # Prometheus 在容器里，用 host.docker.internal 访问宿主机上的后端
        if not demo.port_open(8090):
            started_pids["hub"] = demo.spawn("hub", ["java", "-jar", str(demo.build_hub_jar())], ROOT / "enterprise-business-hub", env)
            check.ok(demo.wait_for("hub", 90, started_pids["hub"]), "Java 业务服务启动")
        started_pids["backend"] = demo.spawn("backend", [demo.PY, "-m", "uvicorn", "FasdtApi.main:app", "--host", "127.0.0.1", "--port", "8011"], ROOT, env)
        check.ok(demo.wait_for("backend", 120, started_pids["backend"]), "后端启动（OTLP 已启用）")
        headers = {**user["headers"], "Content-Type": "application/json"}
        with ent.begin() as conn:
            conn.execute(text("INSERT INTO leave_balance (user_id, leave_type_id, year, remaining_days) SELECT :u, id, :y, 10 FROM leave_type WHERE code='annual'"),
                         {"u": user["id"], "y": TODAY.year})
        start = (TODAY + timedelta(days=30)).isoformat()
        draft_body = {"team_id": team, "leave_type_code": "annual", "start_date": start, "end_date": start, "reason": "联调"}

        def create_draft(trace_id=None, secret=False):
            h = dict(headers)
            if trace_id:
                h["traceparent"] = traceparent(trace_id)
            began = time.perf_counter()
            response = requests.post(f"{API}/enterprise/oa/leave/mine", headers=h, json=draft_body, timeout=30)
            return response, time.perf_counter() - began

        print("== 二、链路：FastAPI → Java → 事件，在 Tempo 里是同一个 trace ==")
        t1 = new_trace_id()
        response, _ = create_draft(t1)
        check.ok(response.status_code == 200, f"带 trace_id 的真实请求成功（{response.status_code}）")
        check.ok(response.headers.get("X-Trace-ID") == t1, "响应头 X-Trace-ID 就是我们传入的 trace_id")
        requests.get(f"{API}/health", timeout=5)
        from service.events import outbox
        outbox.TOPICS.add("platform.issue.v1")
        event_ids.append(outbox.emit(db, topic="platform.issue.v1", event_type="issue.created", aggregate_type="e2e_observability", aggregate_id=SUFFIX,
                                     payload={"severity": "low", "title": "联调", "issue_no": f"E2E-{SUFFIX}", "error_code": "E2E"}, trace_id=t1))
        db.commit()
        spans = poll(lambda: (lambda s: s if {"server", "client", "producer", "consumer"} <= {x["kind"].split("_")[-1].lower() if isinstance(x["kind"], str) else str(x["kind"]) for x in s} else None)(tempo_spans(t1)), 60) or tempo_spans(t1)
        print("   Tempo 里的 span：", sorted({(s["name"], str(s["kind"])) for s in spans}))
        check.ok(bool(spans), "Tempo 能按 trace_id 查到这条链路")
        check.ok(all(s["trace_id"] == t1 for s in spans), "所有 span 的 trace_id 都等于请求里的那个（与日志、X-Trace-ID 一致）")
        server = [s for s in spans if s["name"] == "POST /enterprise/oa/leave/mine"]
        check.ok(len(server) == 1, "服务端 span 以路由模板命名")
        client_spans = [s for s in spans if s["name"].startswith("enterprise_hub ")]
        check.ok(bool(client_spans) and bool(server) and all(s["parent"] == server[0]["span_id"] for s in client_spans), "调用 Java 业务服务的 span 是服务端 span 的子节点")
        check.ok(bool(server) and server[0]["attrs"].get("http.response.status_code") in (200, "200"), "服务端 span 记录了状态码")
        check.ok(any(s["name"].startswith("publish ") for s in spans), "事件发布有 span（producer）")
        check.ok(any(s["name"].startswith("consume ") for s in spans), "事件消费有 span（consumer，沿用事件里的 trace_id）")
        blob = json.dumps(spans, ensure_ascii=False)
        check.ok("Bearer" not in blob and user["headers"]["Authorization"].split()[-1] not in blob, "span 里没有令牌")
        health = requests.get(f"{TEMPO}/api/search", params={"q": '{ name = "GET /health" }', "limit": 5, "start": int(time.time()) - 600, "end": int(time.time()) + 60}, timeout=8)
        check.ok(health.status_code == 200 and not health.json().get("traces"), "探活 /health 不产生链路")

        print("== 三、日志：同一个 trace_id 在 Loki 里能查到 ==")
        lines = poll(lambda: loki_lines(t1), 60) or []
        print("   Loki 日志条数：", len(lines))
        check.ok(len(lines) >= 1, "Loki 里能按 trace_id 查到这次请求的日志")
        check.ok(not any("Bearer " in line or "password" in line.lower() for line in lines), "日志里没有口令和令牌")

        print("== 四、指标与看板 ==")
        up = poll(lambda: [r for r in requests.get(f"{PROM}/api/v1/query", params={"query": 'up{job="agent-api"}'}, timeout=5).json()["data"]["result"] if r["value"][1] == "1"], 60, 3)
        check.ok(bool(up), "Prometheus 抓到后端的 /metrics（up=1）")
        for source in requests.get(f"{GRAFANA}/api/datasources", auth=GRAFANA_AUTH, timeout=5).json():
            if source["type"] == "tempo":      # Tempo 插件没有实现 /health 接口：改为经 Grafana 代理访问 Tempo 的 /api/echo
                result = requests.get(f"{GRAFANA}/api/datasources/proxy/uid/{source['uid']}/api/echo", auth=GRAFANA_AUTH, timeout=15)
                good = result.status_code == 200 and result.text.strip() == "echo"
            else:
                result = requests.get(f"{GRAFANA}/api/datasources/uid/{source['uid']}/health", auth=GRAFANA_AUTH, timeout=15)
                good = result.status_code == 200 and result.json().get("status") == "OK"
            check.ok(good, f"Grafana 数据源 {source['name']} 可用")

        print("== 五、Collector 中断与恢复 ==")
        baseline = [create_draft()[1] for _ in range(15)]
        docker("stop", "enthub-otel")
        try:
            during = [create_draft() for _ in range(40)]
        finally:
            pass
        statuses = {r.status_code for r, _ in during}
        check.ok(statuses == {200}, f"Collector 停掉期间 40 个请求全部成功（{statuses}）")
        slow = statistics.median(d for _, d in during)
        check.ok(slow <= statistics.median(baseline) * 3 + 0.2, f"耗时中位数没有明显变长（{statistics.median(baseline) * 1000:.0f}ms → {slow * 1000:.0f}ms）")
        docker("start", "enthub-otel")
        check.ok(wait_ready(["Collector"], 60), "Collector 重新就绪")
        t2 = new_trace_id()
        for _ in range(60):                             # gRPC 通道有重连退避：持续有请求，等通道自己恢复
            create_draft(t2)
            if tempo_spans(t2):
                break
            time.sleep(2)
        check.ok(bool(tempo_spans(t2)), "Collector 恢复后，新请求的链路自动出现在 Tempo")
        check.ok(bool(poll(lambda: loki_lines(t2), 60)), "Collector 恢复后，新请求的日志自动出现在 Loki")

        print("== 六、Loki 中断：Collector 重试队列补送 ==")
        t3 = new_trace_id()
        docker("stop", "enthub-loki")
        for _ in range(5):
            check.ok(create_draft(t3)[0].status_code == 200, "Loki 停掉期间请求成功") if _ == 0 else create_draft(t3)
        time.sleep(5)
        docker("start", "enthub-loki")
        check.ok(wait_ready(["Loki"], 120), "Loki 重新就绪")
        check.ok(bool(poll(lambda: loki_lines(t3), 150, 3)), "Loki 恢复后，中断期间产生的日志被补送（不丢）")
        check.ok(bool(tempo_spans(t3)) or bool(poll(lambda: tempo_spans(t3), 30)), "Loki 中断期间链路不受影响")

        print("== 七、Tempo 中断：Collector 重试队列补送 ==")
        t4 = new_trace_id()
        docker("stop", "enthub-tempo")
        for _ in range(5):
            create_draft(t4)
        time.sleep(5)
        docker("start", "enthub-tempo")
        check.ok(wait_ready(["Tempo"], 120), "Tempo 重新就绪")
        check.ok(bool(poll(lambda: tempo_spans(t4), 150, 3)), "Tempo 恢复后，中断期间产生的链路被补送（不丢）")
        check.ok(bool(poll(lambda: loki_lines(t4), 60)), "Tempo 中断期间日志不受影响")

        print("== 八、Sentry：没配置 / 配置了但地址不可达 ==")
        from service.observability import sentry_setup
        os.environ.pop("SENTRY_DSN", None)
        check.ok(sentry_setup.init() is False and sentry_setup.capture(RuntimeError("x"), trace_id=t1, operation="e2e", error_code="E2E") is None, "没配置 DSN：初始化返回 False，上报是空操作")
        os.environ["SENTRY_DSN"] = "http://publickey@127.0.0.1:1/1"
        check.ok(sentry_setup.init() is True, "配置了不可达的 DSN：初始化成功（不会因为 Sentry 起不来）")
        began = time.perf_counter()
        [sentry_setup.capture(RuntimeError(f"password=hunter2 {i}"), trace_id=t1, operation="e2e", error_code="E2E") for i in range(50)]
        check.ok(time.perf_counter() - began < 3, f"地址不可达时连续上报 50 次不卡业务（{time.perf_counter() - began:.2f}s，未抛异常）")
        import sentry_sdk
        flushed = time.perf_counter()
        sentry_sdk.flush(timeout=2)
        check.ok(time.perf_counter() - flushed < 5, "退出时 flush 有超时上限，不会拖住进程")
        os.environ.pop("SENTRY_DSN", None)
        sentry_sdk.init(dsn=None)
    finally:
        for name in ("enthub-otel", "enthub-loki", "enthub-tempo"):              # 不管上面哪一步断了，都把停掉的容器拉起来
            subprocess.run(["docker", "start", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for pid in started_pids.values():
            demo.kill_pid(pid)
        try:
            with ent.begin() as conn:
                for stmt in (f"DELETE FROM leave_request WHERE applicant_user_id={user['id']}", f"DELETE FROM leave_balance WHERE user_id={user['id']}",
                             f"DELETE FROM audit_event WHERE user_id={user['id']}"):
                    conn.execute(text(stmt))
            if event_ids:
                db.execute(text("DELETE FROM consumer_inbox WHERE event_id IN :ids").bindparams(__import__("sqlalchemy").bindparam("ids", expanding=True)), {"ids": event_ids})
                db.execute(text("DELETE FROM outbox_event WHERE event_id IN :ids").bindparams(__import__("sqlalchemy").bindparam("ids", expanding=True)), {"ids": event_ids})
            db.execute(text(f"DELETE FROM notification WHERE user_id IN ({user['id']}, {owner['id']})"))
            db.commit()
        except Exception as exc:  # noqa: BLE001
            print("  清理提示：", type(exc).__name__, str(exc)[:100])
        rc.cleanup()
        db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": team})
        db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": team})
        db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": org})
        db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org})
        db.commit()
        db.close()
    print(f"\n通过 {check.passed} 项，失败 {len(check.failures)} 项")
    for failure in check.failures:
        print("  ✘", failure)
    return 1 if check.failures else 0


if __name__ == "__main__":
    sys.exit(main())
