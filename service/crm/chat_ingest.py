"""飞书 / 钉钉聊天内容进 CRM：只处理员工明确要求保存的消息。

员工给机器人发“保存到CRM：……”（或转发聊天记录时带上这句话），这条内容才会存成客户活动；
不读取员工之间的私聊，也不会把发给机器人的普通对话自动存进 CRM。
"""
import re
from typing import Any, Dict, Optional

from sqlalchemy import select

from models.init_db import Team, TeamMember
from service.crm import activities as acts
from service.crm.sources import ActivityDraft
from service.exceptions import AppError
from utils.timeutil import utcnow

PREFIX = re.compile(r"^\s*(?:#\s*CRM|(?:保存|存|记录|记)(?:到|进)\s*CRM)\s*[:：,，]?\s*", re.I)


def wants_crm(text: str) -> bool:
    return bool(PREFIX.match(text or ""))


async def _sales_team(db, user_id: int) -> Optional[int]:
    return (await db.execute(select(Team.id).join(TeamMember, TeamMember.team_id == Team.id).where(
        TeamMember.user_id == user_id, TeamMember.status == "active", Team.status == "active",
        Team.department_code == "sales").order_by(Team.id).limit(1))).scalar()


async def save(db, user_id: int, provider: str, message_id: str, text: str) -> str:
    """返回给员工看的结果。"""
    content = PREFIX.sub("", text or "", count=1).strip()
    if not content:
        return "请把要保存的内容写在“保存到CRM：”后面，例如：保存到CRM：华星科技王总说下周三前给预算答复。"
    team_id = await _sales_team(db, user_id)
    if team_id is None:
        return "你不在销售部门，不能把内容保存到 CRM。"
    draft = ActivityDraft("chat", f"{provider}:{message_id}", utcnow(), content.splitlines()[0][:60], content[:20000])
    try:
        result = await acts.ingest(db, user_id, team_id, draft, provider)
    except AppError as exc:
        return f"没有保存：{exc}"
    activity: Dict[str, Any] = result["activity"] or {}
    if result["duplicate"]:
        return "这条内容已经保存过了，不会重复记录。"
    if activity.get("customer_id"):
        return f"已保存到客户「{activity.get('customer_name')}」的时间线。"
    candidates = activity.get("match_candidates") or []
    if candidates:
        names = "、".join(c["name"] for c in candidates[:3])
        return f"已保存，但不确定是哪个客户（可能是：{names}）。请到网页工作台的“待归属活动”里选一下。"
    return "已保存，但没有对上任何客户。可以在内容里写上客户名称，或到网页工作台的“待归属活动”里指定客户。"
