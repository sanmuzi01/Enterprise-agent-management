"""外部应用配置（collaboration_app）：管理员填写飞书 / 钉钉自建应用的凭证。

密钥（App Secret、Encrypt Key / aes_key）用 utils.crypto 加密后存库，接口只返回“是否已填写”和末四位，不回显明文；
明文只在处理回调、调用开放接口时在内存里解出来（AppCredentials）。平台只服务一家企业，所以每个平台只有一份配置。
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional

from sqlalchemy import select

from models.init_db import CollaborationApp
from service.exceptions import InvalidInput, NotFound
from service.integrations.base import PROVIDER_LABELS, PROVIDERS
from utils.crypto import decrypt, encrypt


_ENCRYPTED_PREFIX = "fernet:"


def _encrypt_token(value: str) -> str:
    return _ENCRYPTED_PREFIX + encrypt(value)


def _decrypt_token(value: Optional[str]) -> str:
    if not value:
        return ""
    if value.startswith(_ENCRYPTED_PREFIX):
        return decrypt(value[len(_ENCRYPTED_PREFIX):])
    return value  # 兼容升级前保存的明文；get_row_sync 会立即将它改写为密文。


@dataclass
class AppCredentials:
    id: int
    organization_id: int
    provider: str
    app_id: str
    app_secret: str
    verification_token: str
    encrypt_key: str
    robot_code: str
    card_template_id: str
    enabled: bool


def check_provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise NotFound("不支持的协作平台")
    return provider


def credentials(row: CollaborationApp) -> AppCredentials:
    return AppCredentials(
        id=row.id, organization_id=row.organization_id, provider=row.provider, app_id=row.app_id,
        app_secret=decrypt(row.encrypted_app_secret), verification_token=_decrypt_token(row.verification_token),
        encrypt_key=decrypt(row.encrypted_encrypt_key) if row.encrypted_encrypt_key else "",
        robot_code=row.robot_code or "", card_template_id=row.card_template_id or "", enabled=bool(row.enabled))


def _tail(secret: str) -> str:
    return f"…{secret[-4:]}" if secret else ""


def describe(row: Optional[CollaborationApp], provider: str) -> Dict[str, Any]:
    if row is None:
        return {"provider": provider, "label": PROVIDER_LABELS[provider], "configured": False, "enabled": False}
    creds = credentials(row)
    return {"provider": provider, "label": PROVIDER_LABELS[provider], "configured": True, "enabled": creds.enabled,
            "app_id": creds.app_id, "app_secret": _tail(creds.app_secret), "has_verification_token": bool(creds.verification_token),
            "encrypt_key": _tail(creds.encrypt_key), "robot_code": creds.robot_code, "card_template_id": creds.card_template_id,
            "last_health_at": row.last_health_at.isoformat() if row.last_health_at else None, "last_error": row.last_error}


def enterprise_id_sync(db) -> int:
    from sqlalchemy import text
    org = db.execute(text("SELECT id FROM organizations ORDER BY id LIMIT 1")).scalar()
    if org is None:
        raise NotFound("企业还没有初始化")
    return int(org)


def get_row_sync(db, provider: str) -> Optional[CollaborationApp]:
    check_provider(provider)
    row = db.execute(select(CollaborationApp).where(CollaborationApp.organization_id == enterprise_id_sync(db),
                                                    CollaborationApp.provider == provider)).scalar_one_or_none()
    # 老版本把 Verification Token 明文存在同一列。首次读取即原位加密，不让历史数据长期裸存。
    if row is not None and row.verification_token and not row.verification_token.startswith(_ENCRYPTED_PREFIX):
        row.verification_token = _encrypt_token(row.verification_token)
        db.commit()
    return row


def load_enabled_sync(db, provider: str) -> Optional[AppCredentials]:
    """处理回调用：没配置或没启用返回 None（回调直接拒绝）。"""
    row = get_row_sync(db, provider)
    return credentials(row) if row is not None and row.enabled else None


def save_sync(db, provider: str, operator_id: int, *, app_id: str, app_secret: Optional[str], verification_token: Optional[str],
              encrypt_key: Optional[str], robot_code: Optional[str], card_template_id: Optional[str], enabled: bool) -> Dict[str, Any]:
    """新建或更新。密钥类字段传 None 表示“不修改”（页面不回显旧值，留空就是保持原样）；传空字符串表示清空。"""
    from service import audit_service
    row = get_row_sync(db, provider)
    app_id = (app_id or "").strip()
    if not app_id:
        raise InvalidInput("请填写应用的 App ID（钉钉为 AppKey）")
    old = credentials(row) if row is not None else None
    resolved_app_secret = (app_secret or "").strip() or (old.app_secret if old else "")
    resolved_token = (verification_token or "").strip() if verification_token is not None else (old.verification_token if old else "")
    resolved_encrypt_key = (encrypt_key or "").strip() if encrypt_key is not None else (old.encrypt_key if old else "")
    if enabled:
        required = {
            "feishu": (("App Secret", resolved_app_secret), ("Verification Token", resolved_token),
                       ("Encrypt Key", resolved_encrypt_key)),
            # 钉钉入站机器人可以只依赖 AppSecret；robotCode 仅在主动推送时需要，不能一刀切为启用前必填。
            "dingtalk": (("AppSecret", resolved_app_secret),),
        }[provider]
        missing = [label for label, value in required if not value]
        if missing:
            raise InvalidInput(f"启用{PROVIDER_LABELS[provider]}前请先填写：{'、'.join(missing)}")
    if row is None:
        if not resolved_app_secret:
            raise InvalidInput("第一次配置需要填写 App Secret")
        row = CollaborationApp(organization_id=enterprise_id_sync(db), provider=provider, app_id=app_id,
                               encrypted_app_secret=encrypt(resolved_app_secret), enabled=0)
        db.add(row)
    row.app_id = app_id
    changed = ["app_id"]
    if app_secret:
        row.encrypted_app_secret = encrypt(app_secret.strip())
        changed.append("app_secret")
    if verification_token is not None:
        row.verification_token = _encrypt_token(verification_token.strip()) if verification_token.strip() else None
        changed.append("verification_token")
    if encrypt_key is not None:
        row.encrypted_encrypt_key = encrypt(encrypt_key.strip()) if encrypt_key.strip() else None
        changed.append("encrypt_key")
    if robot_code is not None:
        row.robot_code = robot_code.strip() or None
    if card_template_id is not None:
        row.card_template_id = card_template_id.strip() or None
    row.enabled = 1 if enabled else 0
    row.updated_by = operator_id
    db.commit()
    audit_service.record(operator_id, "integration.app_saved", resource_type="collaboration_app", resource_id=row.id,
                         detail={"provider": provider, "enabled": bool(enabled), "changed": changed})   # 不记任何密钥
    return describe(row, provider)
