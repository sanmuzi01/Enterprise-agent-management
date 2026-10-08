"""Redis 客户端管理工具。"""

import os
import threading
import time

try:
    import redis
except ImportError:  # Redis 是可选依赖；未安装时调用方会回退到内存实现。
    redis = None


class RedisClientManager:
    """带自动重连的 Redis 客户端管理器。

    Redis 在上线环境里可能短暂重启。如果只在启动时连接一次，失败后就会一直退回内存，
    多进程限流、验证码、缓存都会失去分布式能力。这里用很轻量的重试窗口恢复连接。
    """

    def __init__(self, decode_responses: bool = False):
        self.decode_responses = decode_responses
        self._client = None
        self._next_retry_at = 0.0
        self._connect_lock = threading.Lock()

    def get_client(self):
        """返回可用 Redis 客户端；不可用时返回 None。"""

        if self._client is not None:
            return self._client
        if time.time() < self._next_retry_at:
            return None
        # 连接过程加锁：并发的第一批请求里，只有一个线程去连；其余线程等它连完直接用，
        # 而不是因为“重试窗口”拿到 None、各自退回进程内的限流 / 缓存（那样多个线程各算各的，并发上限会被成倍突破）。
        with self._connect_lock:
            if self._client is not None:
                return self._client
            if time.time() < self._next_retry_at:
                return None
            redis_url = os.getenv("REDIS_URL")
            if not redis_url or redis is None:
                return None
            try:
                client = redis.Redis.from_url(
                    redis_url,
                    socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT", "0.3")),
                    socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT", "0.5")),
                    decode_responses=self.decode_responses,
                )
                client.ping()
                self._client = client
                return client
            except Exception:
                self._client = None
                # 只有连接失败才开启重试窗口（成功之前不能设：锁外的快速路径会让其它线程误以为“刚试过、连不上”）
                self._next_retry_at = time.time() + float(os.getenv("REDIS_RECONNECT_INTERVAL_SECONDS", "5"))
                return None

    def mark_failed(self) -> None:
        """Redis 操作失败时调用，允许下一轮按重试窗口重连。"""

        self._client = None
        self._next_retry_at = time.time() + float(os.getenv("REDIS_RECONNECT_INTERVAL_SECONDS", "5"))

    def is_available(self) -> bool:
        """检查 Redis 当前是否可用。"""

        client = self.get_client()
        if not client:
            return False
        try:
            client.ping()
            return True
        except Exception:
            self.mark_failed()
            return False
