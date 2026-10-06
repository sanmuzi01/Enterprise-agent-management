"""事件消费者登记：导入这个模块就会把所有消费者注册进运行器（应用启动和测试都要导入它）。

- batch_item_worker：整理批量材料里的一份（automation.job.v1）；重启后会接着处理没做完的，重试用尽标成失败。
- issue_notifier：问题中心出现严重/高优先级的新问题或问题复发时，通知所有管理员（platform.issue.v1）。
"""
from typing import Any, Dict, List

from sqlalchemy import select

from service.events.runner import Consumer, register
from service import automation_batch_service as batches

register(Consumer(name="batch_item_worker", topics=["automation.job.v1"], handler=batches.handle_item_event, max_attempts=5,
                  concurrency=batches.CONCURRENCY, timeout_seconds=150, on_dead=batches.handle_item_dead))


async def admin_user_ids(db) -> List[int]:
    import os
    from models.init_db import Role, User, association_table
    from service.admin_service import ADMIN_ROLE_NAMES
    names = [n.strip() for n in os.getenv("ADMIN_USER_NAMES", "admin").split(",") if n.strip()]
    by_name = (await db.execute(select(User.id).where(User.name.in_(names)))).scalars().all()
    by_role = (await db.execute(select(association_table.c.user_id).join(Role, Role.id == association_table.c.role_id)
                                .where(Role.role_name.in_(ADMIN_ROLE_NAMES)))).scalars().all()
    return sorted({*by_name, *by_role})


async def notify_admins_of_issue(event: Dict[str, Any]) -> None:
    from models.async_db import AsyncSessionLocal
    from service import notification_center
    payload = event["payload"]
    regressed = event["event_type"] == "issue.regressed"
    if not regressed and payload.get("severity") not in ("high", "critical"):
        return
    title = f"{'问题复发' if regressed else '新问题'}：{payload['title']}"
    body = f"{payload['issue_no']} · 严重程度 {payload.get('severity_label') or payload.get('severity')} · 错误码 {payload['error_code']}"
    key = f"issue:{payload['issue_no']}:{event['event_type']}:{payload.get('regress_count', 0)}"
    async with AsyncSessionLocal() as db:
        for user_id in await admin_user_ids(db):
            await notification_center.notify(db, user_id, "system", title, body=body, link="/admin/issues", dedupe_key=key)


register(Consumer(name="issue_notifier", topics=["platform.issue.v1"], handler=notify_admins_of_issue, max_attempts=5))
