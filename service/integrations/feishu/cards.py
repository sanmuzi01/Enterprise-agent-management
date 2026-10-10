"""BusinessCard → 飞书消息卡片 JSON。按钮的 value 只有 {action, token}（BusinessCard.button_values），不带业务参数。"""
from typing import Any, Dict

from service.integrations.base import BusinessCard, ConfirmAction, OpenUrlAction, RejectAction

_TEMPLATE = {"normal": "blue", "warning": "orange", "success": "green", "danger": "red"}


class FeishuCardRenderer:
    @staticmethod
    def render(card: BusinessCard) -> Dict[str, Any]:
        elements = []
        if card.fields:
            elements.append({"tag": "div", "fields": [
                {"is_short": len(f.value) <= 24, "text": {"tag": "plain_text", "content": f"{f.label}：{f.value}"}}
                for f in card.fields]})
        if card.note:
            elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": card.note}]})
        buttons = []
        for action in card.actions:
            if isinstance(action, ConfirmAction):
                buttons.append({"tag": "button", "type": "primary", "text": {"tag": "plain_text", "content": action.label},
                                "value": {"action": "confirm", "token": action.token}})
            elif isinstance(action, RejectAction):
                buttons.append({"tag": "button", "type": "default", "text": {"tag": "plain_text", "content": action.label},
                                "value": {"action": "reject", "token": action.token}})
            elif isinstance(action, OpenUrlAction):
                buttons.append({"tag": "button", "type": "default", "text": {"tag": "plain_text", "content": action.label},
                                "url": action.url})
        if buttons:
            elements.append({"tag": "action", "actions": buttons})
        return {"config": {"wide_screen_mode": True, "update_multi": True},
                "header": {"title": {"tag": "plain_text", "content": card.title},
                           "template": _TEMPLATE.get(card.tone, "blue")},
                "elements": elements}
