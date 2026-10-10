"""外部回调的统一处理：验签解密 → 去重 → 找到平台账号和部门 → 选中央 Agent → 走同一条对话流程 → 回复 / 发确认卡片。

回调必须很快应答（飞书 3 秒、钉钉机器人几秒内），而 Agent 一轮可能要几十秒，所以：
  handle() 只做校验、去重、登记，马上返回平台要的应答；真正的处理（process_message / process_card_action）
  作为后台任务在应答之后执行（FastAPI BackgroundTasks），处理结果写回 external_event_inbox。

权限：每次都按“此刻”的平台数据判断——绑定是否有效、账号是否停用、属于哪个部门、能用哪个 Agent，
和网页完全一致；调岗、停用、离职立即生效。卡片按钮只带一次性确认令牌，令牌必须属于点按钮的人
（tool_confirmation_service 校验归属、过期、只能用一次）。
"""
import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from models.init_db import SessionLocal
from service.integrations import apps, event_inbox, identity
from service.integrations.base import (PROVIDER_LABELS, BusinessCard, CardAction, CardField, ConfirmAction, Handshake,
                                       Ignored, InboundMessage, IntegrationError, OpenUrlAction, OrgChange, RejectAction,
                                       VerificationError, logger)
from service.integrations.registry import get_adapter
from utils.cache import TTLCache
from utils.timeutil import utcnow

MAX_BODY_BYTES = 1024 * 1024
_conversations = TTLCache(default_ttl=1800, namespace="integration_conversation")
NEW_CONVERSATION_WORDS = {"新对话", "重新开始", "/new", "/reset"}


@dataclass
class Outcome:
    status_code: int
    body: Dict[str, Any]
    background: Optional[Callable[[], Awaitable[None]]] = None
    log: Dict[str, Any] = field(default_factory=dict)


def _sync(fn, *args, **kwargs):
    db = SessionLocal()
    try:
        return fn(db, *args, **kwargs)
    finally:
        db.close()


async def _in_thread(fn, *args, **kwargs):
    return await asyncio.to_thread(_sync, fn, *args, **kwargs)


# ---------------------------------------------------------------- 入口

async def handle(provider: str, kind: str, headers: Dict[str, str], query: Dict[str, str], body: bytes) -> Outcome:
    """kind: events / card-actions。"""
    adapter = get_adapter(provider)
    if len(body) > MAX_BODY_BYTES:
        return Outcome(413, {"detail": "请求体过大"})
    app = await _in_thread(apps.load_enabled_sync, provider)
    if app is None:
        return Outcome(404, {"detail": f"{PROVIDER_LABELS[provider]}接入未启用"})
    try:
        parsed = (adapter.parse_event if kind == "events" else adapter.parse_card_action)(app, headers, query, body)
    except VerificationError as exc:
        logger.warning(f"{provider} 回调校验失败：{exc}")
        return Outcome(401, {"detail": f"回调校验失败：{exc}"})

    if isinstance(parsed, Handshake):
        return Outcome(200, parsed.response)
    if isinstance(parsed, Ignored):
        if parsed.event_id:
            inbox_id = await _in_thread(event_inbox.claim_sync, provider, parsed.tenant_id, parsed.event_id, parsed.event_type)
            if inbox_id:
                await _in_thread(event_inbox.finish_sync, inbox_id, "ignored", parsed.reason)
        return Outcome(200, parsed.ack or {})

    event_type = getattr(parsed, "event_type", None) or ("card.action" if isinstance(parsed, CardAction) else "event")
    inbox_id = await _in_thread(event_inbox.claim_sync, provider, parsed.tenant_id, parsed.event_id, event_type)
    if inbox_id is None:                       # 平台重推的同一个事件：确认收到，不再处理
        return Outcome(200, parsed.ack or {}, log={"duplicate": True})

    if isinstance(parsed, OrgChange):
        await _in_thread(_apply_org_change, parsed, inbox_id)
        return Outcome(200, parsed.ack or {})
    if isinstance(parsed, CardAction):
        body_out = adapter.card_action_response(app, "已收到，正在处理…", True)
        return Outcome(200, body_out, background=lambda: process_card_action(app, parsed, inbox_id))
    return Outcome(200, parsed.ack or {}, background=lambda: process_message(app, parsed, inbox_id))


