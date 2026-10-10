"""飞书开放接口客户端：带 tenant_access_token 调接口，code != 0 当作失败。地址可用 FEISHU_BASE_URL 换成私有化部署的地址。"""
import os
from typing import Any, Dict, Optional

from service.integrations.base import IntegrationError
from service.integrations.feishu import auth
from service.integrations.http import call_json


def base_url() -> str:
    return os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn").rstrip("/")


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
