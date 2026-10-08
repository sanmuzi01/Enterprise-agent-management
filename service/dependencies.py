from utils.timeutil import utcnow

from typing import Optional

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from models.init_db import SessionLocal, get_db
from models.async_db import get_async_db
from models.user_dao import get_user_by_id
from models.user_async_dao import get_user_by_id_async, touch_user_seen_async
from service.auth import decode_access_token
from service.admin_service import is_admin_user
from service import session_cookie

# 使用 HTTPBearer：Swagger 会显示一个简单的 Bearer Token 输入框
# 用户直接填 token 即可，不需要走 OAuth2 密码流表单
_bearer = HTTPBearer(auto_error=False)


def security(
    request: Request,
    bearer: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> HTTPAuthorizationCredentials:
    """取出登录令牌：先看 Authorization: Bearer（脚本 / 集成 / 测试），没有再看 HttpOnly 会话 Cookie（浏览器前端）。

    Cookie 是浏览器自动携带的，所以用 Cookie 鉴权的、会改数据的请求还要通过 CSRF 校验（见 service/session_cookie.py）；
    Bearer 头不会被浏览器自动带上，不需要。
    """
    if bearer is not None and bearer.credentials:
        return bearer
    cookie_token = request.cookies.get(session_cookie.SESSION_COOKIE)
    if cookie_token:
        session_cookie.verify_csrf(request, cookie_token)
        return HTTPAuthorizationCredentials(scheme="Cookie", credentials=cookie_token)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未登录或登录已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _touch_user_seen(user_id: int) -> bool:
    """独立会话记录访问心跳，避免影响当前请求自己的事务。"""
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE `user` SET last_seen_at = :last_seen_at WHERE id = :user_id"),
            {"last_seen_at": utcnow(), "user_id": user_id},
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def _decode_payload(credentials: HTTPAuthorizationCredentials) -> dict:
    """解析 Bearer Token，返回完整 payload（含 user_id、ver）。"""

    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("user_id") is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 中缺少用户信息",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def _check_session_version(user, payload: dict) -> None:
    """token 里签发时的版本号要和用户当前的版本号一致，否则是一个"已经失效"的旧 token。

    改密码 / 管理员重置密码 / 管理员强制下线 / 用户"退出所有设备" 都会让 auth_version + 1，
    在此之前签发的 token（ver 还是旧值）从这一刻起全部失效，不需要等自然过期。
    """
    token_ver = payload.get("ver", 0)
    user_ver = getattr(user, "auth_version", 0) or 0
    if token_ver != user_ver:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录状态已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    解析 token，返回当前登录用户
    :raises HTTPException: token 无效/过期/用户不存在/已被吊销时抛 401
    :return: User 对象
    """
    payload = _decode_payload(credentials)
    user_id = payload.get("user_id")
    # 查询用户是否存在（防止用户已注销但 token 还有效）
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _check_session_version(user, payload)
    if getattr(user, "is_disabled", 0) == 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用，请联系管理员",
        )
    now = utcnow()
    last_seen_at = getattr(user, "last_seen_at", None)
    if not last_seen_at or (now - last_seen_at).total_seconds() > 30:
        if _touch_user_seen(user.id):
            db.refresh(user)
    return user


async def get_current_user_async(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    async_db=Depends(get_async_db),
):
    """异步当前用户依赖。"""

    payload = _decode_payload(credentials)
    user = await get_user_by_id_async(async_db, payload.get("user_id"))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _check_session_version(user, payload)
    if getattr(user, "is_disabled", 0) == 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用，请联系管理员",
        )
    now = utcnow()
    last_seen_at = getattr(user, "last_seen_at", None)
    if not last_seen_at or (now - last_seen_at).total_seconds() > 30:
        await touch_user_seen_async(async_db, user.id)
        # touch_user_seen_async has already committed this field. Mark the local
        # value as committed too, otherwise the next read query autoflushes a
        # second UPDATE and concurrent page requests can lock the same user row.
        set_committed_value(user, "last_seen_at", now)
    return user


def get_current_admin_user(current_user=Depends(get_current_user)):
    if not is_admin_user(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user


async def get_current_admin_user_async(current_user=Depends(get_current_user_async)):
    """异步优先的管理员鉴权依赖。"""

    if not is_admin_user(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user
