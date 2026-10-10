"""读取飞书群聊记录（员工要求时）：“总结一下华星项目群今天聊了什么”“上面的讨论有哪些待办”。

边界（和“默认不读私聊”的原则一致）：
- 只读群聊，不读任何私聊（员工之间的私聊机器人本来也读不到，要员工本人授权才行，不在这里做）；
- 机器人必须在这个群里，**提问的员工本人也必须在这个群里**——不能借助手看自己不在的群；
- 一次最多读 7 天、500 条；每次读取都写审计（谁、哪个群、多长时间、多少条），读到的内容只用于回答这一次提问。
需要飞书应用开通“获取群组中所有消息”“获取群组信息”（读群成员）权限。钉钉机器人读不到群历史消息，不支持。
"""
import json
import time
from typing import Any, Dict, Optional

from service.tools.base import BaseTool, ToolRegistry

MAX_HOURS = 7 * 24
MAX_MESSAGES = 500
MAX_CHARS = 30000


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _binding(db, provider: str, tenant_id: str, user_id: int) -> Optional[str]:
    from sqlalchemy import select
    from models.init_db import ExternalUserBinding
    return db.execute(select(ExternalUserBinding.external_user_id).where(
        ExternalUserBinding.provider == provider, ExternalUserBinding.external_tenant_id == tenant_id,
        ExternalUserBinding.local_user_id == user_id, ExternalUserBinding.status == "active")).scalar()


def read_group_chat(user_id: int, chat_name: str = "", hours: float = 24, limit: int = 200) -> Dict[str, Any]:
    """返回 {chat_name, hours, count, truncated, transcript} 或 {error}。"""
    from models.init_db import SessionLocal
    from service import audit_service
    from service.crm.chat_ingest import transcript
    from service.integrations import apps, chat_context
    from service.integrations.base import IntegrationError
    from service.integrations.feishu import client

    hours = max(0.5, min(float(hours or 24), MAX_HOURS))
    limit = max(1, min(int(limit or 200), MAX_MESSAGES))
    db = SessionLocal()
    try:
        app = apps.load_enabled_sync(db, "feishu")
        if app is None:
            return {"error": "企业还没有开通飞书接入，读不了飞书群聊记录。"}
        open_id = _binding(db, "feishu", app.app_id, user_id)
    finally:
        db.close()
    if not open_id:
        return {"error": "你还没有绑定飞书账号（平台“设置 → 飞书 / 钉钉”里绑定），绑定后才能读取你所在的群的聊天记录。"}

    try:
        chat_id, name = None, ""
        recent = chat_context.recent(user_id)
        in_group = bool(recent and recent.get("provider") == "feishu" and recent.get("chat_type") == "group")
        if chat_name.strip():
            keyword = chat_name.strip()
            chats = client.bot_chats(app)
            matches = [c for c in chats if c["name"] == keyword] or [c for c in chats if keyword in c["name"]]
            if not matches:
                return {"error": f"机器人不在名字含「{keyword}」的群里。需要先把企业机器人拉进这个群，才能读取群聊记录。"}
            if len(matches) > 1:
                return {"error": "有好几个群对得上，请说完整的群名", "candidates": [c["name"] for c in matches[:10]]}
            chat_id, name = matches[0]["chat_id"], matches[0]["name"]
            if in_group and chat_id != recent["chat_id"]:
                # 在 A 群里提问却要读 B 群：回答会发在 A 群，B 群的内容就漏给了不在 B 群的人（包括被提示注入诱导的情况）
                return {"error": f"在群里只能读当前这个群。要看「{name}」的记录，请私聊我（回答只发给你本人）。"}
        else:
            if not in_group:
                return {"error": "请告诉我要读哪个群（群名），或者在那个群里 @我 提问。"}
            chat_id = recent["chat_id"]
            name = (client.chat_info(app, chat_id) or {}).get("name") or "当前群"
        if not client.chat_has_member(app, chat_id, open_id):
            return {"error": f"你不在「{name}」这个群里，不能读取它的聊天记录。"}
        end = int(time.time())
        lines = client.chat_history(app, chat_id, end - int(hours * 3600), end, limit)
    except IntegrationError as exc:
        return {"error": f"读取飞书群聊记录失败：{exc}（需要飞书应用开通“获取群组中所有消息”“获取群组信息”权限）"}

    audit_service.record(user_id, "integration.chat_read", resource_type="feishu_chat", resource_id=0,
                         detail={"chat_id": chat_id, "chat_name": name, "hours": hours, "messages": len(lines)})
    text = transcript(lines, limit=MAX_CHARS)
    return {"chat_name": name, "hours": hours, "count": len(lines), "truncated": len(lines) >= limit,
            "transcript": text or "（这段时间群里没有消息）",
            "note": "聊天内容只用于回答提问人这一次的问题，不要转发或保存到别处，除非提问人明确要求（例如保存到 CRM）。"}


@ToolRegistry.register
class ReadFeishuGroupChatTool(BaseTool):
    requires_context = True
    risk_level = "read"

    def get_name(self) -> str:
        return "read_feishu_group_chat"

    def get_description(self) -> str:
        return ("读取飞书群里最近的聊天记录，用来总结讨论、提取待办和结论、回答“群里刚才说了什么”。"
                "只能读机器人所在、且提问人本人也在的群；不能读私聊。用户在群里 @你 时不填群名就是读当前这个群。")

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {
            "chat_name": {"type": "string", "description": "群名（可以是群名的一部分）；用户在群里提问、指的就是当前群时留空"},
            "hours": {"type": "number", "description": f"读最近多少小时的消息，默认 24，最多 {MAX_HOURS}"},
            "limit": {"type": "integer", "description": f"最多读多少条，默认 200，最多 {MAX_MESSAGES}"},
        }}

    def execute(self, **kwargs) -> str:
        if not self._ctx or not self._ctx.user_id:
            return _error("缺少用户上下文")
        result = read_group_chat(self._ctx.user_id, str(kwargs.get("chat_name") or ""), kwargs.get("hours") or 24,
                                 kwargs.get("limit") or 200)
        return json.dumps(result, ensure_ascii=False)
