import copy
import datetime as _dt
import json
import logging
import time
from pathlib import PurePath
from threading import RLock
from typing import Any, Callable, Dict, Hashable, Optional, Tuple
from urllib.parse import quote

from utils.redis_client import RedisClientManager

logger = logging.getLogger("cache")

# Redis 里的键前缀带版本：旧版本用 pickle 存的数据不会被读到（更不会被反序列化），自然过期即可
_KEY_VERSION = "v2"
_TYPE_TAG = "__t"


def _json_default(value: Any):
    """缓存值里允许出现的、JSON 原生不支持的类型。其它类型一律拒绝（不写入 Redis，只留在进程内存里）。"""
    if isinstance(value, _dt.datetime):
        return {_TYPE_TAG: "datetime", "v": value.isoformat()}
    if isinstance(value, _dt.date):
        return {_TYPE_TAG: "date", "v": value.isoformat()}
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=repr)
    raise TypeError(f"缓存值里有不支持写入 Redis 的类型：{type(value).__name__}")


def _json_hook(obj: dict):
    tag = obj.get(_TYPE_TAG)
    if tag == "datetime":
        return _dt.datetime.fromisoformat(obj["v"])
    if tag == "date":
        return _dt.date.fromisoformat(obj["v"])
    return obj


def encode_value(value: Any) -> bytes:
    """缓存值 → 字节。只用 JSON：Redis 里的内容即使被别人改过，最坏也只是一份错误的数据，不会变成可执行的代码（不再使用 pickle）。"""
    return json.dumps(value, ensure_ascii=False, default=_json_default, separators=(",", ":")).encode("utf-8")


def decode_value(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw, object_hook=_json_hook)


