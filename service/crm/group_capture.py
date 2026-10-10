"""按群开启的“群消息记录到 CRM”（飞书）。

默认不记录任何群的聊天。销售部门负责人或群主在客户群里 @机器人 发“开启CRM记录”（可以带客户名：“开启CRM记录 华星科技”），
机器人在群里公告“本群消息会记录到 CRM”，之后这个群的消息才会被收下；“关闭CRM记录”随时关闭。

收下的消息先暂存（crm_chat_message），一段对话停下 30 分钟（或攒够 300 条）后，由定时任务整理成一条“群聊”客户活动，
每条带发送人和时间，然后删掉暂存。整理前撤回的消息直接从暂存删掉，不会进 CRM。

前提：飞书应用要开通“获取群组中所有消息”权限，否则机器人只收得到 @ 它的消息。钉钉机器人收不到群里没 @ 它的消息，不支持。
"""
import re
from datetime import timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from models.init_db import CrmChatGroup, CrmChatMessage, EnterpriseRole, Team, TeamMember, User
from service.crm import activities as acts
from service.crm import matching
from service.crm.chat_ingest import beijing, participants, transcript
from service.crm.sources import ActivityDraft
from service.department_access import require_team_member_async
from service.exceptions import AppError, InvalidInput, NotFound, PermissionDenied
from service.integrations.base import ChatLine, logger
from utils.timeutil import utcnow

GAP = timedelta(minutes=30)          # 停下这么久算一段对话结束
MAX_BATCH = 300                      # 一条活动最多这么多条消息

_COMMAND = re.compile(r"^\s*(开启|打开|开始|关闭|停止|结束)\s*CRM\s*记录\s*(.*?)\s*$", re.I)
_STATUS = re.compile(r"^\s*CRM\s*记录\s*(状态)?\s*$", re.I)


def parse_command(text: str) -> Optional[Tuple[str, str]]:
    """("enable", 客户名) / ("disable", "") / ("status", "")；不是这几个命令返回 None。"""
    text = text or ""
    if _STATUS.match(text):
        return "status", ""
    found = _COMMAND.match(text)
    if not found:
        return None
    if found.group(1) in ("关闭", "停止", "结束"):
        return "disable", ""
    return "enable", found.group(2).strip(" ：:，,")


# ------------------------------------------------------------------ 谁能开关

async def _sales_membership(db, user_id: int) -> Optional[Tuple[int, bool]]:
    """(销售部门 ID, 是不是部门负责人)。部门负责人 = 部门管理员角色，或部门的负责人账号。不在销售部门返回 None。"""
    row = (await db.execute(
        select(Team.id, Team.owner_user_id, EnterpriseRole.code)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .join(EnterpriseRole, EnterpriseRole.id == TeamMember.role_id)
        .where(TeamMember.user_id == user_id, TeamMember.status == "active", Team.status == "active",
               Team.department_code == "sales").order_by(Team.id).limit(1))).first()
    if row is None:
        return None
    return row[0], row[2] == "admin" or row[1] == user_id


async def _is_leader(db, user_id: int, team_id: int) -> bool:
    membership = await _sales_membership(db, user_id)
    return bool(membership and membership[0] == team_id and membership[1])


async def _group(db, provider: str, tenant_id: str, chat_id: str) -> Optional[CrmChatGroup]:
    return (await db.execute(select(CrmChatGroup).where(
        CrmChatGroup.provider == provider, CrmChatGroup.tenant_id == tenant_id, CrmChatGroup.chat_id == chat_id))).scalar()


async def _team_name(db, team_id: int) -> str:
    return (await db.execute(select(Team.name).where(Team.id == team_id))).scalar() or f"部门 {team_id}"


# ------------------------------------------------------------------ 群里的命令

