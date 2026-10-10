"""调用飞书 / 钉钉开放接口的 HTTP 封装：复用平台的超时、重试、熔断（service/http_resilience.py），
不跟随重定向，返回体太大直接拒绝，错误信息里不带密钥和令牌。"""
from typing import Any, Dict, Optional

import requests

from service.http_resilience import CircuitOpenError, request_with_retry
from service.integrations.base import IntegrationError

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def call_json(service: str, method: str, url: str, *, json_body: Optional[Dict[str, Any]] = None,
              params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    def send(timeout: float) -> requests.Response:
        return requests.request(method, url, json=json_body, params=params, headers=headers or {},
                                timeout=timeout, allow_redirects=False)

    try:
        response = request_with_retry(service, send, "INTEGRATION_HTTP_TIMEOUT_SECONDS", 10.0)
    except CircuitOpenError:
        raise IntegrationError("外部平台连续失败，暂时停止调用，稍后会自动恢复") from None
    except requests.RequestException as exc:
        raise IntegrationError(f"连不上外部平台：{type(exc).__name__}") from None
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise IntegrationError("外部平台返回的数据过大")
    try:
        data = response.json()
    except ValueError:
        raise IntegrationError(f"外部平台返回的不是 JSON（HTTP {response.status_code}）") from None
    if response.status_code >= 400:
        message = data.get("msg") or data.get("message") or data.get("errmsg") or ""
        raise IntegrationError(f"外部平台拒绝了请求（HTTP {response.status_code}）：{str(message)[:200]}")
    return data
