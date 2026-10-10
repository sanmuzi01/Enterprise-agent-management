"""员工自助绑定飞书 / 钉钉账号。

企业的飞书 / 钉钉应用由管理员配置一次；员工各自要做的只是把自己的飞书 / 钉钉账号和平台账号对上。三条路：
  1. 绑定码（默认，不需要额外配置）：员工在平台“设置 → 飞书 / 钉钉”领取 6 位绑定码，
     在飞书 / 钉钉里私聊机器人发“绑定 123456”。机器人收到的消息已经验签，发消息的人就是这个外部账号的主人。
  2. 一键授权（service/integrations/oauth.py）：跳到飞书 / 钉钉授权页，授权回来直接绑定。
  3. 管理员批量：组织同步按手机号对上，或在后台手动选人（identity.py）。

安全规则：
  - 绑定码只存 HMAC 摘要；10 分钟过期；只能用一次；领新码时旧码作废；同一外部账号 15 分钟内最多试 5 次；
  - 一个外部账号只对应一个平台账号，一个平台账号在同一平台只绑一个外部账号（要换先解绑）；
  - 管理员停用的绑定（离职、违规）员工不能自己重新绑；
  - 绑定、解绑都写审计。
"""
import hashlib
import hmac
import os
import re
import secrets
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select, text, update

from models.init_db import ExternalBindCode, ExternalUserBinding
from service.exceptions import Conflict, InvalidInput, NotFound
from service.integrations import apps
from service.integrations.base import PROVIDER_LABELS
from utils.timeutil import utcnow

CODE_TTL = timedelta(minutes=10)
BIND_COMMAND = re.compile(r"^\s*(?:绑定|bind)\s*[:：]?\s*(\d{6})\s*$", re.I)


