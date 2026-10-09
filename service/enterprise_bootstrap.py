"""全新部署第一次启动时，自动建好平台服务的那家企业。

之前要求部署后手动跑 scripts/backfill_default_organization.py。忘了跑，组织架构、部门、企业智能体这些页面
都会提示“企业尚未初始化”，新注册的用户也进不了企业——这是交付时最容易漏掉的一步，所以改成启动时自动做：
  - 库里已经有企业记录就什么都不做（平台只服务一家企业，不会建第二个）；
  - 没有企业、也一个用户都没有就跳过（等有人注册 / 内置管理员建好后的下一次启动再建）；
  - 企业的所有者取第一个平台管理员（没有就取最早的用户），现有用户全部加入：管理员是所有者，其他人是成员；
  - 还没归属企业的知识库空间挂到这家企业下。
多个进程同时启动时用 MySQL 命名锁串行化，只会建一次。设 AUTO_CREATE_ENTERPRISE=0 可以关掉（改回手动跑脚本）。
"""
import os
from typing import Any, Dict, Optional

from sqlalchemy import text

from utils.logger_handler import get_logger

logger = get_logger("enterprise_bootstrap")

_LOCK_NAME = "enterprise_bootstrap"
# 和 models/enterprise_dao._ENTERPRISE_ID_SQL 同一个口径：id 最小的那条就是“这家企业”
_ANY_ENTERPRISE_SQL = "SELECT id FROM organizations ORDER BY id LIMIT 1"


def enabled() -> bool:
    return os.getenv("AUTO_CREATE_ENTERPRISE", "1").strip().lower() not in ("0", "false", "no", "off")


def _pick_owner(db) -> Optional[int]:
    from models.init_db import User
    from service.admin_service import is_admin_user
    first = None
    for (uid,) in db.execute(text("SELECT id FROM `user` ORDER BY id")).all():
        first = uid if first is None else first
        if is_admin_user(db.get(User, uid)):
            return uid
    return first


def ensure_default_enterprise(db) -> Dict[str, Any]:
    """幂等。返回 {"status": "exists" | "created" | "skipped", ...}，便于启动日志和测试核对。"""
    from models.enterprise_dao import DEFAULT_ORG_NAME
    from models.init_db import User
    from service.admin_service import is_admin_user

    existing = db.execute(text(_ANY_ENTERPRISE_SQL)).scalar()
    if existing is not None:
        return {"status": "exists", "organization_id": int(existing)}
    owner = _pick_owner(db)
    if owner is None:
        return {"status": "skipped", "reason": "还没有任何用户"}
    roles = dict(db.execute(text(
        "SELECT code, id FROM enterprise_role WHERE scope='organization' AND code IN ('owner', 'member')")).all())
    if {"owner", "member"} - set(roles):
        return {"status": "skipped", "reason": "企业角色表还没初始化（迁移没跑完）"}

    db.execute(text("INSERT INTO organizations (name, owner_user_id, status, created_at) VALUES (:n, :o, 'active', NOW())"),
               {"n": os.getenv("DEFAULT_ENTERPRISE_NAME", DEFAULT_ORG_NAME), "o": owner})
    org_id = int(db.execute(text(_ANY_ENTERPRISE_SQL)).scalar())
    members = 0
    for (uid,) in db.execute(text(
            "SELECT u.id FROM `user` u LEFT JOIN organization_members om ON om.user_id = u.id AND om.organization_id = :o "
            "WHERE om.id IS NULL ORDER BY u.id"), {"o": org_id}).all():
        role = roles["owner"] if is_admin_user(db.get(User, uid)) else roles["member"]
        db.execute(text("INSERT INTO organization_members (organization_id, user_id, role_id, status, created_at, updated_at) "
                        "VALUES (:o, :u, :r, 'active', NOW(), NOW())"), {"o": org_id, "u": uid, "r": role})
        members += 1
    spaces = db.execute(text("UPDATE knowledge_spaces SET organization_id = :o WHERE organization_id IS NULL"),
                        {"o": org_id}).rowcount
    db.commit()
    return {"status": "created", "organization_id": org_id, "owner_user_id": owner, "members": members, "spaces": int(spaces or 0)}


def ensure_on_startup() -> Optional[Dict[str, Any]]:
    """启动时调用：加锁后执行；任何异常只记日志，不阻断启动（页面会提示企业未初始化，可再手动跑脚本）。"""
    if not enabled():
        return None
    from models.init_db import SessionLocal, engine
    lock_conn = None
    try:
        lock_conn = engine.connect()
        lock_conn.execute(text("SELECT GET_LOCK(:n, 30)"), {"n": _LOCK_NAME})
        with SessionLocal() as db:
            result = ensure_default_enterprise(db)
        if result["status"] == "created":
            logger.info("已自动建好企业：%s", result)
        elif result["status"] == "skipped":
            logger.info("暂不自动建企业：%s", result["reason"])
        return result
    except Exception:  # noqa: BLE001
        logger.exception("自动建企业失败（不影响启动；可手动跑 scripts/backfill_default_organization.py --yes）")
        return None
    finally:
        if lock_conn is not None:
            try:
                lock_conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": _LOCK_NAME})
            finally:
                lock_conn.close()
