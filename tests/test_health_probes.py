"""存活 / 就绪探针：/live 永远轻量，/ready 和 /health 在依赖故障时返回 503（以前不管数据库 / Redis 是否可用都返回 200，
Docker 里容器永远是 healthy，负载均衡还会继续往坏实例转发）。

走真实的应用（TestClient）；数据库 / Redis 的故障用打桩模拟，所以不需要真的把它们停掉。需要本机 MySQL 的只有“数据库正常时返回 200”那一条。"""
import asyncio
import os
import re
import pathlib
import unittest
from unittest import mock

from tests import _route_client as rc

from service import health

_AVAILABLE, _WHY = rc.route_tests_available()
ROOT = pathlib.Path(__file__).resolve().parent.parent


class ReadyLogicTests(unittest.TestCase):
    """不经过 HTTP：就绪的判定规则。"""

    def run_ready(self, *, database, redis_configured=False, redis_up=True, require_redis=False):
        env = {"READY_REQUIRE_REDIS": "1" if require_redis else "0"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(health, "_check_database", return_value=database), \
                mock.patch.object(health, "redis_configured", return_value=redis_configured), \
                mock.patch.object(health._redis, "is_available", return_value=redis_up):
            return asyncio.run(health.ready())

    def test_database_down_is_not_ready(self):
        self.assertEqual(self.run_ready(database=False), {"ok": False, "degraded": False})

    def test_everything_up_is_ready(self):
        self.assertEqual(self.run_ready(database=True, redis_configured=True, redis_up=True), {"ok": True, "degraded": False})

    def test_no_redis_configured_is_fine(self):
        self.assertEqual(self.run_ready(database=True, redis_configured=False), {"ok": True, "degraded": False})

    def test_redis_down_is_degraded_but_still_ready_by_default(self):
        """Redis 抖一下不能让所有实例同时被摘掉：降级（有指标和告警），限流按策略收紧。"""
        self.assertEqual(self.run_ready(database=True, redis_configured=True, redis_up=False), {"ok": True, "degraded": True})

    def test_redis_down_is_not_ready_when_required(self):
        self.assertEqual(self.run_ready(database=True, redis_configured=True, redis_up=False, require_redis=True), {"ok": False, "degraded": True})

    def test_a_hanging_database_check_times_out_instead_of_hanging_the_probe(self):
        import time

        def hang():
            time.sleep(2)
            return True
        with mock.patch.dict(os.environ, {"READY_TIMEOUT_SECONDS": "0.5"}), \
                mock.patch.object(health, "_check_database", side_effect=hang), \
                mock.patch.object(health, "redis_configured", return_value=False):
            started = time.perf_counter()
            result = asyncio.run(health.ready())
        self.assertFalse(result["ok"])
        self.assertLess(time.perf_counter() - started, 1.9)

    def test_live_never_looks_at_dependencies(self):
        with mock.patch.object(health, "_check_database", side_effect=AssertionError("live 不能查数据库")):
            self.assertEqual(health.live(), {"ok": True})

    def test_probe_responses_carry_no_internal_details(self):
        result = self.run_ready(database=False, redis_configured=True, redis_up=False)
        self.assertEqual(set(result), {"ok", "degraded"})


class ProbeEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()

    def get(self, path, **ready_kwargs):
        defaults = dict(database=True, redis_configured=False, redis_up=True)
        defaults.update(ready_kwargs)
        with mock.patch.object(health, "_check_database", return_value=defaults["database"]), \
                mock.patch.object(health, "redis_configured", return_value=defaults["redis_configured"]), \
                mock.patch.object(health._redis, "is_available", return_value=defaults["redis_up"]):
            return self.client.get(path)

    def test_live_is_200_even_when_the_database_is_down(self):
        response = self.get("/live", database=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_ready_and_health_are_503_when_the_database_is_down(self):
        for path in ("/ready", "/health"):
            response = self.get(path, database=False)
            self.assertEqual(response.status_code, 503, path)
            self.assertEqual(response.json()["ok"], False)

    def test_ready_and_health_are_200_when_healthy(self):
        for path in ("/ready", "/health"):
            response = self.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.json(), {"ok": True, "degraded": False})

    def test_redis_outage_shows_up_as_degraded(self):
        response = self.get("/ready", redis_configured=True, redis_up=False)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["degraded"])

    @unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
    def test_real_database_is_ready(self):
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200, response.text)


class ProbeWiringTests(unittest.TestCase):
    """部署文件里用的是对的探针。"""

    def test_docker_healthchecks_use_ready(self):
        for name in ("docker-compose.yml", "docker-compose.prod.yml"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("http://127.0.0.1:8000/ready", text, name)
            self.assertNotIn("http://127.0.0.1:8000/health", text, name)

    def test_nginx_proxies_the_probes(self):
        for name in ("deploy/nginx.conf", "agent-platform-https.conf"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for path in ("/live", "/ready", "/health"):
                self.assertTrue(re.search(rf"location = {path} \{{\s+proxy_pass http://127.0.0.1:8000{path};", text), f"{name} 没有透传 {path}")

    def test_probes_are_not_traced_or_operation_logged(self):
        from service import operation_log_middleware, request_context_middleware
        for path in ("/live", "/ready", "/health"):
            self.assertIn(path, operation_log_middleware.SKIP_PATHS)
            self.assertIn(path, request_context_middleware._UNTRACED_PATHS)


if __name__ == "__main__":
    unittest.main()
