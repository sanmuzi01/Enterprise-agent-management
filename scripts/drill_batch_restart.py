"""故障演练：批量整理进行到一半时后端进程被杀掉 → 重启后继续整理，而不是标成失败。

验证真实服务（FastAPI + MySQL，事件运行器在后端进程里）：
1. 提交 10 份材料的批次，立即杀掉后端进程（kill -9 级别），此时还有材料没整理完；
2. 事件和材料在同一个事务里登记，进程崩溃后事件还在发件箱里；
3. 重新启动后端：运行器第一轮就接着处理遗留事件，全部材料最终是“待核对”，没有一份被标成失败；
4. 同一批次没有被重复整理（每份材料只被整理一次）。

前置：scripts\\demo.py start 已运行。用法：.venv\\Scripts\\python.exe scripts\\drill_batch_restart.py
"""
import os
import pathlib
import subprocess
import sys
import time
import uuid

import requests
from sqlalchemy import create_engine, text

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
API = os.environ.get("DEMO_API", "http://127.0.0.1:8011")
PY = sys.executable
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def check(condition, message):
    if not condition:
        raise SystemExit(f"  FAIL {message}")
    print(f"  PASS {message}")


def engine():
    from urllib.parse import quote_plus
    return create_engine(f"mysql+pymysql://{os.getenv('DB_USER', 'root')}:{quote_plus(os.getenv('DB_PASSWORD', ''))}@{os.getenv('DB_HOST', '127.0.0.1')}:{os.getenv('DB_PORT', '3306')}/{os.getenv('DB_NAME', 'agent_sql')}?charset=utf8mb4")


def listening_pid(port):
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, errors="replace").stdout
    for line in out.splitlines():
        if f":{port} " in line and "LISTENING" in line:
            return int(line.split()[-1])
    return None


def statuses(db, batch_id):
    with db.connect() as conn:
        return [r[0] for r in conn.execute(text("SELECT status FROM automation_work WHERE batch_id=:b ORDER BY batch_index"), {"b": batch_id}).all()]


def attempt(headers, team, db, size):
    batch_id = str(uuid.uuid4())
    items = [{"name": f"演练材料{i}.txt", "text": f"演练材料 {i}：我要请年假，到 2026年12月{i + 1:02d}日，家里有事需要处理。"} for i in range(size)]
    response = requests.post(f"{API}/enterprise/automation/batches", headers=headers, timeout=30, json={
        "batch_id": batch_id, "team_id": team, "kind": "leave", "model_name": "demo-offline", "sensitivity": "internal", "items": items})
    response.raise_for_status()
    pid = listening_pid(8011)
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)       # 立即杀掉后端
    time.sleep(1)
    return batch_id, statuses(db, batch_id)


def main():
    db = engine()
    login = requests.post(f"{API}/user/login", json={"name": "demo_emp", "password": "Demo@12345"}, timeout=10)
    login.raise_for_status()
    headers = {"Authorization": f"Bearer {login.json().get('access_token') or login.json().get('token')}"}
    team = requests.get(f"{API}/enterprise/workspace", headers=headers, timeout=10).json()["departments"][0]["id"]

    print("== 1. 提交批次后立即杀掉后端 ==")
    batch_id, mid = None, []
    for round_ in range(6):
        if round_:
            subprocess.run([PY, str(ROOT / "scripts" / "demo.py"), "start"], capture_output=True, timeout=300)
            time.sleep(3)
            # 前一轮杀得太晚（全部已整理完）：等它们都结束，再试
            login = requests.post(f"{API}/user/login", json={"name": "demo_emp", "password": "Demo@12345"}, timeout=10)
            headers = {"Authorization": f"Bearer {login.json().get('access_token') or login.json().get('token')}"}
        batch_id, mid = attempt(headers, team, db, 10)
        if any(s in ("queued", "processing") for s in mid):
            break
    check(any(s in ("queued", "processing") for s in mid), f"杀掉后端时批次还没整理完：{sorted(set(mid))}")
    with db.connect() as conn:
        pending = conn.execute(text("SELECT COUNT(*) FROM outbox_event WHERE topic='automation.job.v1' AND payload_json LIKE :p"), {"p": f"%{batch_id}%"}).scalar()
    check(pending == 10, "10 个事件都已经和材料一起落库（崩溃没有丢事件）")

    print("== 2. 重启后端，继续整理 ==")
    subprocess.run([PY, str(ROOT / "scripts" / "demo.py"), "start"], capture_output=True, timeout=300)
    deadline = time.time() + 180
    final = []
    while time.time() < deadline:
        final = statuses(db, batch_id)
        if all(s not in ("queued", "processing") for s in final):
            break
        time.sleep(3)
    check(all(s == "ready" for s in final), f"重启后全部材料整理完成、待核对：{sorted(set(final))}")
    check("failed" not in final, "没有任何一份被标成失败")
    with db.connect() as conn:
        dead = conn.execute(text("SELECT COUNT(*) FROM dead_letter WHERE consumer='batch_item_worker' AND payload_json LIKE :p"), {"p": f"%{batch_id}%"}).scalar()
        done = conn.execute(text("SELECT COUNT(*) FROM consumer_inbox i JOIN outbox_event o ON o.event_id=i.event_id WHERE i.consumer='batch_item_worker' AND o.payload_json LIKE :p"), {"p": f"%{batch_id}%"}).scalar()
        works = conn.execute(text("SELECT COUNT(*) FROM automation_work WHERE batch_id=:b AND proposal_json IS NOT NULL"), {"b": batch_id}).scalar()
    check(dead == 0, "没有进入死信")
    check(done == 10 and works == 10, "每份材料恰好被处理一次（收件箱 10 条、结果 10 份）")
    print("\n批量整理崩溃恢复演练通过")


if __name__ == "__main__":
    main()
