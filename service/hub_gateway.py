"""调用 Java 业务系统的异步封装：统一的错误翻译、已签名的范围路径、写操作的幂等键。
财务凭证、IT 服务台等"部门内部业务"共用。"""
import asyncio
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from service import enterprise_hub_client as hub
from service.exceptions import AppError, Conflict, InvalidInput, NotFound, PermissionDenied, UpstreamError


def translate_hub_error(exc: hub.EnterpriseHubError) -> AppError:
    status = exc.status_code
    if status == 400:
        return InvalidInput(exc.detail)
    if status == 403:
        return PermissionDenied(exc.detail)
    if status == 404:
        return NotFound(exc.detail)
    if status == 409:
        return Conflict(exc.detail)
    return UpstreamError(exc.detail)


def scoped_path(base: str, scope: Optional[List[int]] = None, **params: Any) -> str:
    """scopeTeamIds 与其它查询参数一起写进路径；整条路径（含查询串）都在 HMAC 签名里，改不了。"""
    query: Dict[str, Any] = {}
    if scope is not None:
        query["scopeTeamIds"] = ",".join(str(t) for t in scope)
    query.update({k: v for k, v in params.items() if v is not None})
    return f"{base}?{urlencode(query, safe=',')}" if query else base


async def call_hub(method: str, path: str, user_id: int, team_id: Optional[int], scopes: List[str], operation: str,
                   json_body: Optional[Dict[str, Any]] = None, write: bool = False, *,
                   idempotency_key: Optional[str] = None, is_org_admin: bool = False, is_team_admin: bool = False) -> Any:
    try:
        return await asyncio.to_thread(
            hub.call, method, path, user_id, team_id, scopes, operation,
            json_body=json_body, idempotency_key=idempotency_key or (str(uuid.uuid4()) if write else None),
            is_org_admin=is_org_admin, is_team_admin=is_team_admin,
        )
    except hub.EnterpriseHubError as exc:
        raise translate_hub_error(exc)
