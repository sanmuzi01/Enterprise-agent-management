"""飞书自建应用的 tenant_access_token：用 App ID / App Secret 换，缓存到快过期（见 token_store）。"""
from typing import Tuple

from service.integrations import token_store
from service.integrations.base import IntegrationError
from service.integrations.http import call_json


def fetch(app) -> Tuple[str, int]:
    from service.integrations.feishu.client import base_url
    data = call_json("feishu", "POST", base_url() + "/open-apis/auth/v3/tenant_access_token/internal",
                     json_body={"app_id": app.app_id, "app_secret": app.app_secret})
    if data.get("code") != 0 or not data.get("tenant_access_token"):
        raise IntegrationError(f"飞书凭证无效：{str(data.get('msg') or '')[:200]}（请核对 App ID 和 App Secret）")
    return data["tenant_access_token"], int(data.get("expire") or 7200)


def tenant_token(app) -> str:
    return token_store.get_token("feishu", app.app_id, app.app_secret, lambda: fetch(app))


def forget(app) -> None:
    token_store.invalidate("feishu", app.app_id, app.app_secret)
