"""飞书 / 钉钉聊天内容进 CRM：只处理员工明确交给机器人的内容。

- 员工给机器人发“保存到CRM：……”，这条内容存成客户活动；
- 员工把和客户的聊天记录合并转发给机器人（飞书），整段记录存成一条“群聊 / 聊天”活动，每条带发送人和时间；
- 按群开启的“群消息记录到 CRM”见 service/crm/group_capture.py。
不读取员工之间的私聊，也不会把发给机器人的普通对话自动存进 CRM。
"""
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from models.init_db import Team, TeamMember
from service.crm import activities as acts
from service.crm.sources import ActivityDraft
from service.integrations.base import ChatLine
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
    if result["duplicate"]:
        return "这条内容已经保存过了，不会重复记录。"
    return reply_for(result["activity"] or {})


def beijing(value) -> str:
    return (value + timedelta(hours=8)).strftime("%m-%d %H:%M")


def transcript(lines: List[ChatLine], limit: int = 20000) -> str:
    """聊天记录排成“[10-09 14:03] 王总：……”一行一条（北京时间）。超长时保留最后的部分（最近的对话更有用）。"""
    rows = [f"[{beijing(line.sent_at)}] {line.sender_name}：{line.text}" for line in lines]
    text = "\n".join(rows)
    if len(text) > limit:
        text = "（前面的记录过长，已省略）\n" + text[-limit:]
    return text


def participants(lines: List[ChatLine]) -> List[Dict[str, str]]:
    seen: Dict[str, Dict[str, str]] = {}
    for line in lines:
        seen.setdefault(line.sender_name, {"name": line.sender_name, "email": "", "role": "chat"})
    return list(seen.values())[:50]


async def save_forward(db, user_id: int, provider: str, message_id: str, lines: List[ChatLine]) -> str:
    """员工合并转发给机器人的聊天记录：整段存成一条客户活动（同一次转发只存一次）。"""
    if not lines:
        return "这段聊天记录里没有能读取的文字（图片、文件暂时不能识别），没有保存。"
    team_id = await _sales_team(db, user_id)
    if team_id is None:
        return "转发聊天记录目前用于保存到 CRM，只有销售部门的同事可以用。"
    people = participants(lines)
    names = "、".join(p["name"] for p in people[:4]) + ("等" if len(people) > 4 else "")
    title = f"聊天记录：{names}（{len(lines)} 条，{beijing(lines[0].sent_at)} 起）"
    draft = ActivityDraft("chat", f"{provider}:{message_id}", lines[-1].sent_at, title[:300], transcript(lines),
                          participants=people)
    try:
        result = await acts.ingest(db, user_id, team_id, draft, provider)
    except AppError as exc:
        return f"没有保存：{exc}"
    if result["duplicate"]:
        return "这段聊天记录已经保存过了，不会重复记录。"
    return f"收到 {len(lines)} 条聊天记录。" + reply_for(result["activity"] or {})


def reply_for(activity: Dict[str, Any]) -> str:
    if activity.get("customer_id"):
        return f"已保存到客户「{activity.get('customer_name')}」的时间线。"
    candidates = activity.get("match_candidates") or []
    if candidates:
        names = "、".join(c["name"] for c in candidates[:3])
        return f"已保存，但不确定是哪个客户（可能是：{names}）。请到网页工作台的“待归属活动”里选一下。"
    return "已保存，但没有对上任何客户。可以在内容里写上客户名称，或到网页工作台的“待归属活动”里指定客户。"
