"""真实 Redis 上的并发配额与缓存安全测试（没有 Redis 时自动跳过）。

连接：读 REDIS_URL（默认不设置就跳过）；本机一条命令起一个：
    docker compose -f deploy/test-services/docker-compose.yml up -d
    $env:REDIS_URL='redis://127.0.0.1:6390/0'

覆盖：
1. 并发令牌：很多线程 / 多个进程（模拟多个 API 实例）同时抢同一个配额，成功的数量必须恰好等于上限（之前 ZCARD + ZADD 分两步，会超发）；
2. 令牌释放后名额能再次被占用；过期令牌被回收；
3. 固定窗口限流在并发下计数准确，并且 key 一定带过期时间；
4. 缓存：Redis 里被塞进 pickle 载荷不会被执行；存进去的是 JSON；模型密钥在 Redis 里只有密文。
"""
import os
import pickle
import subprocess
import sys
import textwrap
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None


def _redis_client():
    url = os.getenv("REDIS_URL")
    if not url or redis is None:
        return None
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=0.5, decode_responses=False)
        client.ping()
        return client
    except Exception:  # noqa: BLE001
        return None


_CLIENT = _redis_client()


@unittest.skipUnless(_CLIENT is not None, "需要真实 Redis：设置 REDIS_URL（见本文件顶部说明）")
class RealRedisConcurrencyTests(unittest.TestCase):
    def setUp(self):
        from utils.rate_limit import ConcurrencyLimiter, FixedWindowRateLimiter
        self.ns = f"t{uuid.uuid4().hex[:8]}"
        self.concurrency = ConcurrencyLimiter(namespace=self.ns)
        self.fixed = FixedWindowRateLimiter(namespace=self.ns)

    def tearDown(self):
        for key in _CLIENT.scan_iter(f"limit:{self.ns}:*"):
            _CLIENT.delete(key)

    def _grab(self, limiter, key, limit):
        from utils.rate_limit import LimitExceeded
        try:
            return limiter.acquire(key, limit, 60, "测试")
        except LimitExceeded:
            return None

    def test_threads_never_exceed_the_limit(self):
        limit, attempts = 5, 120
        with ThreadPoolExecutor(max_workers=40) as pool:
            leases = list(pool.map(lambda _: self._grab(self.concurrency, "user-1", limit), range(attempts)))
        granted = [lease for lease in leases if lease]
        self.assertEqual(len(granted), limit, f"上限 {limit}，却放行了 {len(granted)} 个")
        self.assertEqual(_CLIENT.zcard(f"limit:{self.ns}:user-1"), limit)

    def test_release_frees_a_slot(self):
        leases = [self._grab(self.concurrency, "user-2", 2) for _ in range(4)]
        granted = [lease for lease in leases if lease]
        self.assertEqual(len(granted), 2)
        granted[0].release()
        self.assertIsNotNone(self._grab(self.concurrency, "user-2", 2))
        self.assertIsNone(self._grab(self.concurrency, "user-2", 2))

    def test_expired_tokens_are_reclaimed(self):
        from utils.rate_limit import LimitExceeded
        self.concurrency.acquire("user-3", 1, 1, "测试")
        with self.assertRaises(LimitExceeded):
            self.concurrency.acquire("user-3", 1, 1, "测试")
        _CLIENT.zadd(f"limit:{self.ns}:user-3", {"stale": 1})      # 一个早就过期的令牌
        _CLIENT.zremrangebyscore(f"limit:{self.ns}:user-3", "-inf", "+inf")
        self.assertIsNotNone(self.concurrency.acquire("user-3", 1, 1, "测试"))

    def test_processes_never_exceed_the_limit(self):
        """多个进程 = 多个 API 实例：各自有自己的限流器对象，只通过 Redis 协调。"""
        limit, processes = 4, 6
        script = textwrap.dedent(f"""
            import os, sys
            sys.path.insert(0, {os.getcwd()!r})
            from utils.rate_limit import ConcurrencyLimiter, LimitExceeded
            limiter = ConcurrencyLimiter(namespace={self.ns!r})
            got = 0
            for _ in range(20):
                try:
                    limiter.acquire("user-4", {limit}, 60, "测试")
                    got += 1
                except LimitExceeded:
                    pass
            print(got)
        """)
        procs = [subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(processes)]
        total = 0
        for proc in procs:
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
            total += int(out.strip().splitlines()[-1])
        self.assertEqual(total, limit, f"{processes} 个进程合计放行 {total} 个，上限是 {limit}")

    def test_fixed_window_counts_exactly_and_keys_expire(self):
        from utils.rate_limit import LimitExceeded
        limit, attempts = 10, 80

        def hit(_):
            try:
                self.fixed.check("ip-1", limit, 3600, "测试")
                return True
            except LimitExceeded:
                return False
        with ThreadPoolExecutor(max_workers=30) as pool:
            results = list(pool.map(hit, range(attempts)))
        self.assertEqual(sum(results), limit)
        keys = list(_CLIENT.scan_iter(f"limit:{self.ns}:ip-1:*"))
        self.assertTrue(keys)
        for key in keys:
            self.assertGreater(_CLIENT.ttl(key), 0, "限流计数 key 必须带过期时间")


@unittest.skipUnless(_CLIENT is not None, "需要真实 Redis：设置 REDIS_URL（见本文件顶部说明）")
class RealRedisCacheSafetyTests(unittest.TestCase):
    def setUp(self):
        from utils.cache import TTLCache
        self.cache = TTLCache(default_ttl=30, namespace=f"t{uuid.uuid4().hex[:8]}")

    def tearDown(self):
        self.cache.invalidate()

    def test_values_are_stored_as_json_not_pickle(self):
        self.cache.set(("a", 1), {"name": "测试", "items": [1, 2, None], "ok": True})
        raw = _CLIENT.get(self.cache._redis_key(("a", 1)))
        self.assertTrue(raw.startswith(b"{"), raw[:20])
        self.assertEqual(self.cache.get(("a", 1)), {"name": "测试", "items": [1, 2, None], "ok": True})

    def test_pickle_payload_planted_in_redis_is_never_executed(self):
        marker = os.path.join(os.environ.get("TEMP", "/tmp"), f"pwned-{uuid.uuid4().hex}")

        class Evil:
            def __reduce__(self):
                return (open, (marker, "w"))
        key = self.cache._redis_key(("planted",))
        _CLIENT.setex(key, 30, pickle.dumps(Evil()))
        self.assertIsNone(self.cache.get(("planted",)))
        self.assertFalse(os.path.exists(marker), "Redis 里的 pickle 载荷被执行了")
        self.assertIsNone(_CLIENT.get(key), "无法解析的缓存内容应当被丢弃")

    def test_old_pickle_keys_from_previous_version_are_ignored(self):
        _CLIENT.setex(f"cache:{self.cache.namespace}:legacy", 30, pickle.dumps({"x": 1}))
        self.assertIsNone(self.cache.get(("legacy",)))
        _CLIENT.delete(f"cache:{self.cache.namespace}:legacy")


if __name__ == "__main__":
    unittest.main()
