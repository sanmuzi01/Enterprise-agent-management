"""站内通知推送到员工自己的飞书 / 钉钉（消费 notification.command.v1）。

可靠性（复用 service/events 的发件箱）：
- 通知和推送事件在同一个事务里写入（notification_center.notify），通知一定先存进库，飞书 / 钉钉挂了不影响业务；
- 发送失败（网络、平台限流、令牌失效）抛异常 → 按指数退避重试；平台恢复后自动补发，晚到的消息注明原通知时间；
- 重试用尽进死信（问题中心里能看到），管理员修好配置后在“问题中心 → 死信”里重新投递；
- 每个绑定一条事件：飞书发成功、钉钉失败时，只重试钉钉那条，不会给飞书重复发。

发送前按“此刻”的数据再判断一次：通知已经在网页上读过、绑定已停用、接入已关闭，都不再发（算处理完成）。
"""
import asyncio
from datetime import timedelta
from typing import Any, Dict, Optional

from sqlalchemy import select

from models.init_db import ExternalUserBinding, Notification, SessionLocal
from service.integrations import apps
from service.integrations.base import IntegrationError
from service.integrations.registry import get_adapter
from utils.timeutil import utcnow

LATE_AFTER = timedelta(minutes=10)
MAX_ATTEMPTS = 10              # 退避 2、4、8……最长 5 分钟一次，约 25 分钟后进死信


def compose(row: Notification, now=None) -> str:
    from service.integrations.dispatcher import web_url
    lines = [f"【{row.title}】"]
    if row.body:
        lines.append(row.body)
    if row.link and web_url():
        lines.append(f"查看：{web_url()}{row.link}")
    if (now or utcnow()) - row.created_at > LATE_AFTER:
        lines.append(f"（这是 {(row.created_at + timedelta(hours=8)):%m-%d %H:%M} 的通知，平台恢复后补发）")
    return "\n".join(lines)


def _context(provider: str, external_user_id: str) -> Dict[str, Any]:
    return {"open_id": external_user_id} if provider == "feishu" else {"staff_id": external_user_id}


def deliver_sync(payload: Dict[str, Any]) -> Optional[str]:
    """发一条。返回不发的原因（None 表示发出去了）；发送失败抛 IntegrationError（交给重试）。"""
    provider, user_id = payload.get("provider"), payload.get("user_id")
    db = SessionLocal()
    try:
        row = db.get(Notification, payload.get("notification_id"))
        if row is None or row.user_id != user_id:
            return "通知不存在"
        if row.read_at is not None:
            return "已在网页上读过"
        app = apps.load_enabled_sync(db, provider)
        if app is None:
            return "接入已关闭"
        external_id = db.execute(select(ExternalUserBinding.external_user_id).where(
            ExternalUserBinding.provider == provider, ExternalUserBinding.external_tenant_id == app.app_id,
            ExternalUserBinding.local_user_id == user_id, ExternalUserBinding.status == "active")).scalar()
        if not external_id:
            return "绑定已解除或停用"
        text = compose(row)
    finally:
        db.close()
    get_adapter(provider).send_text(app, _context(provider, external_id), text)
    return None


async def handle(event: Dict[str, Any]) -> None:
    try:
        await asyncio.to_thread(deliver_sync, event["payload"])
    except IntegrationError as exc:
        raise IntegrationError(f"推送到{event['payload'].get('provider')}失败：{exc}") from None
