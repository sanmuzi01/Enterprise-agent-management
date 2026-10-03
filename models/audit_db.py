"""审计事件写入专用的独立数据库连接（最小权限账号）。

审计表的防篡改依赖"写审计的账号不能改/删审计记录"：如果审计写入复用应用主账号
（对所有表都有 INSERT/UPDATE/DELETE 权限），一个被攻破的应用进程就能篡改或抹掉
自己写过的操作记录，审计也就形同虚设。这里单独开一个 SQLAlchemy engine/session，
接一个只被授予 `audit_event` 表 INSERT/SELECT 权限的 MySQL 账号（建号脚本见
deploy/mysql-init/02-create-audit-user.sh），供 `service/audit_service.py` 专用。

本地开发/测试/CI 没有单独建这个账号时，AUDIT_DB_USER/AUDIT_DB_PASSWORD 退回主账号
（DB_USER/DB_PASSWORD）——不是新增的硬依赖，只是生产环境应该配更紧的权限（生产环境
是否真的配对了，由 service/config_validation.py 的 assert_runtime_config() 强制
拦截，不靠这里的兜底逻辑本身去分辨"是开发环境故意留空"还是"生产环境忘配了"）。

第五轮审计 P1-4 修的一个真实 bug：docker-compose.prod.yml 给 AUDIT_DB_PASSWORD
写的是 `${AUDIT_DB_PASSWORD:-}`——`.env` 没设时，容器环境变量会被设成空字符串，
不是"完全不设这个变量"。`os.getenv(key, default)` 只有变量真的不存在时才返回
default，变量存在但是空字符串会原样返回空字符串——所以这里不能用两参数版本的
os.getenv，必须先取值再用 `or` 判断"空也算没有"，才能在这种情况下正确退回主账号。
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from models.init_db import (
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_POOL_PRE_PING,
    DB_POOL_RECYCLE,
    DB_PORT,
    DB_USER,
)

AUDIT_DB_USER = os.getenv("AUDIT_DB_USER") or DB_USER
AUDIT_DB_PASSWORD = os.getenv("AUDIT_DB_PASSWORD") or DB_PASSWORD

# 第五轮审计 P1-6：不能用 f-string 直接拼——密码里如果有 @ : / % 之类的字符，
# 会被误解析成主机名/路径的一部分。URL.create() 负责正确的百分号编码，
# render_as_string(hide_password=False) 拿到编码后的完整连接串（这里需要真密码
# 去连库，不是给人看的日志，所以不能用默认的 hide_password=True）。
AUDIT_DATABASE_URL = URL.create(
    "mysql+pymysql", username=AUDIT_DB_USER, password=AUDIT_DB_PASSWORD,
    host=DB_HOST, port=int(DB_PORT), database=DB_NAME,
).render_as_string(hide_password=False)
AUDIT_ASYNC_DATABASE_URL = URL.create(
    "mysql+asyncmy", username=AUDIT_DB_USER, password=AUDIT_DB_PASSWORD,
    host=DB_HOST, port=int(DB_PORT), database=DB_NAME,
).render_as_string(hide_password=False)

# 审计写入量小、只追加，不需要跟主业务连接池一样大。
_AUDIT_POOL_SIZE = 2
_AUDIT_MAX_OVERFLOW = 3

audit_engine = create_engine(
    AUDIT_DATABASE_URL,
    echo=False,
    pool_size=_AUDIT_POOL_SIZE,
    max_overflow=_AUDIT_MAX_OVERFLOW,
    pool_recycle=DB_POOL_RECYCLE,
    pool_pre_ping=DB_POOL_PRE_PING,
    connect_args={"charset": "utf8mb4"},
)
AuditSessionLocal = sessionmaker(bind=audit_engine)

# ASYNC_DB_POOL=null 只给测试用，原因见 models/async_db.py。
if os.getenv("ASYNC_DB_POOL", "queue").lower() == "null":
    from sqlalchemy.pool import NullPool
    _audit_pool_options = {"poolclass": NullPool}
else:
    _audit_pool_options = {"pool_size": _AUDIT_POOL_SIZE, "max_overflow": _AUDIT_MAX_OVERFLOW,
                           "pool_recycle": DB_POOL_RECYCLE, "pool_pre_ping": DB_POOL_PRE_PING}

audit_async_engine = create_async_engine(
    AUDIT_ASYNC_DATABASE_URL,
    echo=False,
    connect_args={"charset": "utf8mb4"},
    **_audit_pool_options,
)
AuditAsyncSessionLocal = async_sessionmaker(
    bind=audit_async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
