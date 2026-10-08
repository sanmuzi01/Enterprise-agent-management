"""Redis 不可用时限流 / 并发控制的降级策略（不需要真实 Redis，用一个“永远连不上”的客户端管理器）。

约定：只有“配置了 Redis（REDIS_URL）却用不了”才算降级；没配置 Redis 的单进程部署本来就用内存，不是故障。
· 普通限流：各进程用自己的内存额度；
· 安全敏感入口（登录 / 短信 / 注册 / 找回密码，critical=True）：open（同上）/ strict（额度按进程数均分，合计不超过原额度，生产默认）/ closed（直接拒绝）；
· 重任务并发名额：closed（停止接收新任务，生产默认）/ local；
· 每次降级都有 Prometheus 计数。"""
import os
import unittest
import uuid
from unittest import mock

from prometheus_client import REGISTRY

from utils import rate_limit
from utils.rate_limit import ConcurrencyLimiter, FixedWindowRateLimiter, LimitExceeded


class DownRedis:
    """配置了 Redis 但永远连不上。"""

    def get_client(self):
        return None

    def mark_failed(self):
        pass

    def is_available(self):
        return False


class BrokenClient:
    """连得上但每个命令都报错（Redis 中途挂了）。"""

    def pipeline(self):
        raise ConnectionError("redis went away")

    def register_script(self, _script):
        raise ConnectionError("redis went away")

    def zrem(self, *a, **k):
        raise ConnectionError("redis went away")


class BrokenRedis(DownRedis):
    def get_client(self):
        return BrokenClient()


def counter(namespace: str, mode: str) -> float:
    return REGISTRY.get_sample_value("agent_limiter_redis_degraded_total", {"limiter": namespace, "mode": mode}) or 0.0


def fixed(redis_manager=None):
    limiter = FixedWindowRateLimiter(namespace=f"t{uuid.uuid4().hex[:8]}")
    limiter._redis = redis_manager or DownRedis()
    return limiter


def concurrency(redis_manager=None):
    limiter = ConcurrencyLimiter(namespace=f"t{uuid.uuid4().hex[:8]}")
    limiter._redis = redis_manager or DownRedis()
    return limiter


def attempts(limiter, n, critical, limit=10):
    granted = 0
    for _ in range(n):
        try:
            limiter.check("k", limit, 60, "测试", critical=critical)
            granted += 1
        except LimitExceeded:
            pass
    return granted


class FixedWindowDegradationTests(unittest.TestCase):
    def env(self, **values):
        base = {"REDIS_URL": "redis://127.0.0.1:1/0", "APP_ENV": "development", "API_WORKERS": "2"}
        base.update({k: str(v) for k, v in values.items()})
        return mock.patch.dict(os.environ, base)

    def test_ordinary_limit_falls_back_to_local_memory(self):
        with self.env(RATE_LIMIT_CRITICAL_FALLBACK="closed"):
            limiter = fixed()
            self.assertEqual(attempts(limiter, 25, critical=False), 10)
            self.assertGreater(counter(limiter.namespace, "local"), 0)

    def test_critical_open_uses_the_full_local_allowance(self):
        with self.env(RATE_LIMIT_CRITICAL_FALLBACK="open"):
            self.assertEqual(attempts(fixed(), 25, critical=True), 10)

    def test_critical_strict_splits_the_allowance_across_workers(self):
        with self.env(RATE_LIMIT_CRITICAL_FALLBACK="strict", API_WORKERS=2):
            limiter = fixed()
            self.assertEqual(attempts(limiter, 25, critical=True), 5, "每个进程只用一半，两个进程合计不超过原额度 10")
            self.assertGreater(counter(limiter.namespace, "strict"), 0)
        with self.env(RATE_LIMIT_CRITICAL_FALLBACK="strict", API_WORKERS=4):
            self.assertEqual(attempts(fixed(), 25, critical=True, limit=10), 2)
        with self.env(RATE_LIMIT_CRITICAL_FALLBACK="strict", API_WORKERS=100):
            self.assertEqual(attempts(fixed(), 25, critical=True, limit=10), 1, "至少留 1 次，不能把入口完全堵死")

    def test_critical_closed_rejects_everything(self):
        with self.env(RATE_LIMIT_CRITICAL_FALLBACK="closed"):
            limiter = fixed()
            self.assertEqual(attempts(limiter, 5, critical=True), 0)
            self.assertGreater(counter(limiter.namespace, "closed"), 0)
            with self.assertRaises(LimitExceeded) as caught:
                limiter.check("k", 10, 60, "登录", critical=True)
            self.assertGreaterEqual(caught.exception.retry_after, 1)

    def test_redis_dying_in_the_middle_degrades_the_same_way(self):
        with self.env(RATE_LIMIT_CRITICAL_FALLBACK="closed"):
            self.assertEqual(attempts(fixed(BrokenRedis()), 5, critical=True), 0)

    def test_without_redis_configured_nothing_is_degraded(self):
        with mock.patch.dict(os.environ, {"RATE_LIMIT_CRITICAL_FALLBACK": "closed"}):
            os.environ.pop("REDIS_URL", None)
            limiter = fixed()
            self.assertEqual(attempts(limiter, 25, critical=True), 10, "单进程本地开发没有 Redis，不能因为“降级策略”被拒绝")
            self.assertEqual(counter(limiter.namespace, "closed") + counter(limiter.namespace, "local"), 0)

    def test_defaults_depend_on_environment(self):
        with mock.patch.dict(os.environ, {"APP_ENV": "production"}):
            os.environ.pop("RATE_LIMIT_CRITICAL_FALLBACK", None)
            self.assertEqual(rate_limit.critical_fallback_mode(), "strict")
        with mock.patch.dict(os.environ, {"APP_ENV": "development"}):
            os.environ.pop("RATE_LIMIT_CRITICAL_FALLBACK", None)
            self.assertEqual(rate_limit.critical_fallback_mode(), "open")
        with mock.patch.dict(os.environ, {"RATE_LIMIT_CRITICAL_FALLBACK": "nonsense", "APP_ENV": "production"}):
            self.assertEqual(rate_limit.critical_fallback_mode(), "strict")

    def test_login_sms_register_and_reset_are_marked_critical(self):
        import re
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        for path in ("FasdtApi/login.py", "service/phone_verification_service.py"):
            text = (root / path).read_text(encoding="utf-8")
            calls = re.findall(r"require_limit\(\s*(.*?)\n\s*\)", text, re.S)
            self.assertTrue(calls, path)
            for call in calls:
                self.assertIn("critical=True", call, f"{path} 里有没标 critical 的限流：{call[:60]}")