async def handle_command(db, adapter, app, msg, user_id: int, action: str, argument: str,
                         chat_info: Callable[[], Awaitable[Dict[str, str]]]) -> str:
    """群里 @机器人 发的开启 / 关闭 / 状态命令，返回回到群里的文字。chat_info：取群名和群主（只在需要时调用）。"""
    if msg.chat_type == "p2p":
        return "请在要记录的客户群里 @我 发送“开启CRM记录”（可以带上客户名称，例如“开启CRM记录 华星科技”）。"
    if not adapter.supports_group_capture:
        return "这个平台的机器人只能收到 @ 它的消息，暂时不能记录整个群的聊天。可以 @我 发“保存到CRM：……”保存单条内容。"
    group = await _group(db, msg.provider, msg.tenant_id, msg.chat_id)
    if action == "status":
        if group is None or group.status != "active":
            return "本群没有开启 CRM 记录，群里的聊天不会被保存。"
        customer = f"记到客户「{group.customer_name}」" if group.customer_name else "自动对应客户"
        return (f"本群正在记录到 CRM（{await _team_name(db, group.team_id)}，{customer}），"
                f"{beijing(group.enabled_at)} 开启，已记录 {group.message_count} 条。")

    membership = await _sales_membership(db, user_id)
    info = await chat_info()
    is_owner = bool(info.get("owner_id")) and info.get("owner_id") == msg.external_user_id
    if action == "disable":
        if group is None or group.status != "active":
            return "本群没有开启 CRM 记录。"
        if not (is_owner or user_id == group.enabled_by or await _is_leader(db, user_id, group.team_id)):
            return "只有开启人、群主或该销售部门的负责人可以关闭本群的 CRM 记录。"
        saved = await close(db, group, user_id, "在群里关闭")
        return "已关闭，本群之后的聊天不再记录到 CRM。" + (f"开启期间的消息已整理成 {saved} 条客户活动。" if saved else "")

    if membership is None:
        return "只有销售部门的同事可以开启本群的 CRM 记录。"
    team_id, leader = membership
    if not (leader or is_owner):
        return "只有销售部门负责人或群主可以开启本群的 CRM 记录（开启后群里所有人的消息都会被记录）。"
    if group is not None and group.status == "active" and group.team_id != team_id:
        return f"本群已经由「{await _team_name(db, group.team_id)}」开启了 CRM 记录，不能重复开启。"

    customer_id, customer_name = None, None
    if argument:
        directory = await acts.load_directory(db, user_id, team_id)
        found = matching.match(directory, text=argument)
        if found.customer_id is None or found.status != "auto":
            names = "、".join(c["name"] for c in found.candidates[:3])
            return (f"没有开启：「{argument}」对不上唯一的客户" + (f"（可能是：{names}）" if names else "") +
                    "。请写客户全称再发一次，或者只发“开启CRM记录”，由系统按聊天内容自动对应客户。")
        customer_id, customer_name = found.customer_id, directory.name_of(found.customer_id)

    now = utcnow()
    if group is None:
        group = CrmChatGroup(provider=msg.provider, tenant_id=msg.tenant_id, chat_id=msg.chat_id, team_id=team_id,
                             enabled_by=user_id, enabled_at=now, message_count=0)
        db.add(group)
    group.team_id, group.customer_id, group.customer_name = team_id, customer_id, customer_name
    group.chat_name = (info.get("name") or group.chat_name or "")[:200] or None
    group.status, group.enabled_by, group.enabled_at = "active", user_id, now
    group.closed_by = group.closed_at = group.close_reason = None
    try:
        await db.commit()
    except IntegrityError:                        # 两个人同时开启
        await db.rollback()
        return "本群刚刚已经开启了 CRM 记录。"
    target = f"记到客户「{customer_name}」" if customer_name else "系统按聊天内容自动对应客户，对不上的由销售在网页上指定"
    return ("已开启：从现在起，本群的聊天会记录到公司的 CRM 客户档案（" + target + "）。\n"
            "请各位知悉。撤回的消息不会被记录；开启人、群主或销售负责人 @我 发“关闭CRM记录”可随时关闭。\n"
            "（如果之后网页上一直显示“还没收到消息”，请管理员在飞书开放平台给应用开通“获取群组中所有消息”权限。）")


# ------------------------------------------------------------------ 收消息

