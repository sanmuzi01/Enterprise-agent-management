"""补齐审计写入专用最小权限账号的表级授权（docs/enterprise-rbac-plan.md 第21节）。

deploy/mysql-init/02-create-audit-user.sh 只建账号（CREATE USER），不在那一步做
GRANT —— MySQL 的表级 GRANT 要求目标表已经存在，而 mysql-init 在容器第一次启动时跑，
这时 Alembic（agent_sql.audit_event）和 Flyway（enterprise_business.audit_event）
都还没建表，实测过直接 GRANT 会报 `ERROR 1146 (42S02): Table doesn't exist` 并中断
整个 docker-entrypoint-initdb.d 流程。

这个脚本在两边迁移都跑完之后（也就是 `docker compose -f docker-compose.prod.yml up -d`
之后，两个服务都变 healthy）执行一次，把 GRANT 补上。GRANT 本身是幂等的，重复执行
只会重新声明同样的权限，不会报错，可以放心重跑（比如新增部署环境、或者怀疑权限被
手动改过时）。

用法（在项目根目录，用跟容器一致的 .env）：
    .venv/Scripts/python.exe scripts/grant_audit_db_privileges.py
"""
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from models.init_db import DB_NAME, SessionLocal  # noqa: E402

ENTERPRISE_DB_NAME = os.getenv("ENTERPRISE_DB_NAME", "enterprise_business")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def main() -> None:
    audit_user = os.getenv("AUDIT_DB_USER")
    if not audit_user:
        print("未设置 AUDIT_DB_USER，跳过（审计写入会使用主账号，不需要额外授权）")
        return
    if not _SAFE_IDENTIFIER.match(audit_user):
        raise SystemExit(f"AUDIT_DB_USER 含非法字符：{audit_user!r}（只允许字母/数字/下划线）")

    db = SessionLocal()
    try:
        for db_name in (DB_NAME, ENTERPRISE_DB_NAME):
            db.execute(text(
                f"GRANT INSERT, SELECT ON `{db_name}`.audit_event TO '{audit_user}'@'%'"
            ))
            print(f"OK: GRANT INSERT, SELECT ON `{db_name}`.audit_event TO '{audit_user}'@'%'")
        db.execute(text("FLUSH PRIVILEGES"))
        db.commit()
    finally:
        db.close()

    print(f"\n审计专用账号 {audit_user} 授权完成（仅 audit_event 表 INSERT/SELECT）。")


if __name__ == "__main__":
    main()