class ConcurrencyDegradationTests(unittest.TestCase):
    def env(self, **values):
        base = {"REDIS_URL": "redis://127.0.0.1:1/0", "APP_ENV": "development"}
        base.update({k: str(v) for k, v in values.items()})
        return mock.patch.dict(os.environ, base)

    def test_closed_stops_accepting_new_tasks(self):
        with self.env(CONCURRENCY_REDIS_DOWN="closed"):
            limiter = concurrency()
            with self.assertRaises(LimitExceeded) as caught:
                limiter.acquire("u", 3, 60, "任务")
            self.assertIn("暂时无法接收新的任务", caught.exception.message)
            self.assertGreater(counter(limiter.namespace, "closed"), 0)

    def test_closed_also_applies_when_redis_dies_mid_flight(self):
        with self.env(CONCURRENCY_REDIS_DOWN="closed"):
            with self.assertRaises(LimitExceeded):
                concurrency(BrokenRedis()).acquire("u", 3, 60, "任务")

    def test_local_keeps_working_per_process(self):
        with self.env(CONCURRENCY_REDIS_DOWN="local"):
            limiter = concurrency()
            leases = [limiter.acquire("u", 3, 60, "任务") for _ in range(3)]
            with self.assertRaises(LimitExceeded):
                limiter.acquire("u", 3, 60, "任务")
            leases[0].release()
            limiter.acquire("u", 3, 60, "任务")
            self.assertGreater(counter(limiter.namespace, "local"), 0)

    def test_without_redis_configured_it_is_just_local(self):
        with mock.patch.dict(os.environ, {"CONCURRENCY_REDIS_DOWN": "closed"}):
            os.environ.pop("REDIS_URL", None)
            limiter = concurrency()
            limiter.acquire("u", 2, 60, "任务")
            limiter.acquire("u", 2, 60, "任务")

    def test_defaults_depend_on_environment(self):
        with mock.patch.dict(os.environ, {"APP_ENV": "production"}):
            os.environ.pop("CONCURRENCY_REDIS_DOWN", None)
            self.assertEqual(rate_limit.concurrency_fallback_mode(), "closed")
        with mock.patch.dict(os.environ, {"APP_ENV": "development"}):
            os.environ.pop("CONCURRENCY_REDIS_DOWN", None)
            self.assertEqual(rate_limit.concurrency_fallback_mode(), "local")


if __name__ == "__main__":
    unittest.main()
