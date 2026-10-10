"""异步数据库入口。

面向上线和分布式部署时，路由层应该使用明确的异步数据库会话。
如果异步 MySQL 驱动缺失，应用启动阶段就应该暴露配置问题，而不是在
每个路由里写一遍“异步失败后走同步”的兼容分支。
"""

import importlib.util
import os
from typing import AsyncGenerator

from utils.db_probe import connect_args
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from models.init_db import (
    DB_HOST,
    DB_MAX_OVERFLOW,
    DB_NAME,
    DB_PASSWORD,
    DB_POOL_PRE_PING,
    DB_POOL_RECYCLE,
    DB_POOL_SIZE,
    DB_POOL_TIMEOUT,
    DB_PORT,
    DB_USER,
)


ASYNC_DB_DRIVER = "asyncmy"
# 第五轮审计 P1-6：不能用 f-string 直接拼——密码里如果有 @ : / % 之类的字符，
# 会被误解析成主机名/路径的一部分。URL.create() 负责正确的百分号编码，
# render_as_string(hide_password=False) 拿到编码后的完整连接串（这里需要真密码
# 去连库，不是给人看的日志，所以不能用默认的 hide_password=True）。
ASYNC_DATABASE_URL = URL.create(
    f"mysql+{ASYNC_DB_DRIVER}", username=DB_USER, password=DB_PASSWORD,
    host=DB_HOST, port=int(DB_PORT), database=DB_NAME,
).render_as_string(hide_password=False)


def async_database_available() -> bool:
    """当前环境是否安装了异步 MySQL 驱动。"""

    return importlib.util.find_spec(ASYNC_DB_DRIVER) is not None


if not async_database_available():
    raise RuntimeError(f"缺少异步数据库驱动: {ASYNC_DB_DRIVER}")

# ASYNC_DB_POOL=null：每个会话用完就关闭连接，不跨事件循环复用。只给测试用——测试里每次
# asyncio.run/TestClient 请求都是一个新的事件循环，进程级连接池里的连接会绑在已经关掉的循环上，
# 之后被垃圾回收时才去关闭，报 'NoneType' object has no attribute 'send'。生产保持默认的连接池。
if os.getenv("ASYNC_DB_POOL", "queue").lower() == "null":
    _pool_options = {"poolclass": NullPool}
else:
    _pool_options = {
        "pool_size": DB_POOL_SIZE, "max_overflow": DB_MAX_OVERFLOW, "pool_timeout": DB_POOL_TIMEOUT,
        "pool_recycle": DB_POOL_RECYCLE, "pool_pre_ping": DB_POOL_PRE_PING, "pool_use_lifo": True,
    }

async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=False,
    connect_args=connect_args(async_driver=True),      # 含 connect_timeout：数据库“半通不通”时连接线程不会无限卡住（见 utils/db_probe.py）
    **_pool_options,
)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """返回异步数据库会话。"""

    async with AsyncSessionLocal() as session:
        yield session
