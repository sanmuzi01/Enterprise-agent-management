"""外部 Agent 的接入配置（管理后台）：设置地址、轮换签名密钥、测试连接。

只有中央/部门 Agent（管理员建的）可以接外部服务；普通用户自己的个人 Agent 没有这个入口。
理由：外部服务会收到提问者的身份和对话内容，接入哪个服务属于企业级决定，不应由个人自行决定。
签名密钥由平台生成，只在创建 / 轮换那一次返回明文，库里只存密文，之后只能轮换不能查看。
"""
import asyncio
import json
import secrets
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy import update as sa_update

from models.init_db import Agent, AgentExternalEndpoint
from service import audit_service
from service.agent_admin_service import VALID_MANAGED_AGENT_TYPES
from service.exceptions import InvalidInput, NotFound
from service.runtime import external_agent
from service.runtime.external_runtime import load_endpoint_async, to_config, user_context_async
from utils.crypto import decrypt, encrypt
from utils.timeutil import utcnow

RUNTIME_TYPES = {"builtin", "external"}
MAX_HEADERS = 10


async def _managed_agent(db, agent_id: int) -> Agent:
    agent = (await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.agent_type.in_(VALID_MANAGED_AGENT_TYPES))
    )).unique().scalar_one_or_none()
    if agent is None:
        raise NotFound("智能体不存在，或不是企业管理的中央 / 部门智能体")
    return agent