def _apply_org_change(db, change: OrgChange, inbox_id: int) -> None:
    from sqlalchemy import update
    from models.init_db import ExternalUserBinding
    from service import audit_service
    if change.left_user_ids:
        db.execute(update(ExternalUserBinding).where(
            ExternalUserBinding.provider == change.provider, ExternalUserBinding.external_tenant_id == change.tenant_id,
            ExternalUserBinding.external_user_id.in_(change.left_user_ids)).values(status="disabled"))
        db.commit()
        audit_service.record(0, "integration.user_left",          # 0 = 系统（外部平台推送的离职事件） resource_type="external_user_binding", resource_id=0,
                             detail={"provider": change.provider, "external_user_ids": change.left_user_ids[:50]})
    event_inbox.finish_sync(db, inbox_id, "done")


# ---------------------------------------------------------------- 选 Agent

async def choose_agent(db, user_id: int) -> Optional[Tuple[int, str]]:
    """员工在外部平台里对话用哪个 Agent：有可用的中央 Agent 就用它（由它按问题转给部门 Agent），
    否则用员工所在部门已发布的部门 Agent。都没有返回 None。每次按当前数据算，不缓存。"""
    from service.enterprise_workspace_service import get_workspace
    workspace = await get_workspace(db, user_id)
    agents = workspace.get("agents") or []
    central = [a for a in agents if a["agent_type"] == "central"]
    if central:
        return central[0]["id"], central[0]["name"]
    team_ids = {d["id"] for d in workspace.get("departments") or []}
    own = [a for a in agents if a["agent_type"] == "department" and a.get("team_id") in team_ids]
    return (own[0]["id"], own[0]["name"]) if own else None


def _conversation_key(provider: str, user_id: int, chat_id: Optional[str]) -> Tuple[str, str, str]:
    return ("conv", f"{provider}:{user_id}", chat_id or "p2p")


# ---------------------------------------------------------------- 消息

async def process_message(app, msg: InboundMessage, inbox_id: int) -> None:
    adapter = get_adapter(msg.provider)
    status, error = "done", None
    try:
        await _process_message(app, adapter, msg)
    except Exception as exc:  # noqa: BLE001 —— 后台任务：记下原因，尽量告诉员工
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        logger.error(f"{msg.provider} 消息处理失败 event={msg.event_id}", exc_info=True)
        _safe_send(adapter, app, msg.reply_context, "处理这条消息时出错了，请稍后再试，或到网页工作台使用。")
    await _in_thread(event_inbox.finish_sync, inbox_id, status, error)


def _safe_send(adapter, app, context: Dict[str, Any], text: str) -> None:
    try:
        adapter.send_text(app, context, text)
    except Exception:  # noqa: BLE001
        logger.warning("给外部平台回消息失败", exc_info=True)


