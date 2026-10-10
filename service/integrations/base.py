"""外部接入层的公共结构：入站消息、卡片按钮回调、统一卡片模型、各平台适配器的接口，以及时间戳 / 重放检查。"""
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from utils.logger_handler import get_logger

logger = get_logger("integrations")

PROVIDERS = ("feishu", "dingtalk")
PROVIDER_LABELS = {"feishu": "飞书", "dingtalk": "钉钉"}


class IntegrationError(Exception):
    """调用外部平台失败（网络、凭证、权限）。message 是给管理员看的中文原因，不含密钥。"""


class VerificationError(Exception):
    """回调没通过校验：签名不对、过期、重放、Token 不对。返回 401，不处理内容。"""


# ---------------------------------------------------------------- 入站

@dataclass
class InboundMessage:
    """员工发给机器人的一条消息（已验签、已解密）。"""
    provider: str
    tenant_id: str
    event_id: str
    external_user_id: str
    text: str
    union_id: Optional[str] = None
    chat_id: Optional[str] = None
    chat_type: str = "p2p"                      # p2p 单聊 / group 群聊（群里只处理 @ 机器人的消息）
    message_id: Optional[str] = None
    reply_context: Dict[str, Any] = field(default_factory=dict)   # 回复用的信息（飞书 message_id，钉钉 sessionWebhook 等）
    event_type: str = "message"
    ack: Dict[str, Any] = field(default_factory=dict)             # 立即回给平台的应答（钉钉事件订阅要加密的 success）


@dataclass
class CardAction:
    """员工点了卡片上的按钮。按钮里只带一次性确认令牌和动作，不带任何业务参数。"""
    provider: str
    tenant_id: str
    external_user_id: str
    action: str                                 # confirm / reject
    token: str
    event_id: Optional[str] = None
    union_id: Optional[str] = None
    reply_context: Dict[str, Any] = field(default_factory=dict)
    ack: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrgChange:
    """通讯录变动：员工离职 / 被删除。对应的绑定立即停用（离职员工不能再用助手）。"""
    provider: str
    tenant_id: str
    event_id: str
    event_type: str
    left_user_ids: List[str] = field(default_factory=list)
    ack: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Handshake:
    """平台配置回调地址时的校验请求（飞书 url_verification、钉钉 check_url）：原样按平台要求回复。"""
    response: Dict[str, Any]


@dataclass
class Ignored:
    """验签通过但不需要处理（不认识的事件类型、群里没有 @ 机器人、机器人自己发的消息）。"""
    reason: str
    tenant_id: str = ""
    event_id: str = ""
    event_type: str = ""
    ack: Dict[str, Any] = field(default_factory=dict)


ParsedEvent = Union[InboundMessage, CardAction, OrgChange, Handshake, Ignored]


# ---------------------------------------------------------------- 统一卡片模型

@dataclass
class CardField:
    label: str
    value: str


@dataclass
class ConfirmAction:
    token: str
    label: str = "确认执行"


@dataclass
class RejectAction:
    token: str
    label: str = "不执行"


@dataclass
class OpenUrlAction:
    url: str
    label: str = "在网页中查看"


CardButton = Union[ConfirmAction, RejectAction, OpenUrlAction]


@dataclass
class BusinessCard:
    """平台内部的卡片。各平台的 Renderer 负责转成自己的格式。

    安全约束：确认 / 拒绝按钮只能带一次性确认令牌（tool_confirmation.token），不能带可修改的业务参数——
    要执行的内容在生成令牌时已经存进数据库，按钮回调只能决定“执行 / 不执行”，改不了要执行什么。"""
    title: str
    fields: List[CardField] = field(default_factory=list)
    actions: List[CardButton] = field(default_factory=list)
    note: str = ""
    tone: str = "normal"                        # normal / warning / success / danger

    def button_values(self) -> List[Dict[str, str]]:
        """按钮回调里会带回来的值：只有动作和令牌。"""
        values = []
        for action in self.actions:
            if isinstance(action, ConfirmAction):
                values.append({"action": "confirm", "token": action.token})
            elif isinstance(action, RejectAction):
                values.append({"action": "reject", "token": action.token})
        return values


class WebCardRenderer:
    """网页工作台用的卡片（JSON），和飞书 / 钉钉的卡片内容一致。"""

    @staticmethod
    def render(card: BusinessCard) -> Dict[str, Any]:
        buttons = []
        for action in card.actions:
            if isinstance(action, ConfirmAction):
                buttons.append({"type": "confirm", "label": action.label, "token": action.token})
            elif isinstance(action, RejectAction):
                buttons.append({"type": "reject", "label": action.label, "token": action.token})
            elif isinstance(action, OpenUrlAction):
                buttons.append({"type": "open_url", "label": action.label, "url": action.url})
        return {"title": card.title, "tone": card.tone, "note": card.note,
                "fields": [{"label": f.label, "value": f.value} for f in card.fields], "buttons": buttons}