def _hash(provider: str, code: str) -> str:
    key = (os.getenv("JWT_SECRET_KEY") or "dev-only").encode("utf-8")
    return hmac.new(key, f"{provider}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def parse_command(message: str) -> Optional[str]:
    """“绑定 123456” → "123456"；不是绑定命令返回 None。"""
    match = BIND_COMMAND.match(message or "")
    return match.group(1) if match else None


def _is_member(db, organization_id: int, user_id: int) -> bool:
    return bool(db.execute(text(
        "SELECT 1 FROM `user` u JOIN organization_members om ON om.user_id = u.id AND om.status = 'active' "
        "WHERE u.id = :u AND COALESCE(u.is_disabled, 0) = 0 AND om.organization_id = :o"),
        {"u": user_id, "o": organization_id}).scalar())


def _binding_of_user(db, provider: str, user_id: int) -> Optional[ExternalUserBinding]:
    return db.execute(select(ExternalUserBinding).where(
        ExternalUserBinding.provider == provider, ExternalUserBinding.local_user_id == user_id)
        .order_by(ExternalUserBinding.status != "active", ExternalUserBinding.id.desc()).limit(1)).scalar_one_or_none()


def bot_link(provider: str, app_id: str) -> Optional[str]:
    """飞书可以用应用链接直接打开和机器人的对话；钉钉没有通用的直达链接，在钉钉里搜索机器人名称。"""
    if provider == "feishu" and app_id:
        return f"https://applink.feishu.cn/client/bot/open?appId={app_id}"
    return None


def status_sync(db, user_id: int) -> Dict[str, Any]:
    """员工看自己的接入状态（每个平台一项）。"""
    from service.integrations import oauth
    items = []
    for provider in ("feishu", "dingtalk"):
        row = apps.get_row_sync(db, provider)
        item: Dict[str, Any] = {"provider": provider, "label": PROVIDER_LABELS[provider],
                                "available": bool(row is not None and row.enabled), "status": "unavailable"}
        if row is not None and row.enabled:
            binding = _binding_of_user(db, provider, user_id)
            item["status"] = "unbound"
            if binding is not None and binding.status == "active":
                item.update(status="bound", external_name=binding.external_name)
            elif binding is not None and binding.status == "disabled":
                item.update(status="disabled", external_name=binding.external_name)
            item["bot_link"] = bot_link(provider, row.app_id)
            item["oauth_available"] = oauth.available(provider)
        items.append(item)
    return {"items": items}


def create_code_sync(db, user_id: int, provider: str) -> Dict[str, Any]:
    from service import audit_service
    row = apps.get_row_sync(db, provider)
    if row is None or not row.enabled:
        raise InvalidInput(f"企业还没有开通{PROVIDER_LABELS[provider]}接入，请联系管理员")
    if not _is_member(db, row.organization_id, user_id):
        raise InvalidInput("你不是本企业的有效成员，不能绑定")
    binding = _binding_of_user(db, provider, user_id)
    if binding is not None and binding.status == "disabled":
        raise Conflict(f"你的{PROVIDER_LABELS[provider]}绑定已被管理员停用，请联系管理员")
    if binding is not None and binding.status == "active":
        raise Conflict(f"你已经绑定了{PROVIDER_LABELS[provider]}账号「{binding.external_name or binding.external_user_id}」，要换绑请先解绑")
    now = utcnow()
    db.execute(update(ExternalBindCode).where(ExternalBindCode.user_id == user_id, ExternalBindCode.provider == provider,
                                              ExternalBindCode.used_at.is_(None))
               .values(expires_at=now - timedelta(seconds=1)))   # MySQL DATETIME 按秒取整：写“现在”可能比读取时刻还晚
    for _ in range(10):                     # 避开仍有效的同号码（6 位数字，碰撞概率很低）
        code = f"{secrets.randbelow(1_000_000):06d}"
        clash = db.execute(select(ExternalBindCode.id).where(
            ExternalBindCode.provider == provider, ExternalBindCode.code_hash == _hash(provider, code),
            ExternalBindCode.used_at.is_(None), ExternalBindCode.expires_at > now)).first()
        if not clash:
            break
    db.add(ExternalBindCode(provider=provider, user_id=user_id, code_hash=_hash(provider, code), expires_at=now + CODE_TTL))
    db.commit()
    audit_service.record(user_id, "integration.bind_code_issued", resource_type="external_bind_code", resource_id=0,
                         detail={"provider": provider})
    return {"code": code, "expires_in": int(CODE_TTL.total_seconds()), "command": f"绑定 {code}",
            "bot_link": bot_link(provider, row.app_id)}


def bind_sync(db, provider: str, tenant_id: str, external_user_id: str, local_user_id: int, *,
              external_name: Optional[str] = None, union_id: Optional[str] = None, via: str = "code") -> Tuple[bool, str]:
    """把外部账号绑到平台账号（绑定码 / 一键授权共用）。返回（是否成功，给员工看的话）。"""
    from service import audit_service
    label = PROVIDER_LABELS[provider]
    app_row = apps.get_row_sync(db, provider)
    if app_row is None or not app_row.enabled:
        return False, f"企业还没有开通{label}接入"
    if not _is_member(db, app_row.organization_id, local_user_id):
        return False, "这个平台账号已停用或不在本企业，不能绑定"
    row = db.execute(select(ExternalUserBinding).where(
        ExternalUserBinding.provider == provider, ExternalUserBinding.external_tenant_id == tenant_id,
        ExternalUserBinding.external_user_id == external_user_id)).scalar_one_or_none()
    if row is not None and row.status == "disabled":
        return False, f"这个{label}账号的绑定已被管理员停用，请联系管理员"
    if row is not None and row.status == "active" and row.local_user_id is not None:
        if row.local_user_id == local_user_id:
            return True, "你已经绑定过了，可以直接给我发消息办事"
        return False, f"这个{label}账号已经绑定了另一个平台账号。如需换绑，请先在原账号的平台设置里解绑，或联系管理员"
    other = _binding_of_user(db, provider, local_user_id)
    if other is not None and other.status == "active" and other.external_user_id != external_user_id:
        return False, f"你的平台账号已经绑定了另一个{label}账号，请先在平台设置里解绑"
    if row is None:
        row = ExternalUserBinding(organization_id=app_row.organization_id, provider=provider, external_tenant_id=tenant_id,
                                  external_user_id=external_user_id)
        db.add(row)
    row.local_user_id, row.status = local_user_id, "active"
    row.external_union_id = union_id or row.external_union_id
    if external_name:
        row.external_name = external_name[:120]
    db.commit()
    audit_service.record(local_user_id, "integration.user_self_bound", resource_type="external_user_binding",
                         resource_id=row.id, detail={"provider": provider, "external_user_id": external_user_id, "via": via})
    return True, "绑定成功！现在可以直接给我发消息办事了，比如“帮我查一下这个月的报销”。"


def redeem_sync(db, provider: str, tenant_id: str, external_user_id: str, code: str,
                external_name: Optional[str] = None) -> str:
    """机器人收到“绑定 123456”：校验绑定码并绑定。返回回复给员工的话。"""
    from utils.rate_limit import LimitExceeded, require_limit
    try:
        require_limit(key=f"bind_code:{provider}:{external_user_id}", limit_env="INTEGRATION_BIND_RATE_LIMIT",
                      default_limit=5, window_env="INTEGRATION_BIND_RATE_WINDOW_SECONDS", default_window=900, label="绑定")
    except LimitExceeded:
        return "尝试次数太多，请 15 分钟后再试。"
    now = utcnow()
    record = db.execute(select(ExternalBindCode).where(
        ExternalBindCode.provider == provider, ExternalBindCode.code_hash == _hash(provider, code),
        ExternalBindCode.used_at.is_(None), ExternalBindCode.expires_at > now)
        .order_by(ExternalBindCode.id.desc()).limit(1)).scalar_one_or_none()
    if record is None:
        return "绑定码不对或已过期。请在平台“设置 → 飞书 / 钉钉”里重新获取（10 分钟内有效）。"
    ok, message = bind_sync(db, provider, tenant_id, external_user_id, record.user_id, external_name=external_name)
    if ok:
        record.used_at, record.used_by_external_id = now, external_user_id[:120]
        db.commit()
    return message


def unbind_self_sync(db, user_id: int, provider: str) -> Dict[str, Any]:
    from service import audit_service
    apps.check_provider(provider)
    binding = _binding_of_user(db, provider, user_id)
    if binding is None or binding.status != "active":
        raise NotFound("你还没有绑定")
    binding.local_user_id, binding.status = None, "unmatched"
    db.commit()
    audit_service.record(user_id, "integration.user_self_unbound", resource_type="external_user_binding",
                         resource_id=binding.id, detail={"provider": provider, "external_user_id": binding.external_user_id})
    return {"ok": True}


def remember_visitor_sync(db, provider: str, tenant_id: str, external_user_id: str, external_name: Optional[str]) -> None:
    """没绑定的人给机器人发了消息：记一条“待绑定”，管理员可以在后台直接选人绑定，不用再去找 open_id / userId。"""
    app_row = apps.get_row_sync(db, provider)
    if app_row is None:
        return
    row = db.execute(select(ExternalUserBinding).where(
        ExternalUserBinding.provider == provider, ExternalUserBinding.external_tenant_id == tenant_id,
        ExternalUserBinding.external_user_id == external_user_id)).scalar_one_or_none()
    if row is None:
        db.add(ExternalUserBinding(organization_id=app_row.organization_id, provider=provider, external_tenant_id=tenant_id,
                                   external_user_id=external_user_id, status="unmatched",
                                   external_name=(external_name or "")[:120] or None))
    elif external_name and not row.external_name:
        row.external_name = external_name[:120]
    db.commit()
