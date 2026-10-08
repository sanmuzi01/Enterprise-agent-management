"""数据库连接超时：数据库网络“半通不通”（端口能连上、握手永远没有回应）时，连接必须在几秒内自己结束，不能把线程一直卡住。

用一个本机的“黑洞”服务模拟：它接受 TCP 连接，但从不发 MySQL 握手包。
实测（见 utils/db_probe.py）：只设 connect_timeout 会一直卡住，必须再加 read_timeout。
不需要真实 MySQL。"""
import asyncio
import os
import socket
import threading
import time
import unittest
from unittest import mock

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

from service import health
from utils import db_probe


class BlackHole:
    """接受连接但永远不说话。"""

    def __init__(self):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(100)
        self.port = self.server.getsockname()[1]
        self.kept = []
        self.running = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        self.server.settimeout(0.2)
        while self.running:
            try:
                self.kept.append(self.server.accept())
            except OSError:
                continue

    def close(self):
        self.running = False
        for conn, _ in self.kept:
            conn.close()
        self.server.close()


class HalfOpenDatabaseCase(unittest.TestCase):
    def setUp(self):
        self.hole = BlackHole()
        self.addCleanup(self.hole.close)
        patcher = mock.patch.dict(os.environ, {"DB_HOST": "127.0.0.1", "DB_PORT": str(self.hole.port), "DB_USER": "u", "DB_PASSWORD": "p",
                                               "DB_NAME": "n", "READY_TIMEOUT_SECONDS": "3"})
        patcher.start()
        self.addCleanup(patcher.stop)


class PingDatabaseTests(HalfOpenDatabaseCase):
    def test_a_half_open_database_fails_fast_instead_of_hanging(self):
        started = time.perf_counter()
        self.assertFalse(db_probe.ping_database(timeout=2))
        self.assertLess(time.perf_counter() - started, 4.5)

    def test_unreachable_port_fails_fast(self):
        with mock.patch.dict(os.environ, {"DB_PORT": "1"}):
            started = time.perf_counter()
            self.assertFalse(db_probe.ping_database(timeout=2))
            self.assertLess(time.perf_counter() - started, 4.5)


class ReadyProbeTests(HalfOpenDatabaseCase):
    def test_ready_returns_503_in_time_and_leaves_no_stuck_threads(self):
        baseline = threading.active_count()
        for _ in range(6):                          # 模拟每隔几秒一次的健康检查
            started = time.perf_counter()
            result = asyncio.run(health.ready())
            self.assertFalse(result["ok"])
            self.assertLess(time.perf_counter() - started, 3.8, "就绪探针必须在 READY_TIMEOUT_SECONDS 附近返回")
        time.sleep(3.5)                             # 等线程池里的探活线程按驱动超时自己结束
        stuck = threading.active_count() - baseline
        self.assertLessEqual(stuck, 1, f"探针结束后还残留 {stuck} 个线程：数据库连接线程被卡住了")

    def test_probe_timeout_is_shorter_than_the_ready_timeout(self):
        """asyncio.wait_for 取消不了线程里的同步 connect()：驱动超时必须小于探针超时，线程才会在探针返回之前（或几乎同时）结束。"""
        calls = []
        with mock.patch.object(health, "ping_database", side_effect=lambda timeout: calls.append(timeout) or True):
            asyncio.run(health.ready())
        self.assertLess(calls[0], health._timeout_seconds())