async def _process_message(app, adapter, msg: InboundMessage) -> None:
    from service.integrations import oauth, self_binding
    label = PROVIDER_LABELS[msg.provider]
    if not await asyncio.to_thread(adapter.addressed_to_bot, app, msg):
        return                                   # 群里 @ 的不是本机器人：不处理、不回复
    code = self_binding.parse_command(msg.text)
    if code is not None:
        if msg.chat_type != "p2p":
            reply = "绑定码请私聊我发送，不要发在群里。"
        else:
            name = await asyncio.to_thread(adapter.user_name, app, msg.external_user_id)
            reply = await _in_thread(self_binding.redeem_sync, msg.provider, msg.tenant_id, msg.external_user_id, code, name)
        await asyncio.to_thread(adapter.send_text, app, msg.reply_context, reply)
        return
    user_id, reason = await _in_thread(identity.resolve_sync, msg.provider, msg.tenant_id, msg.external_user_id)
    if user_id is None:
        if reason == "unbound":
            name = await asyncio.to_thread(adapter.user_name, app, msg.external_user_id)
            await _in_thread(self_binding.remember_visitor_sync, msg.provider, msg.tenant_id, msg.external_user_id, name)
        web = oauth.web_base_url()
        reply = identity.REASONS[reason].format(platform=label, web_hint=f"平台地址：{web}/settings/integrations\n" if web else "")
        await asyncio.to_thread(adapter.send_text, app, msg.reply_context, reply)
        return
    if not msg.text:
        await asyncio.to_thread(adapter.send_text, app, msg.reply_context, "目前只支持文字消息，请直接用文字描述你要办的事。")
        return
    key = _conversation_key(msg.provider, user_id, msg.chat_id)
    if msg.text.strip() in NEW_CONVERSATION_WORDS:
        _conversations.invalidate(key)
        await asyncio.to_thread(adapter.send_text, app, msg.reply_context, "好的，已开始新的对话。")
        return

    from models.async_db import AsyncSessionLocal
    from models.user_async_dao import get_user_by_id_async
    from service import chat_pipeline
    from service.crm import chat_ingest
    from utils.rate_limit import LimitExceeded

    if chat_ingest.wants_crm(msg.text):
        # 员工明确要求“保存到CRM”：存成客户活动，不交给助手
        async with AsyncSessionLocal() as db:
            reply = await chat_ingest.save(db, user_id, msg.provider, msg.message_id or msg.event_id, msg.text)
        await asyncio.to_thread(adapter.send_text, app, msg.reply_context, reply)
        return

    since = utcnow().replace(microsecond=0)
    async with AsyncSessionLocal() as db:
        chosen = await choose_agent(db, user_id)
        if chosen is None:
            await asyncio.to_thread(adapter.send_text, app, msg.reply_context,
                                    "你所在的部门还没有可用的助手，请联系管理员发布部门助手后再试。")
            return
        agent_id, _ = chosen
        cached = _conversations.get(key) or {}
        conversation_id = cached.get("conversation_id") if cached.get("agent_id") == agent_id else None
        user = await get_user_by_id_async(db, user_id)
        try:
            try:
                result, _plan = await chat_pipeline.run_turn(db, user, agent_id, msg.text, conversation_id)
            except ValueError:
                if conversation_id is None:
                    raise
                # 原会话所在的 Agent 已经不能用了（调岗、助手下线）：按现在的部门重新开始
                _conversations.invalidate(key)
                result, _plan = await chat_pipeline.run_turn(db, user, agent_id, msg.text, None)
        except LimitExceeded as exc:
            await asyncio.to_thread(adapter.send_text, app, msg.reply_context, exc.message)
            return
        except ValueError as exc:
            await asyncio.to_thread(adapter.send_text, app, msg.reply_context, str(exc))
            return

    if result.get("conversation_id"):
        _conversations.set(key, {"conversation_id": result["conversation_id"], "agent_id": agent_id})
    reply = result.get("answer") or result.get("message") or "（没有生成回答）"
    await asyncio.to_thread(adapter.send_text, app, msg.reply_context, reply)

    # 这一轮里 Agent 请求了高风险操作：给本人发确认卡片（群聊里也单独私发，不在群里展示要确认的内容）
    pending = await _in_thread(_pending_confirmations, user_id, since)
    private = _private_context(msg)
    for card in pending:
        await asyncio.to_thread(adapter.send_card, app, private, card)


def _private_context(msg: InboundMessage) -> Dict[str, Any]:
    if msg.chat_type == "p2p":
        return msg.reply_context
    if msg.provider == "feishu":
        return {"open_id": msg.external_user_id}
    return {"staff_id": msg.external_user_id}


def web_url() -> str:
    return os.getenv("INTEGRATION_WEB_BASE_URL", "").rstrip("/")


def confirmation_card(row) -> BusinessCard:
    from service.tools.base import ToolRegistry
    tool_class = ToolRegistry.get(row.tool_name)
    title = row.tool_name
    if tool_class is not None:
        try:
            title = (tool_class().get_description() or row.tool_name).split("。")[0][:40]
        except Exception:  # noqa: BLE001
            title = row.tool_name
    try:
        args = json.loads(row.tool_args or "{}")
    except ValueError:
        args = {}
    fields: List[CardField] = []
    for name, value in list(args.items())[:10]:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        fields.append(CardField(str(name), text[:200]))
    actions = [ConfirmAction(row.token), RejectAction(row.token)]
    if web_url():
        actions.append(OpenUrlAction(f"{web_url()}/confirmations"))
    return BusinessCard(title=f"需要你确认：{title}", fields=fields, actions=actions, tone="warning",
                        note="确认后才会真正执行，10 分钟内有效，只有你本人能确认。")


