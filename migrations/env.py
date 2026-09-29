from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import URL


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

load_dotenv()

# Phase 3A：target_metadata 接上 Base.metadata，autogenerate 才能用。
# 之前担心"导入 models/init_db 会创建连接并执行旧的 create_all/幂等迁移"——实测不会：
# 那个模块只在顶层 create_engine()（懒连接，不会真的建 TCP 连接），create_all()/
# _run_migrations() 都是显式函数调用，不在模块导入路径上。真正的风险点是它要求
# DB_USER/DB_PASSWORD/... 环境变量已经加载，上面的 load_dotenv() 已经先做了这件事。
from models.init_db import Base  # noqa: E402  (必须在 load_dotenv() 之后才导入)

target_metadata = Base.metadata


def _database_url() -> str:
    """从环境变量生成 Alembic 使用的数据库连接串。"""

    required = {
        "DB_USER": os.getenv("DB_USER"),
        "DB_PASSWORD": os.getenv("DB_PASSWORD"),
        "DB_HOST": os.getenv("DB_HOST"),
        "DB_PORT": os.getenv("DB_PORT"),
        "DB_NAME": os.getenv("DB_NAME"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError("缺少数据库环境变量: " + ", ".join(missing))

    # 第五轮审计 P1-6：不能用 f-string 直接拼——密码里如果有 @ : / % 之类的字符，
    # 会被误解析成主机名/路径的一部分。URL.create() 负责正确的百分号编码，
    # render_as_string(hide_password=False) 拿到编码后的完整连接串（这里需要
    # 真密码，不是给人看的日志，所以不能用默认的 hide_password=True）。
    return URL.create(
        "mysql+pymysql",
        username=required["DB_USER"],
        password=required["DB_PASSWORD"],
        host=required["DB_HOST"],
        port=int(required["DB_PORT"]),
        database=required["DB_NAME"],
    ).render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    """离线模式生成 SQL，不主动连接数据库。"""

    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式连接数据库并执行迁移脚本。"""

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
