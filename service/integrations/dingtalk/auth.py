"""钉钉企业内部应用的 accessToken：用 AppKey / AppSecret 换，缓存到快过期（见 token_store）。"""
from typing import Tuple

from service.integrations import token_store
from service.integrations.base import IntegrationError
from service.integrations.http import call_json


def fetch(app) -> Tuple[str, int]:
    from service.integrations.dingtalk.client import base_url
    data = call_json("dingtalk", "POST", base_url() + "/v1.0/oauth2/accessToken",
                     json_body={"appKey": app.app_id, "appSecret": app.app_secret})
    if not data.get("accessToken"):
        raise IntegrationError(f"钉钉凭证无效：{str(data.get('message') or '')[:200]}（请核对 AppKey 和 AppSecret）")
    return data["accessToken"], int(data.get("expireIn") or 7200)


def access_token(app) -> str:
    return token_store.get_token("dingtalk", app.app_id, app.app_secret, lambda: fetch(app))


def forget(app) -> None:
    token_store.invalidate("dingtalk", app.app_id, app.app_secret)
