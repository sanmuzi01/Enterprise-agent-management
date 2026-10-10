"""接入自检：管理员一眼看出“为什么员工还用不了”，每项都给出原因和处理办法。"""
import os
from datetime import timedelta
from typing import Any, Dict, List
from urllib.parse import urlparse

from sqlalchemy import func, select, text

from models.init_db import ExternalEventInbox, ExternalUserBinding
from service.integrations import apps, oauth
from service.integrations.base import PROVIDER_LABELS
from utils.timeutil import utcnow


def _check(key: str, label: str, ok: bool, detail: str, fix: str = "", level: str = "error") -> Dict[str, Any]:
    return {"key": key, "label": label, "ok": ok, "level": "ok" if ok else level, "detail": detail, "fix": "" if ok else fix}


def _trusted(host: str) -> bool:
    hosts = {h.strip().lower() for h in os.getenv("TRUSTED_HOSTS", "127.0.0.1,localhost,api").split(",") if h.strip()}
    return "*" in hosts or host.lower() in hosts or any(h.startswith("*.") and host.lower().endswith(h[1:]) for h in hosts)


def readiness_sync(db, provider: str) -> Dict[str, Any]:
    label = PROVIDER_LABELS[provider]
    row = apps.get_row_sync(db, provider)
    checks: List[Dict[str, Any]] = []
    checks.append(_check("configured", "应用凭证已填写并启用", bool(row and row.enabled),
                         "已启用" if row and row.enabled else ("已保存但未启用" if row else "还没有配置"),
                         f"在下方填写{label}应用的凭证，勾选“启用接入”后保存"))
    tested = bool(row and row.last_health_at and not row.last_error)
    checks.append(_check("credentials", "凭证测试通过", tested,
                         row.last_error if row and row.last_error else ("最近一次测试通过" if tested else "还没有测试过"),
                         "点“测试连接”；失败时核对 App ID / App Secret 是否复制完整"))

    public = oauth.public_base_url()
    parsed = urlparse(public) if public else None
    public_ok = bool(parsed and parsed.scheme == "https" and parsed.hostname not in ("localhost", "127.0.0.1"))
    checks.append(_check("public_url", "已配置公网 HTTPS 地址", public_ok,
                         public or "没有配置 INTEGRATION_PUBLIC_BASE_URL",
                         "在 .env 设置 INTEGRATION_PUBLIC_BASE_URL=https://你的域名（经 nginx 时带 /api）；"
                         "本地联调可用内网穿透生成公网 HTTPS 地址，见 docs/collaboration-integrations.md"))
    if public_ok:
        checks.append(_check("trusted_host", "公网域名在 Host 白名单里", _trusted(parsed.hostname),
                             f"TRUSTED_HOSTS 包含 {parsed.hostname}" if _trusted(parsed.hostname) else f"TRUSTED_HOSTS 里没有 {parsed.hostname}",
                             f"把 {parsed.hostname} 加进 .env 的 TRUSTED_HOSTS 并重启后端，否则平台的回调会被拒绝（400）"))

    since = utcnow() - timedelta(hours=24)
    last = db.execute(select(func.max(ExternalEventInbox.received_at)).where(ExternalEventInbox.provider == provider)).scalar()
    checks.append(_check("callback_seen", f"{label}能访问到回调地址", bool(last and last >= since),
                         f"最近一次收到回调：{(last + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')}（北京时间）" if last else "还没有收到过回调",
                         f"在{label}开放平台填好回调地址并保存（平台会发校验请求）；然后用自己的{label}给机器人发一条消息",
                         level="warning"))

    agents = db.execute(text("SELECT agent_type, COUNT(*) FROM agent WHERE lifecycle_status = 'published' "
                             "AND agent_type IN ('central', 'department') GROUP BY agent_type")).all()
    counts = {t: n for t, n in agents}
    checks.append(_check("agents", "有可用的助手", bool(counts),
                         f"中央助手 {counts.get('central', 0)} 个，部门助手 {counts.get('department', 0)} 个",
                         "在后台“企业智能体”发布中央助手或部门助手，员工发来的消息才有人回答"))

    org = apps.enterprise_id_sync(db)
    members = db.execute(text("SELECT COUNT(*) FROM organization_members WHERE organization_id = :o AND status = 'active'"),
                         {"o": org}).scalar() or 0
    bound = db.execute(select(func.count()).select_from(ExternalUserBinding).where(
        ExternalUserBinding.provider == provider, ExternalUserBinding.status == "active",
        ExternalUserBinding.local_user_id.isnot(None))).scalar() or 0
    checks.append(_check("bindings", "员工已绑定", bound > 0, f"已绑定 {bound} / {members} 人",
                         "员工在“设置 → 飞书 / 钉钉”领取绑定码，私聊机器人发送“绑定 xxxxxx”；或点“提醒未绑定员工”",
                         level="warning"))
    if provider == "feishu":
        checks.append(_check("feishu_publish", "飞书应用已发布、可用范围包含员工", True,
                             "这一项系统查不到，请在飞书开放平台确认：版本管理与发布 → 已发布；可用范围包含要使用的员工",
                             level="info"))
    else:
        checks.append(_check("dingtalk_publish", "钉钉应用已发布、机器人已上线", True,
                             "这一项系统查不到，请在钉钉开放平台确认：应用已发布，机器人已上线、可见范围包含要使用的员工",
                             level="info"))
    ready = all(c["ok"] for c in checks if c["level"] == "error")
    return {"provider": provider, "ready": ready, "checks": checks,
            "oauth_redirect_uri": oauth.redirect_uri(provider) if public_ok else None}


async def invite_unbound(db, operator_id: int, provider: str) -> Dict[str, int]:
    """给所有还没绑定的企业成员发站内通知，告诉他们怎么绑定。每人每天最多一次。"""
    from service import notification_center
    label = PROVIDER_LABELS[provider]
    org = (await db.execute(text("SELECT id FROM organizations ORDER BY id LIMIT 1"))).scalar()
    users = (await db.execute(text(
        "SELECT om.user_id FROM organization_members om JOIN `user` u ON u.id = om.user_id "
        "WHERE om.organization_id = :o AND om.status = 'active' AND COALESCE(u.is_disabled, 0) = 0 "
        "AND om.user_id NOT IN (SELECT local_user_id FROM external_user_binding WHERE provider = :p "
        "AND status = 'active' AND local_user_id IS NOT NULL)"), {"o": org, "p": provider})).scalars().all()
    day = (utcnow() + timedelta(hours=8)).date().isoformat()
    sent = 0
    for user_id in users:
        sent += int(await notification_center.notify(
            db, user_id, "system", f"绑定{label}账号，在{label}里直接用助手",
            body=f"打开“设置 → 飞书 / 钉钉”获取绑定码，私聊{label}里的企业机器人发送“绑定 绑定码”即可。",
            link="/settings/integrations", dedupe_key=f"bind-invite:{provider}:{day}"))
    from service import audit_service
    await audit_service.record_async(operator_id, "integration.bind_invited", resource_type="collaboration_app",
                                     resource_id=0, detail={"provider": provider, "sent": sent})
    return {"sent": sent, "unbound": len(users)}
