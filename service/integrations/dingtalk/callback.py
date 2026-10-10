"""钉钉回调解析。同一个地址 /integrations/dingtalk/events 接两种请求：

- 机器人消息（HTTP 模式）：请求头带 timestamp + sign（HmacSHA256，密钥是 AppSecret），请求体是明文消息；
- 事件订阅（HTTP 推送）：请求体只有 {"encrypt": ...}，URL 参数带 msg_signature / timestamp / nonce，
  需要在后台填写事件订阅的“签名 Token”（verification_token）和“加密 aes_key”（encrypt_key）。处理的事件：
  check_url（配置回调地址时的校验）、user_leave_org（员工离职 → 绑定立即停用）。

卡片按钮回调 /integrations/dingtalk/card-actions：钉钉互动卡片的 HTTP 回调。按机器人回调同样的方式验签
（timestamp + sign，AppSecret）。⚠ 钉钉文档对互动卡片 HTTP 回调的签名说明不完整，接真实应用时需要先用
“测试连接”和一次真实点击确认签名方式一致；对不上时回调会被拒绝（401），不会被放行。
"""
import hashlib
import json
from typing import Any, Dict

from service.integrations.base import (CardAction, Handshake, Ignored, InboundMessage, OrgChange, ParsedEvent,
                                       VerificationError, check_timestamp, parse_button_value, remember_nonce)
from service.integrations.dingtalk import signature


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


def _verify_robot(app, headers: Dict[str, str]) -> None:
    timestamp, sign = _header(headers, "timestamp"), _header(headers, "sign")
    if not timestamp or not sign:
        raise VerificationError("缺少签名")
    if not signature.same(signature.robot_sign(timestamp, app.app_secret), sign):
        raise VerificationError("签名不对")
    check_timestamp(timestamp, milliseconds=True)


def parse(app, headers: Dict[str, str], query: Dict[str, str], body: bytes) -> ParsedEvent:
    data = _load(body)
    if "encrypt" in data:
        return _subscription(app, query, data)
    _verify_robot(app, headers)
    return _robot_message(app, data)


def _robot_message(app, data: Dict[str, Any]) -> ParsedEvent:
    tenant, event_id = app.app_id, str(data.get("msgId") or "")
    if not event_id:
        raise VerificationError("缺少 msgId")
    if app.robot_code and data.get("robotCode") and data.get("robotCode") != app.robot_code:
        raise VerificationError("回调不是发给这个机器人的")
    staff_id = str(data.get("senderStaffId") or "")
    if not staff_id:
        return Ignored("发消息的人不是本企业员工（没有 staffId）", tenant, event_id, "robot.message")
    group = str(data.get("conversationType") or "1") == "2"
    if group and data.get("isInAtList") is False:
        return Ignored("群消息没有 @ 机器人", tenant, event_id, "robot.message")
    text = str((data.get("text") or {}).get("content") or "").strip() if data.get("msgtype") == "text" else ""
    return InboundMessage(
        provider="dingtalk", tenant_id=tenant, event_id=event_id, external_user_id=staff_id, text=text,
        chat_id=data.get("conversationId"), chat_type="group" if group else "p2p", message_id=event_id,
        reply_context={"session_webhook": data.get("sessionWebhook"),
                       "session_webhook_expires": data.get("sessionWebhookExpiredTime"), "staff_id": staff_id},
        event_type="robot.message")


def _subscription(app, query: Dict[str, str], data: Dict[str, Any]) -> ParsedEvent:
    if not app.verification_token or not app.encrypt_key:
        raise VerificationError("没有配置事件订阅的签名 Token 和 aes_key")
    encrypted = str(data["encrypt"])
    timestamp, nonce = query.get("timestamp", ""), query.get("nonce", "")
    given = query.get("msg_signature") or query.get("signature") or ""
    if not signature.same(signature.event_signature(app.verification_token, timestamp, nonce, encrypted), given):
        raise VerificationError("签名不对")
    check_timestamp(timestamp, milliseconds=True)
    remember_nonce("dingtalk", f"{timestamp}:{nonce}")
    plain = signature.decrypt(app.encrypt_key, app.app_id, encrypted)
    event = _load(plain.encode("utf-8"))
    ack = signature.encrypted_reply(app.verification_token, app.encrypt_key, app.app_id)
    event_type = str(event.get("EventType") or "")
    tenant = app.app_id
    event_id = str(event.get("EventId") or "") or "evt:" + hashlib.sha256(plain.encode("utf-8")).hexdigest()[:40]
    if event_type == "check_url":
        return Handshake(ack)
    if event_type == "user_leave_org":
        ids = event.get("UserId") or event.get("UserIds") or []
        return OrgChange("dingtalk", tenant, event_id, event_type, [str(i) for i in (ids if isinstance(ids, list) else [ids])],
                         ack=ack)
    return Ignored(f"不处理的事件类型 {event_type}", tenant, event_id, event_type, ack=ack)


def parse_card_action(app, headers: Dict[str, str], query: Dict[str, str], body: bytes) -> ParsedEvent:
    _verify_robot(app, headers)
    data = _load(body)
    content = data.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            content = {}
    params = ((content or {}).get("cardPrivateData") or {}).get("params") or data.get("value") or {}
    action, token = parse_button_value(params)
    staff_id = str(data.get("userId") or "")
    if not staff_id:
        raise VerificationError("缺少操作人")
    return CardAction("dingtalk", app.app_id, staff_id, action, token,
                      event_id=f"card:{data.get('outTrackId') or ''}:{action}:{token}",
                      reply_context={"staff_id": staff_id})