async def capture(db, msg, sender_name: Callable[[str], Awaitable[str]]) -> bool:
    """群里没 @ 机器人的一条消息：本群开启了记录就暂存，返回 True；没开启返回 False（不保存任何内容）。"""
    if not msg.chat_id or not msg.message_id:
        return False
    group = await _group(db, msg.provider, msg.tenant_id, msg.chat_id)
    if group is None or group.status != "active":
        return False
    text = (msg.record_text or msg.text or "").strip()
    if not text:
        return True                               # 开启了，但这条没有可记录的内容（系统消息）
    when = msg.sent_at or utcnow()
    db.add(CrmChatMessage(group_id=group.id, message_id=msg.message_id, sender_id=msg.external_user_id,
                          sender_name=(await sender_name(msg.external_user_id) or "外部成员")[:120],
                          content=text[:5000], sent_at=when))
    group.message_count = (group.message_count or 0) + 1
    group.last_message_at = max(group.last_message_at or when, when)
    try:
        await db.commit()
    except IntegrityError:                        # 同一条消息推了两次
        await db.rollback()
    return True


async def recall(db, provider: str, tenant_id: str, message_id: str) -> int:
    """消息被撤回：还在暂存里的删掉（已经整理进 CRM 的不动，可以在网页上忽略那条活动）。"""
    rows = (await db.execute(select(CrmChatMessage).join(CrmChatGroup, CrmChatGroup.id == CrmChatMessage.group_id).where(
        CrmChatGroup.provider == provider, CrmChatGroup.tenant_id == tenant_id,
        CrmChatMessage.message_id == message_id))).scalars().all()
    for row in rows:
        group = await db.get(CrmChatGroup, row.group_id)
        group.message_count = max((group.message_count or 0) - 1, 0)
        await db.delete(row)
    await db.commit()
    return len(rows)


# ------------------------------------------------------------------ 整理成客户活动

def _sessions(rows: List[CrmChatMessage]) -> List[List[CrmChatMessage]]:
    sessions: List[List[CrmChatMessage]] = []
    for row in rows:
        if sessions and row.sent_at - sessions[-1][-1].sent_at <= GAP and len(sessions[-1]) < MAX_BATCH:
            sessions[-1].append(row)
        else:
            sessions.append([row])
    return sessions


async def _writers(db, group: CrmChatGroup) -> List[int]:
    """用谁的身份把活动记进这个销售部门：开启人优先；开启人已经不在部门了，换部门负责人。"""
    admins = (await db.execute(select(TeamMember.user_id).join(EnterpriseRole, EnterpriseRole.id == TeamMember.role_id).where(
        TeamMember.team_id == group.team_id, TeamMember.status == "active", EnterpriseRole.code == "admin"))).scalars().all()
    owner = (await db.execute(select(Team.owner_user_id).where(Team.id == group.team_id))).scalar()
    ids: List[int] = []
    for uid in [group.enabled_by, *admins, owner]:
        if uid and uid not in ids:
            ids.append(uid)
    return ids


async def _ingest(db, group: CrmChatGroup, draft: ActivityDraft) -> bool:
    for writer in await _writers(db, group):
        try:
            try:
                await acts.ingest(db, writer, group.team_id, draft, group.provider, explicit_customer_id=group.customer_id)
            except NotFound:                      # 绑定的客户已经不在了：按内容自动对应
                await acts.ingest(db, writer, group.team_id, draft, group.provider)
            return True
        except PermissionDenied:
            continue
    return False


async def flush(db, group: CrmChatGroup, *, force: bool = False) -> int:
    """把暂存的消息按对话整理成客户活动。force=False 时，还在进行中的最后一段对话（30 分钟内有新消息）先不整理。
    返回新建的活动数。"""
    rows = (await db.execute(select(CrmChatMessage).where(CrmChatMessage.group_id == group.id)
                             .order_by(CrmChatMessage.sent_at, CrmChatMessage.id))).scalars().all()
    created, now = 0, utcnow()
    for batch in _sessions(list(rows)):
        last = batch[-1]
        if not force and len(batch) < MAX_BATCH and now - last.sent_at < GAP:
            continue
        lines = [ChatLine(r.sender_id or "", r.sender_name or "外部成员", r.content, r.sent_at) for r in batch]
        title = (f"群聊「{group.chat_name or '客户群'}」{beijing(batch[0].sent_at)}"
                 f"–{beijing(last.sent_at)[-5:]}（{len(batch)} 条）")
        draft = ActivityDraft("chat", f"{group.provider}-group:{group.chat_id}:{batch[0].message_id}", last.sent_at,
                              title[:300], transcript(lines), participants=participants(lines))
        try:
            ok = await _ingest(db, group, draft)
        except AppError as exc:                   # 业务系统暂时不可用：留着下次再整理
            logger.warning(f"群聊记录整理失败 group={group.id}：{exc}")
            return created
        if not ok:
            # 部门里已经没有能记录的人：停止记录，暂存删掉（不在平台里无限期保留聊天内容）
            await db.execute(delete(CrmChatMessage).where(CrmChatMessage.group_id == group.id))
            group.status, group.closed_at, group.close_reason = "closed", utcnow(), "部门里没有能记录的成员，已自动停止"
            await db.commit()
            return created
        await db.execute(delete(CrmChatMessage).where(CrmChatMessage.id.in_([r.id for r in batch])))
        await db.commit()
        created += 1
    return created