class TTLCache:
    """带过期时间的缓存：优先使用 Redis，不可用时自动回退到进程内内存缓存。"""

    def __init__(self, default_ttl: int = 60, namespace: str = "default"):
        self.default_ttl = default_ttl
        # namespace 用来隔离不同业务缓存，避免模型配置和 Skill 配置的 key 撞到一起。
        self.namespace = namespace
        self._items: Dict[Hashable, Tuple[float, Any]] = {}
        self._lock = RLock()
        self._redis = RedisClientManager(decode_responses=False)

    def _key_parts(self, key: Hashable) -> Tuple[str, ...]:
        """把业务 key 转成 Redis key 的安全片段。

        业务层常用 tuple 做 key，例如 ("llm_api_key", user_id, model_name)。
        Redis key 是字符串，这里统一转义，避免模型名里的斜杠、空格影响前缀匹配。
        """
        if isinstance(key, tuple):
            return tuple(quote(str(part), safe="") for part in key)
        return (quote(str(key), safe=""),)

    def _redis_key(self, key: Hashable) -> str:
        """生成单个缓存项的 Redis key。"""
        return f"cache:{_KEY_VERSION}:{self.namespace}:" + ":".join(self._key_parts(key))

    def _redis_prefix(self, prefix: Tuple[Any, ...]) -> str:
        """生成批量失效用的 Redis key 前缀。"""
        parts = tuple(quote(str(part), safe="") for part in prefix)
        return f"cache:{_KEY_VERSION}:{self.namespace}:" + ":".join(parts) + "*"

    def _redis_get(self, key: Hashable) -> Tuple[bool, Optional[Any]]:
        """从 Redis 读取缓存。

        返回 (hit, value)，而不是只返回 value，是为了区分“没命中”和“命中值为 None”。
        """
        client = self._redis.get_client()
        if not client:
            return False, None
        try:
            raw = client.get(self._redis_key(key))
        except Exception:
            self._redis.mark_failed()
            return False, None
        if raw is None:
            return False, None
        try:
            return True, decode_value(raw)
        except Exception:      # 内容不是我们写的合法 JSON（被改过、截断、版本不符）：当作没命中并删掉，不算 Redis 故障
            logger.warning("缓存内容无法解析，已丢弃：%s", self._redis_key(key))
            try:
                client.delete(self._redis_key(key))
            except Exception:  # noqa: BLE001
                pass
            return False, None

    def _redis_set(self, key: Hashable, value: Any, ttl: int) -> bool:
        """写入 Redis 缓存，失败时返回 False 让调用方回退到内存缓存。"""
        client = self._redis.get_client()
        if not client:
            return False
        try:
            payload = encode_value(value)
        except (TypeError, ValueError):      # 值里有不能安全序列化的东西：只放进程内存，不是 Redis 的问题
            return False
        try:
            client.setex(self._redis_key(key), ttl, payload)
            return True
        except Exception:
            self._redis.mark_failed()
            return False

    def get_or_set(self, key: Hashable, factory: Callable[[], Any], ttl: Optional[int] = None) -> Any:
        """读取缓存；未命中时调用 factory 生成值并写入缓存。"""
        ttl_seconds = ttl if ttl is not None else self.default_ttl
        hit, cached = self._redis_get(key)
        if hit:
            # 返回深拷贝，避免调用方修改返回对象时污染缓存中的原始值。
            return copy.deepcopy(cached)

        now = time.time()
        with self._lock:
            item = self._items.get(key)
            if item and item[0] > now:
                # 内存缓存同样返回深拷贝，保持 Redis 和内存两种后端行为一致。
                return copy.deepcopy(item[1])
        value = factory()
        if not self._redis_set(key, value, ttl_seconds):
            expires_at = now + ttl_seconds
            with self._lock:
                self._items[key] = (expires_at, copy.deepcopy(value))
        return value

    def get(self, key: Hashable) -> Optional[Any]:
        """读取缓存；未命中或已过期时返回 None。"""
        hit, cached = self._redis_get(key)
        if hit:
            return copy.deepcopy(cached)

        now = time.time()
        with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            if item[0] <= now:
                self._items.pop(key, None)
                return None
            return copy.deepcopy(item[1])

    def set(self, key: Hashable, value: Any, ttl: Optional[int] = None) -> None:
        """主动写入缓存。"""
        ttl_seconds = ttl if ttl is not None else self.default_ttl
        if not self._redis_set(key, value, ttl_seconds):
            with self._lock:
                self._items[key] = (time.time() + ttl_seconds, copy.deepcopy(value))

    def invalidate(self, key: Hashable = None, prefix: Tuple[Any, ...] = None) -> int:
        """删除缓存。

        key 精确删除单个缓存；prefix 删除一类缓存；两者都不传时清空当前 namespace。
        返回删除数量，便于健康检查或调试时判断是否真正清理到了数据。
        """
        redis_count = 0
        client = self._redis.get_client()
        if client:
            try:
                if key is not None:
                    redis_count = client.delete(self._redis_key(key))
                elif prefix is None:
                    keys = list(client.scan_iter(f"cache:{_KEY_VERSION}:{self.namespace}:*"))
                    redis_count = client.delete(*keys) if keys else 0
                else:
                    keys = list(client.scan_iter(self._redis_prefix(prefix)))
                    redis_count = client.delete(*keys) if keys else 0
            except Exception:
                self._redis.mark_failed()

        with self._lock:
            if key is not None:
                memory_count = 1 if self._items.pop(key, None) is not None else 0
                return redis_count + memory_count
            if prefix is None:
                memory_count = len(self._items)
                self._items.clear()
                return redis_count + memory_count
            keys = [item_key for item_key in self._items if isinstance(item_key, tuple) and item_key[:len(prefix)] == prefix]
            for item_key in keys:
                self._items.pop(item_key, None)
            return redis_count + len(keys)

    def stats(self) -> Dict[str, Any]:
        """返回缓存状态，供 /health 和设置页展示。"""
        now = time.time()
        with self._lock:
            expired = [key for key, item in self._items.items() if item[0] <= now]
            for key in expired:
                self._items.pop(key, None)
            memory_size = len(self._items)

        redis_size = 0
        redis_ok = False
        client = self._redis.get_client()
        if client:
            try:
                redis_size = sum(1 for _ in client.scan_iter(f"cache:{_KEY_VERSION}:{self.namespace}:*"))
                redis_ok = True
            except Exception:
                self._redis.mark_failed()

        return {
            "backend": "redis" if redis_ok else "memory",
            "redis_ok": redis_ok,
            "size": redis_size if redis_ok else memory_size,
            "memory_size": memory_size,
            "redis_size": redis_size,
            "expired_removed": len(expired),
        }


config_cache = TTLCache(default_ttl=120, namespace="config")
skill_cache = TTLCache(default_ttl=60, namespace="skill")
verification_cache = TTLCache(default_ttl=300, namespace="verification")
