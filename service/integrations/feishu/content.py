"""飞书消息内容 → 文字。事件推送和“获取指定消息的内容”接口返回的 content 都是 JSON 字符串，格式按消息类型不同：

- text：{"text": "…@_user_1…"}，@ 的人用占位符表示，真名在 mentions 里；
- post（富文本）：{"title": …, "content": [[{"tag": "text", "text": …}, {"tag": "at", "user_name": …}, …], …]}，
  有时外面还包一层语言（{"zh_cn": {...}}）；
- 图片、文件、语音、视频、表情、名片、卡片……：没有可读的文字，记成占位（[图片]、[文件：报价单.pdf]）。

strip_mentions=True：交给助手的文字，去掉所有 @（“@机器人 帮我查一下”只留“帮我查一下”）；
False：记到 CRM 的文字，@ 换成人名，保留原话。
"""
import json
import re
from typing import Any, Dict, List

_MENTION = re.compile(r"@_user_\d+")
READABLE = ("text", "post")
PLACEHOLDERS = {"image": "[图片]", "audio": "[语音]", "sticker": "[表情]", "share_chat": "[群名片]",
                "share_user": "[个人名片]", "interactive": "[卡片消息]", "merge_forward": "[聊天记录]",
                "share_calendar_event": "[日程]", "calendar": "[日程]", "todo": "[任务]", "vote": "[投票]",
                "hongbao": "[红包]", "video_chat": "[视频会议]", "system": ""}


def _load(content: str) -> Dict[str, Any]:
    try:
        data = json.loads(content or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _mention_names(mentions: List[Dict[str, Any]]) -> Dict[str, str]:
    return {str(m.get("key")): str(m.get("name") or "") for m in mentions or [] if m.get("key")}


def _post_text(data: Dict[str, Any], strip_mentions: bool) -> str:
    if "content" not in data:                     # 外面包了一层语言：取第一种
        data = next((v for v in data.values() if isinstance(v, dict) and "content" in v), {})
    lines = [str(data.get("title") or "").strip()] if data.get("title") else []
    for paragraph in data.get("content") or []:
        parts = []
        for node in paragraph if isinstance(paragraph, list) else []:
            tag = node.get("tag")
            if tag in ("text", "a", "md", "code_block"):
                parts.append(str(node.get("text") or ""))
            elif tag == "at" and not strip_mentions:
                parts.append("@" + str(node.get("user_name") or node.get("user_id") or ""))
            elif tag == "img" and not strip_mentions:
                parts.append("[图片]")
            elif tag == "media" and not strip_mentions:
                parts.append("[视频]")
            elif tag == "emotion" and not strip_mentions:
                parts.append(f"[{node.get('emoji_type') or '表情'}]")
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def render(msg_type: str, content: str, mentions: List[Dict[str, Any]] = (), *, strip_mentions: bool) -> str:
    data = _load(content)
    if msg_type == "text":
        text = str(data.get("text") or "")
        if strip_mentions:
            return _MENTION.sub("", text).strip()
        names = _mention_names(list(mentions or []))
        return _MENTION.sub(lambda m: "@" + names[m.group(0)] if names.get(m.group(0)) else "", text).strip()
    if msg_type == "post":
        text = _post_text(data, strip_mentions)
        return _MENTION.sub("", text).strip() if strip_mentions else text
    if strip_mentions:
        return ""                                 # 助手只处理文字
    if msg_type in ("file", "media"):
        name = str(data.get("file_name") or "").strip()
        return f"[{'文件' if msg_type == 'file' else '视频'}{'：' + name if name else ''}]"
    if msg_type == "location":
        return f"[位置：{data.get('name') or ''}]"
    return PLACEHOLDERS.get(msg_type, f"[{msg_type} 消息]")
