"""回归：操作日志不能在事件循环线程上同步写库。

每个请求都会更新 user.last_seen_at（行排他锁），operation_log 写入要做外键检查（同一行共享锁）。
日志写入如果在事件循环线程上同步执行，会堵住"已执行 UPDATE、还没来得及发 COMMIT"的并发请求，
两边互等到 innodb_lock_wait_timeout（约 50 秒）——真实环境里同一用户闲置后打开部门工作台
（十余个并发请求）就会整页卡死近一分钟。该时序在进程内很难稳定复现，所以直接断言不变式。
"""
import asyncio
import unittest
from unittest.mock import patch

from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()


def _on_event_loop_thread() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


@unittest.skipUnless(_AVAILABLE, _WHY)
class OperationLogOffloadTest(unittest.TestCase):
    def test_log_write_runs_off_the_event_loop(self):
        calls = []
        client = rc.make_client()
        with patch("service.operation_log_middleware.create_operation_log",
                   side_effect=lambda **kw: calls.append(_on_event_loop_thread())):
            response = client.get("/agent/selected/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(calls, [False])


if __name__ == "__main__":
    unittest.main()
