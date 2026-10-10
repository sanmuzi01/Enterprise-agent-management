"""给飞书发消息：有原消息就“回复”那条（群里也能对上是回复谁），没有就按 open_id 单聊发送。"""
import json
from typing import Any, Dict

from service.integrations.base import BusinessCard, IntegrationError
from service.integrations.feishu import client
from service.integrations.feishu.cards import FeishuCardRenderer

MAX_TEXT = 8000


def _send(app, context: Dict[str, Any], msg_type: str, content: Dict[str, Any]) -> None:
    payload = {"msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False)}
    if context.get("message_id"):
        client.call(app, "POST", f"/open-apis/im/v1/messages/{context['message_id']}/reply", json_body=payload)
    elif context.get("open_id"):
        client.call(app, "POST", "/open-apis/im/v1/messages", params={"receive_id_type": "open_id"},
                    json_body={**payload, "receive_id": context["open_id"]})
    elif context.get("chat_id"):               # 发到群里（例如网页上关闭了本群的 CRM 记录，在群里告知）
        client.call(app, "POST", "/open-apis/im/v1/messages", params={"receive_id_type": "chat_id"},
                    json_body={**payload, "receive_id": context["chat_id"]})
    else:
        raise IntegrationError("不知道发给谁（缺少 message_id / open_id / chat_id）")


def send_text(app, context: Dict[str, Any], text: str) -> None:
    text = text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n……（内容过长，完整内容请在网页工作台查看）"
    _send(app, context, "text", {"text": text})


def send_card(app, context: Dict[str, Any], card: BusinessCard) -> None:
    _send(app, context, "interactive", FeishuCardRenderer.render(card))
