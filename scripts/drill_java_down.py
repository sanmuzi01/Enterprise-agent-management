"""故障演练：Java 业务服务中断 → 恢复。验证整条故障链路（真实服务，不是替身）：

1. 服务正常时请求成功；
2. 停掉 Java：请求得到统一错误（JAVA_SERVICE_UNAVAILABLE、trace_id、问题编号、可重试），没有堆栈；
3. 反复请求只产生一个问题、累计次数增加，熔断打开只产生一次依赖告警；
4. 重新启动 Java：熔断冷却后请求恢复，同一个问题上出现“依赖恢复”记录；
5. 响应里没有内部信息。

前置：scripts\\demo.py start 已运行（含演示账号）。用法：.venv\\Scripts\\python.exe scripts\\drill_java_down.py
"""
import os
import pathlib
import subprocess
import sys
import time

import requests
from sqlalchemy import create_engine, text

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
API = os.environ.get("DEMO_API", "http://127.0.0.1:8011")
HUB_PORT = 8090
PY = sys.executable
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def check(condition, message):
    if not condition:
        raise SystemExit(f"  FAIL {message}")
    print(f"  PASS {message}")


def db():
    from urllib.parse import quote_plus
    return create_engine(f"mysql+pymysql://{os.getenv('DB_USER', 'root')}:{quote_plus(os.getenv('DB_PASSWORD', ''))}@{os.getenv('DB_HOST', '127.0.0.1')}:{os.getenv('DB_PORT', '3306')}/{os.getenv('DB_NAME', 'agent_sql')}?charset=utf8mb4")


def hub_pid():
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, errors="replace").stdout
    for line in out.splitlines():
        if f":{HUB_PORT} " in line and "LISTENING" in line:
            return int(line.split()[-1])
    return None


def login(name):
    r = requests.post(f"{API}/user/login", json={"name": name, "password": "Demo@12345"}, timeout=10)
    r.raise_for_status()
    token = r.json().get("access_token") or r.json().get("token")
    return {"Authorization": f"Bearer {token}"}


def team_id(headers):
    r = requests.get(f"{API}/enterprise/workspace", headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()["departments"][0]["id"]


def main():
    headers = login("demo_emp")
    team = team_id(headers)
    url = f"{API}/enterprise/hr/cases/my-tasks?team_id={team}"
    engine = db()

    def issue_row():
        with engine.connect() as conn:
            return conn.execute(text("SELECT id, issue_no, occurrence_count, status FROM system_issue WHERE error_code='JAVA_SERVICE_UNAVAILABLE' "
                                     "AND operation LIKE '%my-tasks%' ORDER BY id DESC LIMIT 1")).first()

    def dependency_issue():
        with engine.connect() as conn:
            return conn.execute(text("SELECT id, occurrence_count FROM system_issue WHERE error_code='JAVA_SERVICE_UNAVAILABLE' "
                                     "AND operation LIKE '依赖%' ORDER BY id DESC LIMIT 1")).first()

    print("== 1. 服务正常 ==")
    check(requests.get(url, headers=headers, timeout=15).status_code == 200, "Java 正常时请求成功")
    before = issue_row()
    before_dep = dependency_issue()

    print("== 2. 停掉 Java ==")
    pid = hub_pid()
    check(pid is not None, f"找到 Java 进程 {pid}")
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    time.sleep(2)
    check(hub_pid() is None, "Java 已停止")
    responses = [requests.get(url, headers=headers, timeout=60) for _ in range(8)]
    first = responses[0]
    body = first.json()
    check(first.status_code == 503, f"请求得到 503（{first.status_code}）")
    check(body["code"] == "JAVA_SERVICE_UNAVAILABLE", "统一错误码 JAVA_SERVICE_UNAVAILABLE")
    check(body["retryable"] and body.get("retry_after"), "标明可重试与建议等待时间")
    check(len(body["trace_id"]) == 32 and first.headers["X-Trace-ID"] == body["trace_id"], "响应带 trace_id（与响应头一致）")
    check(body.get("issue_no", "").startswith("ISSUE-"), f"带问题编号 {body.get('issue_no')}")
    check(not any(w in first.text for w in ("Traceback", "ConnectionError", "requests.", "127.0.0.1")), "响应里没有堆栈和内部地址")
    check(all(r.status_code == 503 for r in responses), "后续请求（含熔断期间）都是同一种统一错误")

    print("== 3. 只产生一个问题、一次依赖告警 ==")
    after = issue_row()
    check(after is not None, "问题中心里有这个故障")
    check(before is None or after[0] == before[0], "没有为每次请求新建问题")
    delta = after[2] - (before[2] if before else 0)
    check(delta >= 1, f"累计发生次数增加 {delta}")
    dep = dependency_issue()
    check(dep is not None and (before_dep is None or dep[1] - before_dep[1] == 1), "熔断打开只产生一次依赖告警")

    print("== 4. 重新启动 Java，恢复 ==")
    subprocess.run([PY, str(ROOT / "scripts" / "demo.py"), "start"], capture_output=True, timeout=300)
    deadline = time.time() + 90
    recovered = False
    while time.time() < deadline:
        if requests.get(url, headers=headers, timeout=30).status_code == 200:
            recovered = True
            break
        time.sleep(5)
    check(recovered, "Java 恢复后请求恢复成功（熔断冷却后自动半开探测）")
    with engine.connect() as conn:
        events = [r[0] for r in conn.execute(text("SELECT action FROM issue_event WHERE issue_id=:i"), {"i": dep[0]}).all()]
    check("dependency_recovered" in events, "依赖问题上出现“依赖恢复”记录")
    print("\n故障演练通过")


if __name__ == "__main__":
    main()