def _pending_confirmations(db, user_id: int, since) -> List[BusinessCard]:
    from sqlalchemy import select
    from models.init_db import ToolConfirmation
    rows = db.execute(select(ToolConfirmation).where(
        ToolConfirmation.user_id == user_id, ToolConfirmation.status == "pending",
        ToolConfirmation.created_at >= since).order_by(ToolConfirmation.id).limit(5)).scalars().all()
    return [confirmation_card(row) for row in rows]


# ---------------------------------------------------------------- 卡片按钮

async def process_card_action(app, action: CardAction, inbox_id: int) -> None:
    from service import tool_confirmation_service as confirmations
    adapter = get_adapter(action.provider)
    label = PROVIDER_LABELS[action.provider]
    status, error = "done", None
    try:
        user_id, reason = await _in_thread(identity.resolve_sync, action.provider, action.tenant_id, action.external_user_id)
        if user_id is None:
            await asyncio.to_thread(adapter.send_text, app, action.reply_context,
                                    identity.REASONS[reason].format(platform=label, web_hint=""))
            status, error = "ignored", reason
        else:
            try:
                if action.action == "confirm":
                    outcome = await confirmations.confirm_and_execute_async(action.token, user_id)
                    text = f"已执行。结果：{_summary(outcome.get('result'))}"
                else:
                    await confirmations.reject_async(action.token, user_id)
                    text = "已取消，这次操作不会执行。"
            except confirmations.ConfirmationError as exc:
                text, status, error = exc.message, "ignored", exc.message
            await asyncio.to_thread(adapter.send_text, app, action.reply_context, text)
    except Exception as exc:  # noqa: BLE001
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        logger.error(f"{action.provider} 卡片回调处理失败", exc_info=True)
        _safe_send(adapter, app, action.reply_context, "处理确认时出错了，请到网页工作台查看这次操作的状态。")
    await _in_thread(event_inbox.finish_sync, inbox_id, status, error)


def _summary(result: Any) -> str:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            text = data.get("message") or data.get("error") or text
    except (TypeError, ValueError):
        pass
    return str(text)[:500]


# ---------------------------------------------------------------- 管理端

def test_connection_sync(db, provider: str) -> Dict[str, Any]:
    row = apps.get_row_sync(db, provider)
    if row is None:
        raise IntegrationError("还没有配置这个平台")
    try:
        get_adapter(provider).test_connection(apps.credentials(row))
        row.last_error, ok = None, True
    except IntegrationError as exc:
        row.last_error, ok = str(exc)[:500], False
    row.last_health_at = utcnow()
    db.commit()
    return {"ok": ok, "error": row.last_error, "checked_at": row.last_health_at.isoformat()}


def sync_organization_sync(db, provider: str, operator_id: int) -> Dict[str, Any]:
    row = apps.get_row_sync(db, provider)
    if row is None:
        raise IntegrationError("还没有配置这个平台")
    data = get_adapter(provider).fetch_organization(apps.credentials(row))
    return identity.sync_organization_sync(db, operator_id, row.organization_id, provider, data)


def health_sync(db, provider: str) -> Dict[str, Any]:
    from service.integrations import oauth
    from datetime import timedelta
    from sqlalchemy import func, select
    from models.init_db import ExternalEventInbox, ExternalUserBinding
    row = apps.get_row_sync(db, provider)
    since = utcnow() - timedelta(hours=24)
    events = dict(db.execute(select(ExternalEventInbox.status, func.count()).where(
        ExternalEventInbox.provider == provider, ExternalEventInbox.received_at >= since)
        .group_by(ExternalEventInbox.status)).all())
    bindings = dict(db.execute(select(ExternalUserBinding.status, func.count()).where(
        ExternalUserBinding.provider == provider).group_by(ExternalUserBinding.status)).all())
    failures = [{"event_type": e.event_type, "received_at": e.received_at.isoformat(), "error": e.last_error}
                for e in db.execute(select(ExternalEventInbox).where(
                    ExternalEventInbox.provider == provider, ExternalEventInbox.status == "failed")
                    .order_by(ExternalEventInbox.id.desc()).limit(10)).scalars().all()]
    return {**apps.describe(row, provider), "events_24h": events, "bindings": bindings, "recent_failures": failures,
            "public_base_url": oauth.public_base_url() or None,
            "callback_paths": {"events": f"/integrations/{provider}/events",
                               "card_actions": f"/integrations/{provider}/card-actions"}}