def _clean_headers(headers: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    if headers is None:
        return None
    if not isinstance(headers, dict) or len(headers) > MAX_HEADERS:
        raise InvalidInput(f"附加请求头最多 {MAX_HEADERS} 个")
    cleaned: Dict[str, str] = {}
    for key, value in headers.items():
        name = str(key).strip()
        if not name or not all(c.isalnum() or c in "-_" for c in name):
            raise InvalidInput(f"请求头名称不合法：{name[:40]}")
        if name.lower() in {"host", "content-length", "transfer-encoding", "connection"}:
            raise InvalidInput(f"不能自定义请求头 {name}")
        text = str(value)
        if "\r" in text or "\n" in text or len(text) > 2000:
            raise InvalidInput(f"请求头 {name} 的值不合法")
        cleaned[name] = text
    return cleaned


def _describe(agent: Agent, endpoint: Optional[AgentExternalEndpoint], *, secret: Optional[str] = None) -> Dict[str, Any]:
    info: Dict[str, Any] = {"agent_id": agent.id, "runtime_type": agent.runtime_type, "endpoint": None}
    if endpoint is not None:
        has_headers = bool(endpoint.headers_encrypted)
        header_names = []
        if has_headers:
            try:
                header_names = sorted(json.loads(decrypt(endpoint.headers_encrypted)).keys())
            except Exception:  # noqa: BLE001
                header_names = []
        info["endpoint"] = {
            "url": endpoint.url, "timeout_seconds": endpoint.timeout_seconds, "send_knowledge": bool(endpoint.send_knowledge),
            "header_names": header_names,          # 只给名字，不给值
            "last_test_at": endpoint.last_test_at.strftime("%Y-%m-%d %H:%M:%S") if endpoint.last_test_at else None,
            "last_test_ok": None if endpoint.last_test_ok is None else bool(endpoint.last_test_ok),
            "last_test_message": endpoint.last_test_message,
        }
    if secret:
        info["secret"] = secret                    # 仅在刚生成 / 轮换时返回这一次
    return info


async def get_runtime(db, agent_id: int) -> Dict[str, Any]:
    agent = await _managed_agent(db, agent_id)
    return _describe(agent, await load_endpoint_async(db, agent_id))


async def configure_runtime(
        db, agent_id: int, operator_id: int, runtime_type: str, *, url: Optional[str] = None,
        timeout_seconds: Optional[int] = None, send_knowledge: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None, rotate_secret: bool = False,
) -> Dict[str, Any]:
    if runtime_type not in RUNTIME_TYPES:
        raise InvalidInput(f"运行方式只能是 {sorted(RUNTIME_TYPES)} 之一")
    agent = await _managed_agent(db, agent_id)
    endpoint = await load_endpoint_async(db, agent_id)
    new_secret: Optional[str] = None

    if runtime_type == "external":
        target_url = (url if url is not None else (endpoint.url if endpoint else "")).strip()
        if not target_url:
            raise InvalidInput("请填写外部 Agent 服务的地址")
        try:
            target_url = external_agent.validate_endpoint_url(target_url)
        except external_agent.ExternalAgentError as exc:
            raise InvalidInput(str(exc)) from exc
        timeout = timeout_seconds if timeout_seconds is not None else (endpoint.timeout_seconds if endpoint else 60)
        if not 1 <= int(timeout) <= external_agent.max_timeout_seconds():
            raise InvalidInput(f"超时时间需要在 1 到 {external_agent.max_timeout_seconds()} 秒之间")
        cleaned_headers = _clean_headers(headers)
        address_changed = endpoint is None or endpoint.url != target_url
        if endpoint is None:
            new_secret = secrets.token_urlsafe(32)
            endpoint = AgentExternalEndpoint(
                agent_id=agent_id, url=target_url, secret_encrypted=encrypt(new_secret), timeout_seconds=int(timeout),
                send_knowledge=1 if send_knowledge else 0,
            )
            db.add(endpoint)
        else:
            endpoint.url = target_url
            endpoint.timeout_seconds = int(timeout)
            if send_knowledge is not None:
                endpoint.send_knowledge = 1 if send_knowledge else 0
            if rotate_secret:
                new_secret = secrets.token_urlsafe(32)
                endpoint.secret_encrypted = encrypt(new_secret)
        if cleaned_headers is not None:
            endpoint.headers_encrypted = encrypt(json.dumps(cleaned_headers, ensure_ascii=False)) if cleaned_headers else None
        if address_changed or new_secret or cleaned_headers is not None:
            endpoint.last_test_at = endpoint.last_test_ok = endpoint.last_test_message = None   # 配置变了，之前的测试结果作废

    await db.execute(sa_update(Agent).where(Agent.id == agent_id).values(
        runtime_type=runtime_type, row_version=Agent.row_version + 1))
    await db.commit()
    await db.refresh(agent)
    await audit_service.record_async(
        operator_id, "org.managed_agent_runtime_changed", resource_type="agent", resource_id=agent_id,
        detail={"runtime_type": runtime_type, "url": endpoint.url if endpoint is not None and runtime_type == "external" else None,
                "secret_rotated": bool(new_secret), "send_knowledge": bool(endpoint and endpoint.send_knowledge)},
    )
    return _describe(agent, await load_endpoint_async(db, agent_id), secret=new_secret)


async def test_runtime(db, agent_id: int, operator_id: int) -> Dict[str, Any]:
    """向外部服务发一个 ping 请求（不带任何用户的对话内容），验证地址、签名、返回格式。"""
    agent = await _managed_agent(db, agent_id)
    endpoint = await load_endpoint_async(db, agent_id)
    if endpoint is None:
        raise InvalidInput("还没有配置外部服务地址")
    cfg = to_config(endpoint)
    user = await user_context_async(db, operator_id)
    payload = external_agent.build_payload(
        kind="ping", agent={"id": agent.id, "name": agent.name}, user={"id": user["id"], "name": user["name"], "departments": []},
        message="ping", history=[], run_id=None, conversation_id=None,
    )
    ok, message = True, "连接正常，签名校验通过，返回格式正确"
    try:
        result = await asyncio.to_thread(external_agent.call, cfg, payload)
        if not result["answer"].strip():
            ok, message = False, "服务有响应，但 answer 是空的"
    except external_agent.ExternalAgentError as exc:
        ok, message = False, str(exc)[:280]
    endpoint.last_test_at, endpoint.last_test_ok, endpoint.last_test_message = utcnow(), 1 if ok else 0, message
    await db.commit()
    await audit_service.record_async(operator_id, "org.managed_agent_runtime_tested", resource_type="agent", resource_id=agent_id,
                                     detail={"ok": ok})
    return {"ok": ok, "message": message, **_describe(agent, endpoint)}


async def require_ready_to_publish(db, agent: Agent) -> None:
    """外部 Agent 发布前必须已配置地址。"""
    if agent.runtime_type == "external" and await load_endpoint_async(db, agent.id) is None:
        raise InvalidInput("外部 Agent 还没有配置服务地址，不能发布")
