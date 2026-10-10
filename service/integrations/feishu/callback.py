"""飞书回调解析：事件订阅（加密 + 签名）和卡片按钮回调。

只接受“配置了 Encrypt Key”的应用：飞书只有开启加密后才会给回调签名，不加密的回调只带一个固定的 Verification Token，
抓到一次就能伪造，所以这里不接受明文回调（url_verification 握手除外——它本身不带签名，只核对 Token）。

校验顺序：解密 → 握手直接回 challenge → 验签名（时间戳 + nonce + Encrypt Key + 原始请求体）→ 时间戳 5 分钟内 →
nonce 没用过 → Token 和 App ID 对得上 → 再看事件内容。任何一步不通过都抛 VerificationError（路由返回 401）。
"""
import json
import re
from typing import Any, Dict

from service.integrations.base import (CardAction, Handshake, Ignored, InboundMessage, OrgChange, ParsedEvent,
                                       VerificationError, check_timestamp, parse_button_value, remember_nonce)
from service.integrations.feishu import signature

_MENTION = re.compile(r"@_user_\d+")


def _header(headers: Dict[str, str], name: str) -> str:
    return headers.get(name) or headers.get(name.lower()) or ""


def _load(body: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise VerificationError("请求体不是 JSON") from None
    if not isinstance(data, dict):
        raise VerificationError("请求体不是 JSON 对象")
    return data


def _check_token(app, token: str) -> None:
    if not app.verification_token or not signature.same(app.verification_token, token or ""):
        raise VerificationError("Verification Token 不对")


def open_payload(app, headers: Dict[str, str], body: bytes) -> Dict[str, Any]:
    """解密并校验，返回飞书事件的明文 JSON；握手请求返回 {"__handshake__": challenge}。"""
    if not app.encrypt_key:
        raise VerificationError("应用没有配置 Encrypt Key，不接受未加密的回调")
    outer = _load(body)
    if "encrypt" not in outer:
        raise VerificationError("回调没有加密")
    data = _load(signature.decrypt(app.encrypt_key, str(outer["encrypt"])).encode("utf-8"))
    if data.get("type") == "url_verification":
        _check_token(app, data.get("token"))
        return {"__handshake__": str(data.get("challenge") or "")}

    timestamp, nonce = _header(headers, "X-Lark-Request-Timestamp"), _header(headers, "X-Lark-Request-Nonce")
    expected = signature.event_signature(timestamp, nonce, app.encrypt_key, body)
    if not signature.same(expected, _header(headers, "X-Lark-Signature")):
        raise VerificationError("签名不对")
    check_timestamp(timestamp)
    remember_nonce("feishu", nonce)

    header = data.get("header") or {}
    _check_token(app, header.get("token") or data.get("token"))
    if header.get("app_id") and header.get("app_id") != app.app_id:
        raise VerificationError("回调不是发给这个应用的")
    return data


def parse(app, headers: Dict[str, str], body: bytes) -> ParsedEvent:
    data = open_payload(app, headers, body)
    if "__handshake__" in data:
        return Handshake({"challenge": data["__handshake__"]})
    header, event = data.get("header") or {}, data.get("event") or {}
    tenant, event_id, event_type = app.app_id, str(header.get("event_id") or ""), str(header.get("event_type") or "")
    if not event_id:
        raise VerificationError("缺少 event_id")

    if event_type == "im.message.receive_v1":
        return _message(tenant, event_id, event)
    if event_type == "card.action.trigger":
        return _card(tenant, event_id, event)
    if event_type == "contact.user.deleted_v3":
        open_id = ((event.get("object") or {}).get("open_id"))
        return OrgChange("feishu", tenant, event_id, event_type, [open_id] if open_id else [])
    if event_type == "contact.user.updated_v3":
        obj = event.get("object") or {}
        if (obj.get("status") or {}).get("is_resigned") and obj.get("open_id"):
            return OrgChange("feishu", tenant, event_id, event_type, [obj["open_id"]])
        return Ignored("员工信息变动（不是离职）", tenant, event_id, event_type)
    return Ignored(f"不处理的事件类型 {event_type}", tenant, event_id, event_type)


def _message(tenant: str, event_id: str, event: Dict[str, Any]) -> ParsedEvent:
    sender, message = event.get("sender") or {}, event.get("message") or {}
    if sender.get("sender_type") != "user":
        return Ignored("不是员工发的消息", tenant, event_id, "im.message.receive_v1")
    ids = sender.get("sender_id") or {}
    chat_type = message.get("chat_type") or "p2p"
    if chat_type != "p2p" and not message.get("mentions"):
        # 群聊只处理 @ 机器人的消息（机器人默认也只收得到这些），不读群里其他人的聊天
        return Ignored("群消息没有 @ 机器人", tenant, event_id, "im.message.receive_v1")
    text = ""
    if message.get("message_type") == "text":
        try:
            text = str(json.loads(message.get("content") or "{}").get("text") or "")
        except ValueError:
            text = ""
        text = _MENTION.sub("", text).strip()
    return InboundMessage(
        provider="feishu", tenant_id=tenant, event_id=event_id, external_user_id=str(ids.get("open_id") or ""),
        union_id=ids.get("union_id"), text=text, chat_id=message.get("chat_id"),
        chat_type="p2p" if chat_type == "p2p" else "group", message_id=message.get("message_id"),
        reply_context={"message_id": message.get("message_id"), "open_id": ids.get("open_id"), "chat_id": message.get("chat_id")},
        event_type="im.message.receive_v1")


def _card(tenant: str, event_id: str, event: Dict[str, Any]) -> CardAction:
    operator = event.get("operator") or {}
    action, token = parse_button_value((event.get("action") or {}).get("value"))
    open_id = str(operator.get("open_id") or "")
    if not open_id:
        raise VerificationError("缺少操作人")
    return CardAction("feishu", tenant, open_id, action, token, event_id=event_id, union_id=operator.get("union_id"),
                      reply_context={"open_id": open_id,
                                     "message_id": (event.get("context") or {}).get("open_message_id")})


def parse_card_action(app, headers: Dict[str, str], body: bytes) -> ParsedEvent:
    """卡片回调地址：新版（和事件订阅同一套加密 + 签名）直接按事件解析；
    旧版消息卡片回调是明文，签名 = SHA-1(timestamp + nonce + Verification Token + 原始请求体)。"""
    outer = _load(body)
    if "encrypt" in outer:
        return parse(app, headers, body)
    if outer.get("type") == "url_verification":
        _check_token(app, outer.get("token"))
        return Handshake({"challenge": str(outer.get("challenge") or "")})
    if not app.verification_token:
        raise VerificationError("应用没有配置 Verification Token")
    timestamp, nonce = _header(headers, "X-Lark-Request-Timestamp"), _header(headers, "X-Lark-Request-Nonce")
    expected = signature.legacy_card_signature(timestamp, nonce, app.verification_token, body)
    if not signature.same(expected, _header(headers, "X-Lark-Signature")):
        raise VerificationError("签名不对")
    check_timestamp(timestamp)
    remember_nonce("feishu", nonce)
    action, token = parse_button_value((outer.get("action") or {}).get("value"))
    open_id = str(outer.get("open_id") or "")
    if not open_id:
        raise VerificationError("缺少操作人")
    return CardAction("feishu", app.app_id, open_id, action, token, event_id=f"card:{action}:{token}",
                      reply_context={"open_id": open_id, "message_id": outer.get("open_message_id")})