async def flush_due(db) -> Tuple[int, int]:
    """定时任务：整理所有群里已经结束的对话。关闭了的群，剩下的全部整理。"""
    group_ids = (await db.execute(select(CrmChatMessage.group_id).distinct())).scalars().all()
    created = 0
    for group_id in group_ids:
        group = await db.get(CrmChatGroup, group_id)
        if group is None:
            await db.execute(delete(CrmChatMessage).where(CrmChatMessage.group_id == group_id))
            await db.commit()
            continue
        created += await flush(db, group, force=group.status != "active")
    return created, 0


async def close(db, group: CrmChatGroup, user_id: int, reason: str) -> int:
    saved = await flush(db, group, force=True)
    group.status, group.closed_by, group.closed_at, group.close_reason = "closed", user_id, utcnow(), reason
    await db.commit()
    return saved


# ------------------------------------------------------------------ 网页

def _describe(group: CrmChatGroup, pending: int, names: Dict[int, str]) -> Dict[str, Any]:
    return {"id": group.id, "provider": group.provider, "chat_name": group.chat_name or "（未取到群名）",
            "customer_id": group.customer_id, "customer_name": group.customer_name, "status": group.status,
            "enabled_by": names.get(group.enabled_by, ""), "enabled_at": _iso(group.enabled_at),
            "closed_at": _iso(group.closed_at), "close_reason": group.close_reason,
            "message_count": group.message_count or 0, "pending_messages": pending,
            "last_message_at": _iso(group.last_message_at)}


def _iso(value) -> Optional[str]:
    return value.isoformat() + "Z" if value else None             # 库里存的是 UTC，带上 Z 让网页按本地时区显示


async def list_groups(db, user_id: int, team_id: int) -> Dict[str, Any]:
    await require_team_member_async(db, user_id, team_id, "crm")
    groups = (await db.execute(select(CrmChatGroup).where(CrmChatGroup.team_id == team_id)
                               .order_by(CrmChatGroup.status, CrmChatGroup.enabled_at.desc()).limit(200))).scalars().all()
    ids = [g.id for g in groups]
    pending = dict((await db.execute(select(CrmChatMessage.group_id, func.count()).where(
        CrmChatMessage.group_id.in_(ids)).group_by(CrmChatMessage.group_id))).all()) if ids else {}
    people = {g.enabled_by for g in groups}
    names = dict((await db.execute(select(User.id, User.name).where(User.id.in_(people)))).all()) if people else {}
    return {"items": [_describe(g, pending.get(g.id, 0), names) for g in groups],
            "can_manage": await _is_leader(db, user_id, team_id)}


async def close_from_web(db, user_id: int, team_id: int, group_id: int) -> Tuple[Dict[str, Any], CrmChatGroup]:
    await require_team_member_async(db, user_id, team_id, "crm")
    group = await db.get(CrmChatGroup, group_id)
    if group is None or group.team_id != team_id:
        raise NotFound("没有这个群")
    if group.status != "active":
        raise InvalidInput("这个群已经关闭了记录")
    if user_id != group.enabled_by and not await _is_leader(db, user_id, team_id):
        raise PermissionDenied("只有开启人或销售部门负责人可以关闭")
    saved = await close(db, group, user_id, "在网页上关闭")
    names = dict((await db.execute(select(User.id, User.name).where(User.id == group.enabled_by))).all())
    return {"saved": saved, "group": _describe(group, 0, names)}, group
