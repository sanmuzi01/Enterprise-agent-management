"""企业业务中心（Spring Boot，`enterprise-business-hub/`）的签名 HTTP 客户端。

FastAPI 侧签发短时效权限上下文，业务中心只验证签名+有效期+防重放，不重新判断
权限——权限来源只在这一处（docs/enterprise-business-hub-plan.md 第6节）。签名方案
跟 Java 侧 `SignedRequestContextFilter` 完全对称：`X-Context` 是 base64(JSON)，
`X-Signature` 是对这个 base64 字符串算的 HMAC-SHA256 hex，两边按同一份共享密钥
（`ENTERPRISE_HUB_HMAC_SECRET`）算，不要求两边 JSON 序列化逐字节一致——签的是
base64 之后的字符串，不是 JSON 本身，规避了序列化顺序不一致的问题。

复用 `service/http_resilience.py` 的超时/重试/熔断，跟项目里所有其它外部服务调用
（LLM/Embedding）走同一套韧性策略，不是另起一套。
"""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

from service.http_resilience import request_with_retry

SERVICE_NAME = "enterprise_hub"


class EnterpriseHubError(Exception):
    """业务中心返回 4xx/5xx（不是网络层失败，是业务中心自己拒绝了这次请求）。"""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"[{status_code}] {detail}")


def _base_url() -> str:
    return os.getenv("ENTERPRISE_HUB_BASE_URL", "http://127.0.0.1:8090").rstrip("/")


def _secret() -> str:
    return os.getenv("ENTERPRISE_HUB_HMAC_SECRET", "dev-only-shared-secret-change-me")


def sign_context(
        user_id: int,
        team_id: Optional[int],
        scopes: List[str],
        operation: str,
        *,
        is_org_admin: bool = False,
        is_team_admin: bool = False,
) -> Dict[str, str]:
    """`is_org_admin`/`is_team_admin` 必须来自 `resolve_caller_context()`（服务端按
    user_id 查 enterprise_role 算出来的），不能由调用方随便传 True——这两个布尔值是
    企业业务中心判断"能不能跨部门审批/查看"的唯一依据，Java 那边自己没有
    organization_members/team_members 的数据源，只信这里签的值（docs/
    enterprise-business-hub-plan.md 第16节，修复"工具能直接签发审批权限"那个问题）。"""
    context = {
        "user_id": user_id,
        "team_id": team_id,
        "scopes": scopes,
        "operation": operation,
        "is_org_admin": is_org_admin,
        "is_team_admin": is_team_admin,
        "trace_id": str(uuid.uuid4()),
        "timestamp": int(time.time()),
        "nonce": uuid.uuid4().hex,
    }
    context_b64 = base64.b64encode(json.dumps(context).encode("utf-8")).decode("ascii")
    signature = hmac.new(_secret().encode("utf-8"), context_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"X-Context": context_b64, "X-Signature": signature}


def resolve_caller_context(user_id: int, agent_id: Optional[int] = None) -> Dict[str, Any]:
    """算一次当前调用要签的 team_id + is_org_admin + is_team_admin，OA/采购工具共用这一个
    实现，不允许每个工具模块各自重复算一遍权限判断——重复实现是"工具能自行断言权限"
    这个漏洞的根源，只能有一处算法。

    team_id 的推导顺序（修复"一人属于多个部门时被当成第一个部门"这个问题，
    docs/enterprise-rbac-plan.md 相关记录）：
    1. 当前 Agent 自己的部门（`agent.team_id`，仅当 `agent_type='department'`）——
       这种 Agent 只服务一个部门，用它的 team_id 比瞎猜用户"在职的第一个部门"准得多：
       部门负责人管两个部门时，通过"采购部门Agent"操作就该用采购的 team_id，通过
       "HR部门Agent"操作就该用 HR 的，不会互相踩。
    2. 没有部门 Agent 上下文（比如通过中央/个人 Agent 直接调用）才退回"用户自己在职的
       部门"，且只在这种退回场景下才可能出现"一人多部门只能拿到其中一个"的旧问题——
       主路径（通过对应部门 Agent 操作）已经不受这个限制。

    两步都只认"未被停用"的部门（`teams.status='active'`）——部门被管理员停用后，
    不应该再有任何新的业务数据被记到这个部门名下（`is_team_admin`/`is_org_admin`
    本身也已经在 `service/enterprise_access.py` 里挡了停用部门的角色判断，这里是
    双重保险，避免"团队已停用但 team_id 还照样解析出来"这种半失效状态）。
    """
    from models.init_db import SessionLocal
    from sqlalchemy import text
    from service import enterprise_access

    db = SessionLocal()
    try:
        team_id = None
        if agent_id is not None:
            row = db.execute(
                text(
                    "SELECT a.team_id FROM agent a JOIN teams t ON a.team_id = t.id "
                    "WHERE a.id=:aid AND a.agent_type='department' AND t.status='active'"
                ),
                {"aid": agent_id},
            ).first()
            if row and row[0] is not None:
                team_id = row[0]
        if team_id is None:
            row = db.execute(
                text(
                    "SELECT tm.team_id FROM team_members tm JOIN teams t ON tm.team_id = t.id "
                    "WHERE tm.user_id=:u AND tm.status='active' AND t.status='active' "
                    "ORDER BY tm.id LIMIT 1"
                ),
                {"u": user_id},
            ).first()
            team_id = row[0] if row else None
        return {
            "team_id": team_id,
            "is_org_admin": enterprise_access.is_org_admin(db, user_id),
            "is_team_admin": enterprise_access.is_team_admin(db, user_id, team_id),
        }
    finally:
        db.close()


def call(
        method: str,
        path: str,
        user_id: int,
        team_id: Optional[int],
        scopes: List[str],
        operation: str,
        *,
        is_org_admin: bool = False,
        is_team_admin: bool = False,
        json_body: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        timeout_default: float = 10.0,
) -> Any:
    """签名 + 请求 + 韧性封装；4xx/5xx 统一翻成 EnterpriseHubError，调用方
    （Agent 工具）负责把它转成给用户看的自然语言，不要在这里假设调用场景。"""
    headers = sign_context(
        user_id, team_id, scopes, operation, is_org_admin=is_org_admin, is_team_admin=is_team_admin,
    )
    headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    def sender(timeout: float) -> requests.Response:
        return requests.request(
            method, f"{_base_url()}{path}", headers=headers, json=json_body, timeout=timeout,
        )

    response = request_with_retry(
        SERVICE_NAME, sender,
        timeout_env="ENTERPRISE_HUB_TIMEOUT_SECONDS", default_timeout=timeout_default,
    )
    if response.status_code >= 400:
        detail = response.text
        try:
            detail = response.json().get("detail", detail) or response.json().get("message", detail)
        except (ValueError, AttributeError):
            pass
        raise EnterpriseHubError(response.status_code, detail)
    if not response.content:
        return None
    return response.json()
