"""企业接口连接器 CRUD：用户给 Agent 配一个真实的企业 HTTP 接口，运行时当工具用。

安全边界：URL、请求方式、认证头都是用户在这里配好的，LLM 运行时只填
`param_schema_json` 描述的参数——不能改 URL、不能碰认证信息，这是防止提示词
注入诱导 Agent 访问任意地址/泄露密钥的关键设计。
"""
import json
import re
from typing import Any, Dict, List, Optional

from models import agent_api_connector_dao as dao
from service.exceptions import InvalidInput, NotFound
from service.web_crawler_service import CrawlerError, validate_crawl_url
from utils.crypto import encrypt

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{1,63}$")
_ALLOWED_METHODS = {"GET", "POST"}


def _validate(name: str, description: str, url: str, method: str, param_schema: Dict[str, Any]) -> None:
    if not _NAME_RE.match(name or ""):
        raise InvalidInput("工具名只能是字母/数字/下划线，且不能以数字开头（会原样传给大模型当函数名用）")
    if not description or not description.strip():
        raise InvalidInput("必须填写接口说明，否则大模型不知道什么时候该用这个工具")
    try:
        validate_crawl_url(url)
    except CrawlerError as e:
        raise InvalidInput(f"接口地址不允许访问：{e}")
    if (method or "").upper() not in _ALLOWED_METHODS:
        raise InvalidInput("请求方式只支持 GET 或 POST")
    if param_schema is not None:
        if not isinstance(param_schema, dict) or param_schema.get("type") not in (None, "object"):
            raise InvalidInput("参数说明必须是 JSON Schema 的 object 类型")


def _to_dict(row) -> Dict[str, Any]:
    return {
        "id": row.id,
        "agent_id": row.agent_id,
        "name": row.name,
        "description": row.description,
        "url": row.url,
        "method": row.method,
        "has_headers": bool(row.headers_encrypted),
        "param_schema": json.loads(row.param_schema_json),
        "static_query": json.loads(row.static_query_json or "{}"),
        "is_enabled": bool(row.is_enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def create_connector(
        db, user_id: int, agent_id: int, *, name: str, description: str, url: str,
        method: str = "GET", headers: Optional[Dict[str, str]] = None,
        param_schema: Optional[Dict[str, Any]] = None, static_query: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    param_schema = param_schema or {"type": "object", "properties": {}, "required": []}
    _validate(name, description, url, method, param_schema)
    row = dao.create_connector(
        db,
        user_id=user_id, agent_id=agent_id, name=name.strip(), description=description.strip(),
        url=url.strip(), method=method.upper(),
        headers_encrypted=encrypt(json.dumps(headers, ensure_ascii=False)) if headers else None,
        param_schema_json=json.dumps(param_schema, ensure_ascii=False),
        static_query_json=json.dumps(static_query or {}, ensure_ascii=False),
    )
    db.commit()
    return _to_dict(row)


def list_connectors(db, user_id: int, agent_id: int) -> List[Dict[str, Any]]:
    rows = [
        r for r in dao.list_connectors_by_agent(db, agent_id)
        if r.user_id == user_id
    ]
    return [_to_dict(r) for r in rows]


def set_connector_enabled(db, user_id: int, connector_id: int, enabled: bool) -> Dict[str, Any]:
    row = dao.get_owned_connector(db, user_id, connector_id)
    if not row:
        raise NotFound("连接器不存在或无权限")
    row.is_enabled = 1 if enabled else 0
    db.commit()
    return _to_dict(row)


def delete_connector(db, user_id: int, connector_id: int) -> None:
    row = dao.get_owned_connector(db, user_id, connector_id)
    if not row:
        raise NotFound("连接器不存在或无权限")
    dao.delete_connector(db, row)
    db.commit()


# ---------------------------------------------------------------- 管理员：给企业智能体配置接口工具
# 以前只能在用户端“我的助手 → 运行检查 → 接口工具”里配，而且要求助手是操作人自己建的——
# 企业智能体、部门助手通常不在管理员本人名下，管理员打开只会看到“不存在或无权限”，后台也没有入口。
# 这里按“企业管理的智能体”（中央 / 部门）授权，任何管理员都能维护；每次变更写审计（不记认证头）。

def _managed_agent(db, agent_id: int):
    from models.init_db import Agent
    from service.agent_admin_service import VALID_MANAGED_AGENT_TYPES
    agent = db.query(Agent).filter(Agent.id == agent_id, Agent.agent_type.in_(VALID_MANAGED_AGENT_TYPES)).first()
    if agent is None:
        raise NotFound("智能体不存在，或不是企业管理的中央 / 部门智能体")
    return agent


def _connector_of(db, agent_id: int, connector_id: int):
    row = next((r for r in dao.list_connectors_by_agent(db, agent_id) if r.id == connector_id), None)
    if row is None:
        raise NotFound("接口工具不存在")
    return row


def _audit_detail(row) -> Dict[str, Any]:
    from urllib.parse import urlsplit
    parts = urlsplit(row.url)
    return {"connector_id": row.id, "name": row.name, "method": row.method,
            "url": f"{parts.scheme}://{parts.netloc}{parts.path}",   # 不记查询串，里面可能有令牌
            "has_headers": bool(row.headers_encrypted), "is_enabled": bool(row.is_enabled)}


def admin_list_connectors(db, agent_id: int) -> List[Dict[str, Any]]:
    _managed_agent(db, agent_id)
    return [_to_dict(r) for r in dao.list_connectors_by_agent(db, agent_id)]


def admin_create_connector(db, operator_id: int, agent_id: int, **fields) -> Dict[str, Any]:
    from service import audit_service
    _managed_agent(db, agent_id)
    if any(r.name == (fields.get("name") or "").strip() for r in dao.list_connectors_by_agent(db, agent_id)):
        raise InvalidInput("这个智能体已经有同名的接口工具了（工具名会原样交给大模型，不能重复）")
    created = create_connector(db, operator_id, agent_id, **fields)
    audit_service.record(operator_id, "agent.api_connector_created", resource_type="agent", resource_id=agent_id,
                         detail=_audit_detail(_connector_of(db, agent_id, created["id"])))
    return created


def admin_set_connector_enabled(db, operator_id: int, agent_id: int, connector_id: int, enabled: bool) -> Dict[str, Any]:
    from service import audit_service
    _managed_agent(db, agent_id)
    row = _connector_of(db, agent_id, connector_id)
    row.is_enabled = 1 if enabled else 0
    db.commit()
    audit_service.record(operator_id, "agent.api_connector_enabled" if enabled else "agent.api_connector_disabled",
                         resource_type="agent", resource_id=agent_id, detail=_audit_detail(row))
    return _to_dict(row)


def admin_delete_connector(db, operator_id: int, agent_id: int, connector_id: int) -> None:
    from service import audit_service
    _managed_agent(db, agent_id)
    row = _connector_of(db, agent_id, connector_id)
    detail = _audit_detail(row)
    dao.delete_connector(db, row)
    db.commit()
    audit_service.record(operator_id, "agent.api_connector_deleted", resource_type="agent", resource_id=agent_id, detail=detail)
