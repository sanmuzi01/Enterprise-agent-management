"""在一个全新的空库上跑测试——和 CI 一样的起点，推送前在本地就能发现“依赖开发库里现成数据”的问题。

为什么要有它：CI 的 backend 从 fec74c9 起失败了好几轮才被发现——用户管理列表在库里没有企业记录时直接报错，
本地开发库恰好有企业记录，跑多少遍都是绿的。这类问题只有在空库上才会暴露。

做的事（和 .github/workflows/ci.yml 的 backend 任务一致）：
  1. 在 .env 配置的 MySQL 上建一个临时库（agent_sql_fresh_<时间戳>）；
  2. alembic upgrade head 建表，再跑两次 bootstrap_database 验证幂等；
  3. 把 DB_NAME 指向临时库，跑测试；
  4. 不管成功失败都删掉临时库（--keep 保留，方便排查）。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/test_fresh_db.py              # 跑和“空库”最相关的一组测试（约 1 分钟）
    .venv/Scripts/python.exe scripts/test_fresh_db.py --all        # 全量（和 CI 一样，十几分钟）
    .venv/Scripts/python.exe scripts/test_fresh_db.py tests.test_enterprise_bootstrap tests.test_admin_pagination
需要 .env 里的数据库账号有建库 / 删库权限（本地开发库一般是 root）。
"""
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

# 和空库关系最大的一组：后台各个列表、企业初始化、登录、部门助手、数据体检与保留
DEFAULT_MODULES = [
    "tests.test_no_enterprise_yet",
    "tests.test_enterprise_bootstrap",
    "tests.test_admin_pagination",
    "tests.test_enterprise",
    "tests.test_auth_default_org_enrollment",
    "tests.test_session_cookie",
    "tests.test_session_renewal",
    "tests.test_department_agent_service",
    "tests.test_data_health",
    "tests.test_data_retention",
]


def _server_connection():
    import pymysql
    return pymysql.connect(host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]), user=os.environ["DB_USER"],
                           password=os.environ["DB_PASSWORD"], charset="utf8mb4", autocommit=True)


# 空库的自增 id 从 1 开始，而有些磁盘文件按 id 命名、和开发库共用一个目录（prompt/prompts/<助手id>.yaml、
# skills/enterprise/agent_<助手id>.yml、向量库集合……）：测试建的 1 号助手会覆盖、清理时再删掉开发库 1 号助手的文件。
# 实际踩过：prompt/prompts/1.yaml、5.yaml 被删（已从 git 恢复）。所以建表后把所有自增起点抬到开发库用不到的范围。
ID_BASE = 900_000_000


def _raise_auto_increment(conn, db_name) -> int:
    with conn.cursor() as cur:
        # 按列判断（information_schema.TABLES.AUTO_INCREMENT 在 MySQL 8 上有统计缓存，空表可能是 NULL）
        cur.execute("SELECT DISTINCT TABLE_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND EXTRA LIKE '%%auto_increment%%'", (db_name,))
        tables = [r[0] for r in cur.fetchall()]
        for table in tables:
            cur.execute(f"ALTER TABLE `{db_name}`.`{table}` AUTO_INCREMENT = {ID_BASE}")
    return len(tables)


def _run(cmd, env, label):
    print(f"\n=== {label} ===", flush=True)
    return subprocess.run(cmd, cwd=ROOT, env=env).returncode


def main(argv) -> int:
    load_dotenv(ROOT / ".env")
    keep = "--keep" in argv
    run_all = "--all" in argv
    modules = [a for a in argv if not a.startswith("--")] or DEFAULT_MODULES
    db_name = f"agent_sql_fresh_{time.strftime('%Y%m%d_%H%M%S')}"

    conn = _server_connection()
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    print(f"临时空库：{db_name}")

    env = {**os.environ, "DB_NAME": db_name, "APP_ENV": "test", "PYTHONIOENCODING": "utf-8"}
    python = sys.executable
    try:
        if _run([python, "-m", "alembic", "upgrade", "head"], env, "alembic upgrade head（空库建表）") != 0:
            print("\n[失败] alembic upgrade head")
            return 1
        print(f"自增起点抬到 {ID_BASE}：{_raise_auto_increment(conn, db_name)} 张表（避免和开发库按 id 命名的文件撞车）")
        steps = [
            ([python, "-c", "from models.init_db import bootstrap_database; bootstrap_database(force=True)"], "启动初始化"),
            ([python, "-c", "from models.init_db import bootstrap_database; bootstrap_database(force=True)"], "再跑一次启动初始化（幂等）"),
        ]
        for cmd, label in steps:
            if _run(cmd, env, label) != 0:
                print(f"\n[失败] {label}")
                return 1
        tests = ([python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"] if run_all
                 else [python, "-m", "unittest", *modules])
        code = _run(tests, env, "全量测试" if run_all else f"测试：{', '.join(m.split('.')[-1] for m in modules)}")
        print("\n[通过] 空库上的测试全部通过" if code == 0 else "\n[失败] 空库上有测试没通过（开发库上通过、这里失败，多半是依赖了开发库里的现成数据）")
        return code
    finally:
        if keep:
            print(f"保留临时库 {db_name}（--keep），排查完记得手动删除")
        else:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
            print(f"已删除临时库 {db_name}")
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