class AppEngineTimeoutTests(HalfOpenDatabaseCase):
    def test_pooled_connections_give_up_instead_of_hanging(self):
        """应用连接池用的参数（connect_timeout + read_timeout）对半通不通的数据库有效。"""
        with mock.patch.dict(os.environ, {"DB_CONNECT_TIMEOUT": "2", "DB_READ_TIMEOUT": "2"}):
            engine = create_engine(
                URL.create("mysql+pymysql", username="u", password="p", host="127.0.0.1", port=self.hole.port, database="n"),
                connect_args=db_probe.connect_args(),
            )
        started = time.perf_counter()
        with self.assertRaises(OperationalError):
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        self.assertLess(time.perf_counter() - started, 5)
        engine.dispose()

    def test_async_connections_also_give_up(self):
        from sqlalchemy.ext.asyncio import create_async_engine
        with mock.patch.dict(os.environ, {"DB_CONNECT_TIMEOUT": "2", "DB_READ_TIMEOUT": "2"}):
            engine = create_async_engine(
                URL.create("mysql+asyncmy", username="u", password="p", host="127.0.0.1", port=self.hole.port, database="n"),
                connect_args=db_probe.connect_args(async_driver=True),
            )

        async def attempt():
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))

        async def scenario():
            started = time.perf_counter()
            with self.assertRaises(Exception):
                await asyncio.wait_for(attempt(), timeout=8)
            return time.perf_counter() - started
        elapsed = asyncio.run(scenario())
        asyncio.run(engine.dispose())
        self.assertLess(elapsed, 6, "异步连接在驱动超时后应当自己失败，而不是等到外层 8 秒超时")


class EngineWiringTests(unittest.TestCase):
    """项目里真正使用的四个引擎都带着超时参数（不连数据库，只看建连接时传给驱动的参数）。"""

    def captured_args(self, sync_engine):
        captured = {}

        def fake_connect(*args, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop")
        with mock.patch.object(sync_engine.dialect, "connect", side_effect=fake_connect):
            with self.assertRaises(RuntimeError):
                sync_engine.pool._creator()
        return captured

    def test_sync_engines_have_connect_read_and_write_timeouts(self):
        from models import audit_db, init_db
        for engine in (init_db.engine, audit_db.audit_engine):
            args = self.captured_args(engine)
            self.assertEqual(args["connect_timeout"], db_probe.connect_timeout())
            self.assertEqual(args["read_timeout"], db_probe.read_timeout())
            self.assertEqual(args["write_timeout"], db_probe.read_timeout())
            self.assertEqual(args["charset"], "utf8mb4")

    def test_async_engines_have_connect_and_read_timeouts(self):
        from models import async_db, audit_db
        for engine in (async_db.async_engine, audit_db.audit_async_engine):
            args = self.captured_args(engine.sync_engine)
            self.assertEqual(args["connect_timeout"], db_probe.connect_timeout())
            self.assertEqual(args["read_timeout"], db_probe.read_timeout())
            self.assertEqual(args["charset"], "utf8mb4")

    def test_defaults_and_bad_values(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DB_CONNECT_TIMEOUT", None)
            os.environ.pop("DB_READ_TIMEOUT", None)
            self.assertEqual((db_probe.connect_timeout(), db_probe.read_timeout()), (5, 60))
        for bad in ("abc", "0", "-3", ""):
            with mock.patch.dict(os.environ, {"DB_CONNECT_TIMEOUT": bad, "DB_READ_TIMEOUT": bad}):
                self.assertEqual((db_probe.connect_timeout(), db_probe.read_timeout()), (5, 60))

    def test_read_timeout_is_longer_than_mysql_lock_wait_timeout(self):
        """read_timeout 是兜底，不能比 MySQL 默认的 innodb_lock_wait_timeout（50 秒）短，否则会把正常的锁等待误判成故障。"""
        os.environ.pop("DB_READ_TIMEOUT", None)
        self.assertGreater(db_probe.read_timeout(), 50)

    def test_route_test_scaffold_probe_is_bounded(self):
        from tests import _route_client as rc
        hole = BlackHole()
        self.addCleanup(hole.close)
        with mock.patch.dict(os.environ, {"DB_HOST": "127.0.0.1", "DB_PORT": str(hole.port), "DB_USER": "u", "DB_PASSWORD": "p", "DB_NAME": "n"}):
            started = time.perf_counter()
            available, reason = rc._probe_route_environment()
        self.assertFalse(available)
        self.assertIn("没有正常响应", reason)
        self.assertLess(time.perf_counter() - started, 5)


if __name__ == "__main__":
    unittest.main()
