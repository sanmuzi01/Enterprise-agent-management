import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Dict, Iterator, Optional, Tuple

from utils.redis_client import RedisClientManager


class LimitExceeded(Exception):
    """请求频率或并发数量超过限制时抛出。"""

    def __init__(self, message: str, retry_after: int = 1):
        super().__init__(message)
        self.message = message
        self.retry_after = max(1, int(retry_after))


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量，配置缺失或格式错误时使用默认值。"""

    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# 并发令牌的“清理过期 → 数当前 → 判断上限 → 占位”必须是 Redis 里的一个原子操作：
# 分成几次往返，两个 API 实例会同时看到“还没满”然后都占位，上限就被突破了。
# 返回 1 = 占到了；返回 0 = 已满。
_ACQUIRE_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, ARGV[1])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
    return 0
end
redis.call('ZADD', KEYS[1], ARGV[4], ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""


class _RedisMixin:
    """Redis 客户端初始化混入类，统一处理连接失败后的降级。"""

    def __init__(self):
        self._redis = RedisClientManager(decode_responses=True)


class FixedWindowRateLimiter(_RedisMixin):
    """固定时间窗口限流器，用于限制某个用户在一段时间内的请求次数。"""

    def __init__(self, namespace: str = "rate"):
        super().__init__()
        self.namespace = namespace
        self._lock = RLock()
        self._items: Dict[str, Tuple[int, float]] = {}

    def check(self, key: str, limit: int, window_seconds: int, label: str) -> None:
        """检查当前请求是否超过固定窗口限制。"""

        if limit <= 0:
            # limit <= 0 表示关闭该限制，便于本地调试或特殊部署场景临时放开。
            return
        now = time.time()
        # 固定窗口：同一个窗口内递增计数，窗口结束后自动过期。
        redis_key = f"limit:{self.namespace}:{key}:{int(now // window_seconds)}"
        client = self._redis.get_client()
        if client:
            try:
                # INCR 和 EXPIRE 在同一个事务里：进程恰好死在两步之间，也不会留下一个永不过期的计数 key。
                # 多加 2 秒是为了覆盖时钟边界，避免 key 过早消失导致统计不稳定。
                pipe = client.pipeline()
                pipe.incr(redis_key)
                pipe.expire(redis_key, window_seconds + 2)
                current = pipe.execute()[0]
                if current > limit:
                    ttl = client.ttl(redis_key)
                    raise LimitExceeded(f"{label}过于频繁，请稍后再试", ttl if ttl > 0 else window_seconds)
                return
            except LimitExceeded:
                raise
            except Exception:
                # Redis 异常时降级到内存限流，不影响主请求流程继续受保护。
                self._redis.mark_failed()

        expires_at = now + window_seconds
        with self._lock:
            current, old_expires_at = self._items.get(key, (0, expires_at))
            if old_expires_at <= now:
                current, old_expires_at = 0, expires_at
            current += 1
            self._items[key] = (current, old_expires_at)
            if current > limit:
                raise LimitExceeded(f"{label}过于频繁，请稍后再试", int(old_expires_at - now) or 1)

    def stats(self) -> Dict[str, object]:
        now = time.time()
        with self._lock:
            expired = [key for key, item in self._items.items() if item[1] <= now]
            for key in expired:
                self._items.pop(key, None)
            memory_size = len(self._items)
        redis_ok = False
        if self._redis.is_available():
            redis_ok = True
        return {"backend": "redis" if redis_ok else "memory", "redis_ok": redis_ok, "memory_size": memory_size}


@dataclass
class ConcurrencyLease:
    """并发令牌，任务结束时必须释放。"""

    limiter: "ConcurrencyLimiter"
    key: str
    token: str
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.limiter.release(self.key, self.token)
        self.released = True


class ConcurrencyLimiter(_RedisMixin):
    """并发控制器，用于限制同一用户同时运行的重任务数量。"""

    def __init__(self, namespace: str = "concurrency"):
        super().__init__()
        self.namespace = namespace
        self._lock = RLock()
        self._items: Dict[str, Dict[str, float]] = {}

    def _acquire_script(self, client):
        """按连接缓存脚本对象（首次 EVALSHA 找不到脚本时 redis-py 会自动改用 EVAL 装载）。"""
        cached = getattr(self, "_script_cache", None)
        if cached is None or cached[0] is not client:
            cached = (client, client.register_script(_ACQUIRE_LUA))
            self._script_cache = cached
        return cached[1]

    def acquire(self, key: str, limit: int, ttl_seconds: int, label: str) -> ConcurrencyLease:
        """尝试获取并发令牌，超过上限时抛出 LimitExceeded。"""

        if limit <= 0:
            # limit <= 0 表示关闭并发限制，返回空 token 的 lease，释放时会自动跳过。
            return ConcurrencyLease(self, key, "")
        token = uuid.uuid4().hex
        now = time.time()
        redis_key = f"limit:{self.namespace}:{key}"
        client = self._redis.get_client()
        if client:
            try:
                # sorted set 的 score 存过期时间；token 是本次任务的唯一凭证，任务结束时按 token 精确释放。
                granted = self._acquire_script(client)(
                    keys=[redis_key], args=[now, limit, token, now + ttl_seconds, ttl_seconds + 5])
                if not granted:
                    raise LimitExceeded(f"{label}正在运行的任务过多，请等待已有任务完成", ttl_seconds)
                return ConcurrencyLease(self, key, token)
            except LimitExceeded:
                raise
            except Exception:
                # Redis 异常时降级到内存并发控制，只保护当前进程。
                self._redis.mark_failed()

        with self._lock:
            active = self._items.setdefault(key, {})
            expired = [item_token for item_token, expires_at in active.items() if expires_at <= now]
            for item_token in expired:
                active.pop(item_token, None)
            if len(active) >= limit:
                raise LimitExceeded(f"{label}正在运行的任务过多，请等待已有任务完成", ttl_seconds)
            active[token] = now + ttl_seconds
        return ConcurrencyLease(self, key, token)

    def release(self, key: str, token: str) -> None:
        """释放并发令牌。"""

        if not token:
            # 空 token 来自“关闭并发限制”的场景，无需释放。
            return
        redis_key = f"limit:{self.namespace}:{key}"
        client = self._redis.get_client()
        if client:
            try:
                client.zrem(redis_key, token)
                return
            except Exception:
                self._redis.mark_failed()
        with self._lock:
            active = self._items.get(key)
            if active:
                active.pop(token, None)
                if not active:
                    self._items.pop(key, None)

    def stats(self) -> Dict[str, object]:
        now = time.time()
        with self._lock:
            for key in list(self._items):
                active = self._items[key]
                for token, expires_at in list(active.items()):
                    if expires_at <= now:
                        active.pop(token, None)
                if not active:
                    self._items.pop(key, None)
            memory_active = sum(len(active) for active in self._items.values())
        redis_ok = False
        if self._redis.is_available():
            redis_ok = True
        return {"backend": "redis" if redis_ok else "memory", "redis_ok": redis_ok, "memory_active": memory_active}


rate_limiter = FixedWindowRateLimiter()
concurrency_limiter = ConcurrencyLimiter()


def require_limit(key: str, limit_env: str, default_limit: int, window_env: str, default_window: int, label: str) -> None:
    """按环境变量配置执行一次限流检查。"""

    rate_limiter.check(
        key=key,
        limit=_env_int(limit_env, default_limit),
        window_seconds=_env_int(window_env, default_window),
        label=label,
    )


@contextmanager
def concurrency_guard(
    key: str,
    limit_env: str,
    default_limit: int,
    ttl_env: str,
    default_ttl: int,
    label: str,
) -> Iterator[None]:
    """上下文管理器：进入时占用并发名额，退出时自动释放。"""

    lease: Optional[ConcurrencyLease] = concurrency_limiter.acquire(
        key=key,
        limit=_env_int(limit_env, default_limit),
        ttl_seconds=_env_int(ttl_env, default_ttl),
        label=label,
    )
    try:
        yield
    finally:
        if lease:
            lease.release()
