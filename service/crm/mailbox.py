"""员工邮箱接入（IMAP）：Microsoft 365 / Outlook、Gmail、企业邮箱都走 IMAP（用授权码 / 应用专用密码）。

隐私边界：只读员工指定的那一个文件夹（默认“CRM”）——员工把要进 CRM 的邮件转发、移动或打标签到这个文件夹，
收件箱里的其他邮件一概不读。按 UID 增量读取，同一封邮件按 Message-ID 去重，重复同步不会多出活动。
"""
import imaplib
import os
import socket
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from models.init_db import CrmMailAccount
from service.crm import activities as acts
from service.crm.sources import parse_email
from service.department_access import require_team_member_async
from service.exceptions import InvalidInput, NotFound
from utils.crypto import decrypt, encrypt
from utils.logger_handler import get_logger
from utils.timeutil import utcnow

logger = get_logger("crm_mailbox")

MAX_PER_SYNC = 50


def account_payload(row: Optional[CrmMailAccount]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {"imap_host": row.imap_host, "imap_port": row.imap_port, "username": row.username, "folder": row.folder,
            "enabled": bool(row.enabled), "last_synced_at": row.last_synced_at.isoformat() + "Z" if row.last_synced_at else None,
            "last_error": row.last_error, "has_password": True}


async def _get(db, user_id: int, team_id: int) -> Optional[CrmMailAccount]:
    return (await db.execute(select(CrmMailAccount).where(CrmMailAccount.user_id == user_id,
                                                          CrmMailAccount.team_id == team_id))).scalar_one_or_none()


async def get_account(db, user_id: int, team_id: int) -> Optional[Dict[str, Any]]:
    await require_team_member_async(db, user_id, team_id, "crm")
    return account_payload(await _get(db, user_id, team_id))


def _check_host(host: str) -> str:
    host = (host or "").strip().lower()
    if not host or "/" in host or ":" in host or len(host) > 200:
        raise InvalidInput("请填写 IMAP 服务器地址，例如 outlook.office365.com、imap.gmail.com、imap.exmail.qq.com")
    if os.getenv("APP_ENV", "dev") == "production":
        # 生产环境不允许连内网地址（防止被用来探测内网）
        try:
            import ipaddress
            for info in socket.getaddrinfo(host, None):
                if not ipaddress.ip_address(info[4][0]).is_global:
                    raise InvalidInput("IMAP 服务器地址不能是内网地址")
        except socket.gaierror:
            raise InvalidInput("IMAP 服务器地址解析不到，请检查拼写") from None
    return host


async def save_account(db, user_id: int, team_id: int, *, imap_host: str, username: str, password: Optional[str],
                       imap_port: int = 993, folder: str = "CRM", enabled: bool = True) -> Dict[str, Any]:
    await require_team_member_async(db, user_id, team_id, "crm", message="不属于该部门，不能连接邮箱")
    host = _check_host(imap_host)
    if not (username or "").strip():
        raise InvalidInput("请填写邮箱账号")
    if not 1 <= int(imap_port) <= 65535:
        raise InvalidInput("端口不对")
    folder = (folder or "CRM").strip()[:100]
    row = await _get(db, user_id, team_id)
    if row is None:
        if not password:
            raise InvalidInput("第一次连接需要填写密码或授权码")
        row = CrmMailAccount(user_id=user_id, team_id=team_id, imap_host=host, imap_port=imap_port,
                             username=username.strip(), encrypted_password=encrypt(password), folder=folder, last_uid=0)
        db.add(row)
    else:
        if (row.imap_host, row.username, row.folder) != (host, username.strip(), folder):
            row.last_uid = 0                     # 换了邮箱或文件夹：从头读（Message-ID 去重，不会重复入库）
        row.imap_host, row.imap_port, row.username, row.folder = host, imap_port, username.strip(), folder
        if password:
            row.encrypted_password = encrypt(password)
    row.enabled = 1 if enabled else 0
    row.last_error = None
    await db.commit()
    return account_payload(row)


async def delete_account(db, user_id: int, team_id: int) -> None:
    row = await _get(db, user_id, team_id)
    if row is None:
        raise NotFound("还没有连接邮箱")
    await db.delete(row)
    await db.commit()


def fetch_new(host: str, port: int, username: str, password: str, folder: str, last_uid: int,
              limit: int = MAX_PER_SYNC) -> Tuple[List[Tuple[int, bytes]], int]:
    """读取指定文件夹里 UID 大于 last_uid 的邮件（只读打开，不改已读状态）。返回 ([(uid, 原始邮件)], 最大 UID)。"""
    timeout = float(os.getenv("CRM_IMAP_TIMEOUT_SECONDS", "20"))
    try:
        client = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise InvalidInput(f"连不上邮箱服务器：{type(exc).__name__}") from None
    try:
        try:
            client.login(username, password)
        except imaplib.IMAP4.error:
            raise InvalidInput("邮箱账号或密码（授权码）不对") from None
        status, _ = client.select(f'"{folder}"', readonly=True)
        if status != "OK":
            raise InvalidInput(f"邮箱里没有“{folder}”文件夹，请先建好，把要进 CRM 的邮件放进去")
        status, data = client.uid("search", None, f"UID {last_uid + 1}:*")
        uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > last_uid) if status == "OK" else []
        messages = []
        for uid in uids[:limit]:
            status, parts = client.uid("fetch", str(uid), "(BODY.PEEK[])")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
            if status == "OK" and raw:
                messages.append((uid, raw))
        return messages, max([last_uid] + [u for u, _ in messages])
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass


async def sync(db, user_id: int, team_id: int) -> Dict[str, Any]:
    """立即同步一次。每封邮件按 Message-ID 去重后入库，自动关联客户；对不上的进“待归属”。"""
    import asyncio
    await require_team_member_async(db, user_id, team_id, "crm")
    row = await _get(db, user_id, team_id)
    if row is None:
        raise NotFound("还没有连接邮箱")
    if not row.enabled:
        raise InvalidInput("邮箱接入已暂停")
    try:
        messages, max_uid = await asyncio.to_thread(fetch_new, row.imap_host, row.imap_port, row.username,
                                                    decrypt(row.encrypted_password), row.folder, row.last_uid)
    except InvalidInput as exc:
        row.last_error = str(exc)[:500]
        await db.commit()
        raise
    directory = await acts.load_directory(db, user_id, team_id) if messages else None
    created = duplicates = failed = 0
    for _uid, raw in messages:
        try:
            draft = await asyncio.to_thread(parse_email, raw, own_addresses=[row.username])
            result = await acts.ingest(db, user_id, team_id, draft, "email", directory=directory)
            duplicates += int(result["duplicate"])
            created += int(not result["duplicate"])
        except Exception:  # noqa: BLE001 —— 一封坏邮件不影响其他邮件
            failed += 1
            logger.warning("邮件入库失败", exc_info=True)
    row = await _get(db, user_id, team_id)
    row.last_uid, row.last_synced_at, row.last_error = max_uid, utcnow(), None
    await db.commit()
    return {"fetched": len(messages), "created": created, "duplicates": duplicates, "failed": failed,
            "account": account_payload(row)}
