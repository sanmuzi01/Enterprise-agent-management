"""员工这一轮是从哪个飞书 / 钉钉会话发来的：给“读取群聊记录”工具用——员工在群里 @机器人 说“总结一下上面的讨论”，
工具不用员工再说群名，就知道读的是这个群。只记 10 分钟，按平台账号区分，不跨人共享。"""
from typing import Any, Dict, Optional

from utils.cache import TTLCache

_recent = TTLCache(default_ttl=600, namespace="integration_chat_context")


def remember(user_id: int, provider: str, chat_id: Optional[str], chat_type: str) -> None:
    if chat_id:
        _recent.set(("chat", int(user_id)), {"provider": provider, "chat_id": chat_id, "chat_type": chat_type})


def recent(user_id: int) -> Optional[Dict[str, Any]]:
    return _recent.get(("chat", int(user_id)))


def forget(user_id: int) -> None:
    _recent.invalidate(("chat", int(user_id)))
