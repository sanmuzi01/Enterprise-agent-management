"""钉钉开放接口客户端。新版接口（api.dingtalk.com）用请求头 x-acs-dingtalk-access-token；
通讯录还在旧版接口（oapi.dingtalk.com/topapi，access_token 放 URL 参数，errcode != 0 是失败）。
地址可用 DINGTALK_BASE_URL / DINGTALK_OAPI_URL 换掉（测试、专属钉钉）。"""
import os
from typing import Any, Dict, Optional

from service.integrations.base import IntegrationError
from service.integrations.dingtalk import auth
from service.integrations.http import call_json


def base_url() -> str:
    return os.getenv("DINGTALK_BASE_URL", "https://api.dingtalk.com").rstrip("/")


def oapi_url() -> str:
    return os.getenv("DINGTALK_OAPI_URL", "https://oapi.dingtalk.com").rstrip("/")


def call(app, method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        return call_json("dingtalk", method, base_url() + path, json_body=json_body,
                         headers={"x-acs-dingtalk-access-token": auth.access_token(app), "Content-Type": "application/json"})
    except IntegrationError as exc:
        if "HTTP 401" in str(exc):
            auth.forget(app)
        raise


def oapi(app, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    data = call_json("dingtalk", "POST", oapi_url() + path, json_body=body, params={"access_token": auth.access_token(app)})
    if data.get("errcode") not in (0, None):
        if data.get("errcode") in (40014, 42001):           # 令牌失效：下次重新换
            auth.forget(app)
        raise IntegrationError(f"钉钉返回错误 {data.get('errcode')}：{str(data.get('errmsg') or '')[:200]}")
    return data.get("result") or {}