def parse_button_value(value: Any) -> Tuple[str, str]:
    """从回调里取出 (动作, 令牌)；格式不对抛 VerificationError。只认 confirm / reject 和 32 位十六进制令牌。"""
    if not isinstance(value, dict):
        raise VerificationError("按钮数据格式不对")
    action, token = str(value.get("action") or ""), str(value.get("token") or "")
    if action not in ("confirm", "reject") or len(token) != 32 or not all(c in "0123456789abcdef" for c in token):
        raise VerificationError("按钮数据格式不对")
    return action, token


# ---------------------------------------------------------------- 时间戳与重放

def max_skew_seconds() -> int:
    return int(os.getenv("INTEGRATION_CALLBACK_MAX_SKEW_SECONDS", "300"))


def check_timestamp(timestamp: Union[str, int, None], *, milliseconds: bool = False, now: Optional[float] = None) -> None:
    """回调的时间戳必须在当前时间前后 5 分钟内（INTEGRATION_CALLBACK_MAX_SKEW_SECONDS），过期的请求不处理。"""
    try:
        value = float(timestamp)
    except (TypeError, ValueError):
        raise VerificationError("缺少时间戳") from None
    if milliseconds:
        value /= 1000
    if abs((now if now is not None else time.time()) - value) > max_skew_seconds():
        raise VerificationError("请求已过期")


_SEEN: Dict[str, float] = {}
_SEEN_LOCK = threading.Lock()


def remember_nonce(provider: str, nonce: str, ttl: Optional[int] = None) -> None:
    """同一个 nonce 在有效期内只能用一次（防重放）。有 Redis 时用 SET NX（多个进程共享），没有时退回本进程内存。"""
    if not nonce:
        raise VerificationError("缺少 nonce")
    ttl = ttl or max_skew_seconds() * 2
    key = f"integration:nonce:{provider}:{nonce}"
    client = _redis_client()
    if client is not None:
        try:
            first_time = client.set(key, "1", nx=True, ex=ttl)
        except Exception:  # noqa: BLE001 —— Redis 抖动时退回内存，不因为它拒绝正常回调
            logger.warning("nonce 去重写 Redis 失败，退回进程内存", exc_info=True)
        else:
            if not first_time:
                raise VerificationError("重复的请求")
            return
    now = time.time()
    with _SEEN_LOCK:
        for stale in [k for k, expires in _SEEN.items() if expires < now]:
            _SEEN.pop(stale, None)
        if key in _SEEN:
            raise VerificationError("重复的请求")
        _SEEN[key] = now + ttl


_REDIS = None


def _redis_client():
    global _REDIS
    if not os.getenv("REDIS_URL"):
        return None
    if _REDIS is None:
        from utils.redis_client import RedisClientManager
        _REDIS = RedisClientManager(decode_responses=True)
    return _REDIS.get_client()


# ---------------------------------------------------------------- 平台适配器接口

class ProviderAdapter:
    """每个平台实现这一组方法。app 是 service/integrations/apps.py::AppCredentials（明文凭证只在内存里）。"""
    name = ""

    def parse_event(self, app, headers: Dict[str, str], query: Dict[str, str], body: bytes) -> ParsedEvent:
        raise NotImplementedError

    def parse_card_action(self, app, headers: Dict[str, str], query: Dict[str, str], body: bytes) -> ParsedEvent:
        raise NotImplementedError

    def send_text(self, app, context: Dict[str, Any], text: str) -> None:
        raise NotImplementedError

    def send_card(self, app, context: Dict[str, Any], card: BusinessCard) -> None:
        raise NotImplementedError

    def card_action_response(self, app, result_text: str, ok: bool) -> Dict[str, Any]:
        """按钮回调的即时响应（飞书可以直接返回提示，钉钉返回空对象）。"""
        return {}

    def test_connection(self, app) -> None:
        """用配置的凭证换一次访问令牌；失败抛 IntegrationError。"""
        raise NotImplementedError

    def fetch_organization(self, app) -> Dict[str, List[Dict[str, Any]]]:
        """{"tenant_id": str, "departments": [{id, name, parent_id}], "users": [{user_id, union_id, name, mobile, email, department_ids, active}]}"""
        raise NotImplementedError
