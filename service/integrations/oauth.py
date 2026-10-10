"""一键授权绑定：员工在平台点“用飞书 / 钉钉授权绑定” → 跳到飞书 / 钉钉授权页 → 授权后回到平台，自动绑定。

需要配置 INTEGRATION_PUBLIC_BASE_URL（飞书 / 钉钉能访问到的后端地址，经 nginx 时带 /api），并把
  {INTEGRATION_PUBLIC_BASE_URL}/integrations/{provider}/oauth/callback
填到飞书“安全设置 → 重定向 URL”、钉钉“登录与分享 → 回调域名”里。没配置时页面只提供绑定码方式。

流程安全：state 是一次性随机串（10 分钟有效，用过即作废），里面记着发起授权的平台用户；回调只认 state，
不认浏览器里的登录态，所以别人伪造回调地址也绑不到别人的账号上。

接口（按两家开放平台文档）：
  飞书：授权页 https://accounts.feishu.cn/open-apis/authen/v1/authorize
        换 token POST /open-apis/authen/v2/oauth/token（grant_type=authorization_code）
        用户信息 GET /open-apis/authen/v1/user_info（open_id、union_id、name）
  钉钉：授权页 https://login.dingtalk.com/oauth2/auth（scope=openid）
        换 token POST /v1.0/oauth2/userAccessToken；用户信息 GET /v1.0/contact/users/me（unionId、nick）
        unionId → userId：POST /topapi/user/getbyunionid（企业应用令牌）
"""
import os
import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from service.exceptions import InvalidInput
from service.integrations import apps
from service.integrations.base import PROVIDER_LABELS, IntegrationError
from service.integrations.http import call_json
from utils.cache import TTLCache

STATE_TTL = 600
_states = TTLCache(default_ttl=STATE_TTL, namespace="integration_oauth_state")


def public_base_url() -> str:
    return os.getenv("INTEGRATION_PUBLIC_BASE_URL", "").rstrip("/")


def web_base_url() -> str:
    return os.getenv("INTEGRATION_WEB_BASE_URL", "").rstrip("/")


def available(provider: str) -> bool:
    return public_base_url().startswith("https://")


def redirect_uri(provider: str) -> str:
    return f"{public_base_url()}/integrations/{provider}/oauth/callback"


def start_sync(db, user_id: int, provider: str) -> Dict[str, str]:
    row = apps.get_row_sync(db, provider)
    if row is None or not row.enabled:
        raise InvalidInput(f"企业还没有开通{PROVIDER_LABELS[provider]}接入")
    if not available(provider):
        raise InvalidInput("管理员还没有配置公网 HTTPS 地址（INTEGRATION_PUBLIC_BASE_URL），请用绑定码方式绑定")
    state = secrets.token_urlsafe(24)
    _states.set(("state", state), {"user_id": user_id, "provider": provider}, ttl=STATE_TTL)
    if provider == "feishu":
        url = "https://accounts.feishu.cn/open-apis/authen/v1/authorize?" + urlencode(
            {"client_id": row.app_id, "redirect_uri": redirect_uri(provider), "state": state})
    else:
        url = "https://login.dingtalk.com/oauth2/auth?" + urlencode(
            {"client_id": row.app_id, "redirect_uri": redirect_uri(provider), "response_type": "code",
             "scope": "openid", "state": state, "prompt": "consent"})
    return {"url": url}


def take_state(state: str, provider: str) -> Optional[int]:
    """state 只能用一次。返回发起授权的平台用户；无效 / 过期 / 平台不对返回 None。"""
    if not state:
        return None
    key = ("state", state)
    data = _states.get(key)
    _states.invalidate(key)
    if not data or data.get("provider") != provider:
        return None
    return int(data["user_id"])


def _feishu_identity(app, code: str) -> Dict[str, Any]:
    from service.integrations.feishu.client import base_url
    token = call_json("feishu", "POST", base_url() + "/open-apis/authen/v2/oauth/token", json_body={
        "grant_type": "authorization_code", "client_id": app.app_id, "client_secret": app.app_secret,
        "code": code, "redirect_uri": redirect_uri("feishu")})
    access = token.get("access_token")
    if not access:
        raise IntegrationError(f"飞书授权失败：{str(token.get('error_description') or token.get('msg') or '')[:200]}")
    info = call_json("feishu", "GET", base_url() + "/open-apis/authen/v1/user_info",
                     headers={"Authorization": f"Bearer {access}"})
    data = info.get("data") or {}
    if not data.get("open_id"):
        raise IntegrationError("飞书没有返回用户身份")
    return {"external_user_id": data["open_id"], "union_id": data.get("union_id"), "name": data.get("name")}


def _dingtalk_identity(app, code: str) -> Dict[str, Any]:
    from service.integrations.dingtalk import client
    token = call_json("dingtalk", "POST", client.base_url() + "/v1.0/oauth2/userAccessToken", json_body={
        "clientId": app.app_id, "clientSecret": app.app_secret, "code": code, "grantType": "authorization_code"})
    access = token.get("accessToken")
    if not access:
        raise IntegrationError("钉钉授权失败")
    me = call_json("dingtalk", "GET", client.base_url() + "/v1.0/contact/users/me",
                   headers={"x-acs-dingtalk-access-token": access})
    union_id = me.get("unionId")
    if not union_id:
        raise IntegrationError("钉钉没有返回用户身份")
    result = client.oapi(app, "/topapi/user/getbyunionid", {"unionid": union_id})
    if not result.get("userid"):
        raise IntegrationError("这个钉钉账号不在企业通讯录里（可能不是本企业员工）")
    return {"external_user_id": str(result["userid"]), "union_id": union_id, "name": me.get("nick")}


def callback_sync(db, provider: str, code: str, state: str) -> Dict[str, Any]:
    """授权回调。返回 {ok, message}，路由据此跳回平台设置页。"""
    from service.integrations import self_binding
    user_id = take_state(state, provider)
    if user_id is None:
        return {"ok": False, "message": "授权链接已失效，请回到平台重新发起绑定"}
    if not code:
        return {"ok": False, "message": "你取消了授权"}
    app = apps.load_enabled_sync(db, provider)
    if app is None:
        return {"ok": False, "message": f"企业还没有开通{PROVIDER_LABELS[provider]}接入"}
    try:
        identity = (_feishu_identity if provider == "feishu" else _dingtalk_identity)(app, code)
    except IntegrationError as exc:
        return {"ok": False, "message": str(exc)}
    ok, message = self_binding.bind_sync(db, provider, app.app_id, identity["external_user_id"], user_id,
                                         external_name=identity.get("name"), union_id=identity.get("union_id"), via="oauth")
    return {"ok": ok, "message": message}
