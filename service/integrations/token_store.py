"""外部平台访问令牌（飞书 tenant_access_token、钉钉 accessToken）的缓存。

令牌有效期约 2 小时，每次调用都去换会被限流；缓存时**加密后**再放进缓存（有 Redis 时在 Redis 里，多个进程共用），
比平台给的过期时间早 5 分钟失效；凭证改了（app_id、密钥）缓存键也跟着变，旧令牌自然作废。
"""
import hashlib
import time
from typing import Callable, Tuple

from utils.cache import TTLCache
from utils.crypto import decrypt, encrypt

EARLY_EXPIRE_SECONDS = 300
_cache = TTLCache(default_ttl=3600, namespace="integration_token")


def _key(provider: str, app_id: str, app_secret: str) -> Tuple[str, str, str]:
    fingerprint = hashlib.sha256(f"{app_id}:{app_secret}".encode("utf-8")).hexdigest()[:16]   # 不把密钥本身放进键
    return ("token", provider, fingerprint)


def get_token(provider: str, app_id: str, app_secret: str, fetch: Callable[[], Tuple[str, int]]) -> str:
    """fetch() 返回 (令牌, 有效秒数)。"""
    key = _key(provider, app_id, app_secret)
    cached = _cache.get(key)
    if cached and cached.get("expires_at", 0) > time.time():
        return decrypt(cached["token"])
    token, expires_in = fetch()
    ttl = max(60, int(expires_in) - EARLY_EXPIRE_SECONDS)
    _cache.set(key, {"token": encrypt(token), "expires_at": time.time() + ttl}, ttl=ttl)
    return token


def invalidate(provider: str, app_id: str, app_secret: str) -> None:
    _cache.invalidate(_key(provider, app_id, app_secret))
