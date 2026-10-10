"""飞书适配器：把 callback / messages / contacts 拼成统一的 ProviderAdapter 接口。"""
from typing import Any, Dict

from service.integrations.base import BusinessCard, ParsedEvent, ProviderAdapter
from service.integrations.feishu import auth, callback, contacts, messages


class FeishuAdapter(ProviderAdapter):
    name = "feishu"

    def parse_event(self, app, headers: Dict[str, str], query: Dict[str, str], body: bytes) -> ParsedEvent:
        return callback.parse(app, headers, body)

    def parse_card_action(self, app, headers: Dict[str, str], query: Dict[str, str], body: bytes) -> ParsedEvent:
        return callback.parse_card_action(app, headers, body)

    def send_text(self, app, context: Dict[str, Any], text: str) -> None:
        messages.send_text(app, context, text)

    def send_card(self, app, context: Dict[str, Any], card: BusinessCard) -> None:
        messages.send_card(app, context, card)

    def card_action_response(self, app, result_text: str, ok: bool) -> Dict[str, Any]:
        return {"toast": {"type": "info" if ok else "error", "content": result_text[:100]}}

    def test_connection(self, app) -> None:
        auth.forget(app)
        auth.tenant_token(app)

    def user_name(self, app, external_user_id: str) -> str:
        from service.integrations.feishu import client
        return client.user_name(app, external_user_id)

    def addressed_to_bot(self, app, message) -> bool:
        # 应用开了“读取群内所有消息”权限时，@ 别人的消息也会推过来：只处理 @ 本机器人的
        if message.chat_type == "p2p":
            return True
        from service.integrations.feishu import client
        bot = client.bot_open_id(app)
        return not bot or bot in message.mention_ids

    def fetch_organization(self, app) -> Dict[str, Any]:
        return contacts.fetch_organization(app)
