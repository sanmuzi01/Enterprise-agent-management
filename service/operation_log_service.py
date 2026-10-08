from utils.timeutil import utcnow
from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy import or_

from models.init_db import OperationLog, SessionLocal
from models.user_dao import get_user_by_id
from service.auth import decode_access_token


def _format_dt(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def extract_user_from_authorization(authorization: Optional[str], cookie_token: Optional[str] = None) -> Dict:
    """从请求里认出是谁：Authorization: Bearer 优先，没有就用浏览器的会话 Cookie（操作日志不能因为前端改用 Cookie 就变成匿名）。"""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif cookie_token:
        token = cookie_token
    else:
        return {}
    payload = decode_access_token(token)
    if not payload:
        return {}
    user_id = payload.get("user_id")
    if user_id is None:
        return {}

    db = SessionLocal()
    try:
        user = get_user_by_id(db, int(user_id))
        if not user:
            return {"user_id": int(user_id)}
        return {"user_id": user.id, "username": user.name}
    except Exception:
        return {"user_id": user_id}
    finally:
        db.close()


def create_operation_log(
        user_id: int = None,
        username: str = None,
        method: str = "",
        path: str = "",
        status_code: int = 0,
        latency_ms: int = 0,
        client_ip: str = None,
        user_agent: str = None,
        error_msg: str = None,
) -> None:
    db = SessionLocal()
    try:
        db.add(OperationLog(
            user_id=user_id,
            username=username,
            method=(method or "")[:10],
            path=(path or "")[:500],
            status_code=status_code or 0,
            latency_ms=latency_ms or 0,
            client_ip=(client_ip or "")[:100] or None,
            user_agent=(user_agent or "")[:500] or None,
            error_msg=(error_msg or "")[:1000] or None,
        ))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def list_operation_logs(
        db,
        limit: int = 100,
        keyword: str = None,
        method: str = None,
        status_group: str = None,
        user_id: int = None,
        days: int = 7,
) -> List[Dict]:
    limit = max(1, min(limit, 500))
    days = max(1, min(days, 90))
    query = db.query(OperationLog)
    query = query.filter(OperationLog.created_at >= utcnow() - timedelta(days=days))

    if user_id:
        query = query.filter(OperationLog.user_id == user_id)
    if method:
        query = query.filter(OperationLog.method == method.upper())
    if status_group == "success":
        query = query.filter(OperationLog.status_code >= 200, OperationLog.status_code < 400)
    elif status_group == "error":
        query = query.filter(OperationLog.status_code >= 400)
    elif status_group == "slow":
        query = query.filter(OperationLog.latency_ms >= 1000)
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.filter(or_(
            OperationLog.path.like(like),
            OperationLog.username.like(like),
            OperationLog.client_ip.like(like),
            OperationLog.error_msg.like(like),
        ))

    logs = query.order_by(OperationLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": item.id,
            "user_id": item.user_id,
            "username": item.username,
            "method": item.method,
            "path": item.path,
            "status_code": item.status_code,
            "latency_ms": item.latency_ms,
            "client_ip": item.client_ip,
            "user_agent": item.user_agent,
            "error_msg": item.error_msg,
            "created_at": _format_dt(item.created_at),
        }
        for item in logs
    ]
