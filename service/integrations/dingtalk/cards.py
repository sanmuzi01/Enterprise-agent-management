"""BusinessCard → 钉钉。

互动卡片模板（在钉钉卡片平台搭建，模板 ID 填到后台）需要定义这些变量，全部是字符串：
  title、content（Markdown：各字段 + 说明）、confirm_label、reject_label、web_url（可为空）、
  confirm_params / reject_params（JSON：{"action": "confirm|reject", "token": "..."}，按钮的回传参数直接绑定它）。
按钮回调只回传 {action, token}，执行内容在确认单里，按钮改不了。

没有模板时退回 Markdown 消息：列出内容并提示到网页工作台确认（钉钉 Markdown 消息不能带回调按钮）。
"""
import json
from typing import Dict, Tuple

from service.integrations.base import BusinessCard, ConfirmAction, OpenUrlAction, RejectAction


def _content(card: BusinessCard) -> str:
    lines = [f"- **{f.label}**：{f.value}" for f in card.fields]
    if card.note:
        lines.append("")
        lines.append(f"> {card.note}")
    return "\n".join(lines)


class DingTalkCardRenderer:
    @staticmethod
    def render(card: BusinessCard) -> Dict[str, str]:
        params = {"title": card.title, "content": _content(card), "confirm_label": "", "reject_label": "", "web_url": "",
                  "confirm_params": "", "reject_params": ""}
        for action in card.actions:
            if isinstance(action, ConfirmAction):
                params["confirm_label"] = action.label
                params["confirm_params"] = json.dumps({"action": "confirm", "token": action.token})
            elif isinstance(action, RejectAction):
                params["reject_label"] = action.label
                params["reject_params"] = json.dumps({"action": "reject", "token": action.token})
            elif isinstance(action, OpenUrlAction):
                params["web_url"] = action.url
        return params

    @staticmethod
    def markdown(card: BusinessCard) -> Tuple[str, str]:
        text = f"### {card.title}\n\n{_content(card)}"
        links = [a for a in card.actions if isinstance(a, OpenUrlAction)]
        if any(isinstance(a, ConfirmAction) for a in card.actions):
            text += "\n\n需要你确认后才会执行：请到网页工作台的对话里点“确认”（10 分钟内有效）。"
        if links:
            text += f"\n\n[{links[0].label}]({links[0].url})"
        return card.title, text
