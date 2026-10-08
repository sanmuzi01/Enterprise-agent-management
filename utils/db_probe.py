"""数据库连接的超时与探活：保证“连不上”会在几秒内自己结束，而不是让线程一直卡在驱动的 connect() 里。

问题：就绪探针用 `asyncio.wait_for(run_in_threadpool(...), 3s)` 只能让探针“按时返回 503”，取消不了线程池里正在进行的同步 connect()。
驱动默认没有连接超时，数据库网络“半通不通”（端口能连上、握手永远没有回应）时，每次探针留下一个被卡住的线程，
每 10 秒一次的健康检查会逐步耗尽线程池，最后连正常请求也进不来。
所以必须在驱动层限制。注意 `connect_timeout` 只管 TCP 连接本身——服务端接受了连接却不回握手包时（“半通不通”）仍然会无限等下去，
必须再加 `read_timeout`（实测：只设 connect_timeout=2 卡住 >8 秒，加上 read_timeout=2 则 2 秒后报错）：
  · 所有数据库引擎（同步 / 异步 / 审计）都带 connect_timeout（DB_CONNECT_TIMEOUT，默认 5 秒）和一个宽松的 read_timeout
    （DB_READ_TIMEOUT，默认 60 秒，大于 MySQL 默认的 innodb_lock_wait_timeout=50 秒，所以不会误伤正常的锁等待；
    需要跑超长查询的部署自己调大）。它是兜底：把“握手永远没有回应”从无限卡住变成有限等待；
  · 就绪探针不用连接池里的连接（池满了不能把探针也卡住），而是自己开一条短连接，连接 / 读 / 写超时都小于 READY_TIMEOUT_SECONDS，
    线程在超时后自己结束；
  · 测试脚手架探测“数据库能不能用”也走这里，不再无限等。
"""
import os
from typing import Dict


def connect_timeout() -> int:
    """应用连接池里每条连接的建立超时（秒）。"""
    try:
        value = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
        return value if value > 0 else 5
    except ValueError:
        return 5


def read_timeout() -> int:
    """应用连接上每次读（等握手 / 等查询结果）的超时（秒）。"""
    try:
        value = int(os.getenv("DB_READ_TIMEOUT", "60"))
        return value if value > 0 else 60
    except ValueError:
        return 60


def connect_args(async_driver: bool = False) -> Dict[str, object]:
    """所有 SQLAlchemy 引擎共用的驱动参数。pymysql 认 connect / read / write 三个超时，asyncmy 只认 connect / read。"""
    args: Dict[str, object] = {"charset": "utf8mb4", "connect_timeout": connect_timeout(), "read_timeout": read_timeout()}
    if not async_driver:
        args["write_timeout"] = read_timeout()
    return args


def ping_database(timeout: float = 2.0) -> bool:
    """用一条独立的短连接执行 SELECT 1：连接 / 读 / 写都不超过 timeout 秒，之后线程一定会结束。"""
    import pymysql
    seconds = max(1, int(round(timeout)))
    connection = None
    try:
        connection = pymysql.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", "3306") or 3306),
            user=os.getenv("DB_USER", ""), password=os.getenv("DB_PASSWORD", ""), database=os.getenv("DB_NAME") or None,
            connect_timeout=seconds, read_timeout=seconds, write_timeout=seconds, charset="utf8mb4",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass
