"""飞书开放接口客户端：带 tenant_access_token 调接口，code != 0 当作失败。地址可用 FEISHU_BASE_URL 换成私有化部署的地址。"""
import os
from typing import Any, Dict, List, Optional

from service.integrations.base import ChatLine, IntegrationError
from service.integrations.feishu import auth
from service.integrations.http import call_json
from utils.cache import TTLCache


def base_url() -> str:
    return os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn").rstrip("/")


_bot_ids: dict = {}


def bot_open_id(app) -> str:
    """本应用机器人的 open_id（判断群消息 @ 的是不是自己）。查不到返回空字符串，调用方按“不确定”处理。"""
    cached = _bot_ids.get(app.app_id)
    if cached:
        return cached
    try:
        token = auth.tenant_token(app)
        data = call_json("feishu", "GET", base_url() + "/open-apis/bot/v3/info",
                         headers={"Authorization": f"Bearer {token}"})
        open_id = str((data.get("bot") or {}).get("open_id") or "")
    except IntegrationError:
        return ""
    if open_id:
        _bot_ids[app.app_id] = open_id
    return open_id


_names = TTLCache(default_ttl=3600, namespace="feishu_user_name")


def user_name(app, open_id: str) -> str:
    """员工姓名（需要“获取通讯录基本信息”权限）。查不到返回空字符串（外部联系人也查不到）。查到的缓存 1 小时。"""
    cached = _names.get((app.app_id, open_id))
    if cached:
        return cached
    try:
        data = call(app, "GET", f"/open-apis/contact/v3/users/{open_id}", params={"user_id_type": "open_id"})
    except IntegrationError:
        return ""
    name = str((data.get("user") or {}).get("name") or "")
    if name:
        _names.set((app.app_id, open_id), name)
    return name


def call(app, method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None,
         params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    token = auth.tenant_token(app)
    data = call_json("feishu", method, base_url() + path, json_body=json_body, params=params,
                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
    if data.get("code") not in (0, None):
        if data.get("code") in (99991663, 99991664, 99991661):     # 令牌失效：下次重新换
            auth.forget(app)
        raise IntegrationError(f"飞书返回错误 {data.get('code')}：{str(data.get('msg') or '')[:200]}")
    return data.get("data") or {}


def chat_info(app, chat_id: str) -> Dict[str, str]:
    """群名称和群主 open_id（需要“获取群组信息”权限）。查不到返回空字典。"""
    try:
        data = call(app, "GET", f"/open-apis/im/v1/chats/{chat_id}", params={"user_id_type": "open_id"})
    except IntegrationError:
        return {}
    return {"name": str(data.get("name") or ""), "owner_id": str(data.get("owner_id") or "")}


MAX_FORWARDED = 500


def forwarded_lines(app, message_id: str) -> List[ChatLine]:
    """合并转发消息里的每条子消息（“获取指定消息的内容”接口：返回 1 条合并转发消息 + N 条子消息，
    需要“获取与发送单聊、群组消息”或“读取单聊、群组消息”权限）。嵌套的合并转发不再展开，记成 [聊天记录]。"""
    from service.integrations.feishu import content
    from service.integrations.feishu.callback import millis
    data = call(app, "GET", f"/open-apis/im/v1/messages/{message_id}", params={"user_id_type": "open_id"})
    names: Dict[str, str] = {}
    unknown: Dict[str, str] = {}
    lines: List[ChatLine] = []
    for item in (data.get("items") or [])[:MAX_FORWARDED + 1]:
        if item.get("message_id") == message_id or item.get("upper_message_id") != message_id or item.get("deleted"):
            continue
        sender = item.get("sender") or {}
        sender_id = str(sender.get("id") or "")
        if sender.get("sender_type") == "app":
            name = "机器人"
        elif sender_id in names:
            name = names[sender_id]
        else:
            # 本企业员工能查到姓名；外部联系人（客户）查不到，按出现顺序记成“外部成员1、2……”
            name = user_name(app, sender_id) if sender.get("id_type", "open_id") == "open_id" and sender_id else ""
            if not name:
                name = unknown.setdefault(sender_id, f"外部成员{len(unknown) + 1}")
            names[sender_id] = name
        text = content.render(str(item.get("msg_type") or ""), str((item.get("body") or {}).get("content") or ""),
                              item.get("mentions") or [], strip_mentions=False)
        when = millis(item.get("create_time"))
        if text and when is not None:
            lines.append(ChatLine(sender_id, name, text, when))
    lines.sort(key=lambda line: line.sent_at)
    return lines[:MAX_FORWARDED]
