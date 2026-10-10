"""运行就绪检查（面试/上线前一眼看清“哪个环节没起来”）：数据库、迁移版本、企业业务服务（Java）、
模型（真实模型或离线演示模型）、后台提醒任务、演示数据。每项独立检查、独立失败，带一句话说明和怎么处理。
只读，不改任何数据；返回里不含密钥、连接串等敏感信息。"""
import asyncio
import os
from typing import Any, Dict, List

import requests
from sqlalchemy import text

from service import enterprise_hub_client as hub


def _item(key: str, label: str, ok: bool, message: str, fix: str = "", level: str = "") -> Dict[str, Any]:
    return {"key": key, "label": label, "ok": ok, "level": level or ("ok" if ok else "error"), "message": message, "fix": fix}


def _database() -> Dict[str, Any]:
    from models.init_db import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return _item("database", "主数据库（MySQL）", True, "连接正常")
    except Exception:  # noqa: BLE001
        return _item("database", "主数据库（MySQL）", False, "连不上数据库", "确认 MySQL 已启动，.env 里的 DB_HOST/DB_PORT/DB_USER/DB_PASSWORD 正确")
    finally:
        db.close()


def _migration() -> Dict[str, Any]:
    from pathlib import Path
    from models.init_db import SessionLocal
    db = SessionLocal()
    try:
        current = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001
        return _item("migration", "数据库迁移版本", False, "读不到迁移版本", "运行 .venv\\Scripts\\python.exe -m alembic upgrade head")
    finally:
        db.close()
    revisions, downs = set(), set()
    for path in (Path(__file__).resolve().parent.parent / "migrations" / "versions").glob("*.py"):
        body = path.read_text(encoding="utf-8")
        for line in body.splitlines():
            if line.startswith("revision = "):
                revisions.add(line.split("=")[1].strip().strip("\"'"))
            elif line.startswith("down_revision = "):
                downs.add(line.split("=")[1].strip().strip("\"'"))
    heads = revisions - downs
    ok = current in heads
    return _item("migration", "数据库迁移版本", ok, f"当前 {current}" + ("，已是最新" if ok else f"，最新是 {', '.join(sorted(heads))}"),
                 "" if ok else "运行 .venv\\Scripts\\python.exe -m alembic upgrade head")


def _enterprise_hub() -> Dict[str, Any]:
    url = f"{hub._base_url()}/actuator/health"
    try:
        response = requests.get(url, timeout=3)
        ok = response.status_code == 200 and response.json().get("status") == "UP"
    except Exception:  # noqa: BLE001
        ok = False
    return _item("enterprise_hub", "企业业务服务（Java）", ok, "运行正常" if ok else "连不上，请假/报销/采购/CRM/IT/人事业务不可用",
                 "" if ok else "启动 enterprise-business-hub（见 docs/demo-script.md），并确认 ENTERPRISE_HUB_BASE_URL 指向它")


def _model() -> Dict[str, Any]:
    from service.llm import offline_demo
    if offline_demo.enabled():
        return _item("model", "AI 模型", True, "离线演示模型已启用（规则抽取，不联网）：AI 整理可完整演示，普通对话为固定说明",
                     "要演示真实对话，在设置里连接真实模型", level="warn")
    return _item("model", "AI 模型", True, "使用用户自己连接的真实模型；未连接时 AI 整理会提示先去设置")


def _worker() -> Dict[str, Any]:
    enabled = os.getenv("REMINDERS_ENABLED", "1").strip().lower() not in ("0", "false", "no")
    return _item("reminders", "主动提醒任务", enabled, "已启用，按规则定时生成待办和通知" if enabled else "已关闭，不会生成审批超时/工单超时等提醒",
                 "" if enabled else "去掉 REMINDERS_ENABLED=0", level="ok" if enabled else "warn")


def _demo_data() -> Dict[str, Any]:
    from models.init_db import SessionLocal
    db = SessionLocal()
    try:
        users = db.execute(text("SELECT COUNT(*) FROM `user` WHERE name LIKE 'demo\\_%'")).scalar() or 0
        agents = db.execute(text("SELECT COUNT(*) FROM agent WHERE agent_type IN ('central', 'department') AND lifecycle_status = 'published'")).scalar() or 0
    finally:
        db.close()
    if not users:
        return _item("demo_data", "演示数据", True, "没有演示数据（正式环境正常）；需要演示时运行 scripts\\seed_enterprise_demo.py", level="warn")
    return _item("demo_data", "演示数据", True, f"{users} 个演示账号，{agents} 个已发布的部门/中央助手")


async def collect() -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = [
        await asyncio.to_thread(_database), await asyncio.to_thread(_migration), await asyncio.to_thread(_enterprise_hub),
        _model(), _worker(), await asyncio.to_thread(_demo_data),
    ]
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
