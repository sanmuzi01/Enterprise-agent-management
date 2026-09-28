#!/bin/bash
# 只在 db 容器第一次初始化（数据卷为空）时执行一次。建审计写入专用的最小权限账号——
# 只给 audit_event 表 INSERT/SELECT，没有 UPDATE/DELETE，防止一个被攻破的应用进程
# 篡改或抹掉自己写过的审计记录（agent_sql.audit_event 是 FastAPI 侧，
# enterprise_business.audit_event 是 Java enterprise-business-hub 侧，各自独立表）。
# 见 docs/enterprise-rbac-plan.md 第21节。
#
# 这里只建账号，不 GRANT：MySQL 的表级 GRANT 要求目标表已经存在（实测过，不存在会
# 直接报 ERROR 1146 并中断整个 docker-entrypoint-initdb.d 流程），而这一步跑在容器
# 第一次启动时，Alembic（agent_sql.audit_event）和 Flyway
# （enterprise_business.audit_event）此时都还没建表。GRANT 挪到
# scripts/grant_audit_db_privileges.py，部署时在两边迁移都跑完之后单独执行一次，
# 见 docs/deployment.md。
#
# 老部署（db 容器早于这个脚本加入时就已经初始化过）不会自动重跑，需要手动执行一次：
#   docker compose -f docker-compose.prod.yml exec db mysql -uroot -p -e \
#     "CREATE USER IF NOT EXISTS 'audit_writer'@'%' IDENTIFIED BY '<AUDIT_DB_PASSWORD>';"
set -euo pipefail

AUDIT_DB_USER="${AUDIT_DB_USER:-audit_writer}"
if [ -z "${AUDIT_DB_PASSWORD:-}" ]; then
  echo "AUDIT_DB_PASSWORD 未设置，跳过创建审计专用账号（审计写入会退回主账号，见 .env.production.example）"
  exit 0
fi

mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" <<-SQL
  CREATE USER IF NOT EXISTS '${AUDIT_DB_USER}'@'%' IDENTIFIED BY '${AUDIT_DB_PASSWORD}';
SQL

echo "OK：审计专用账号 ${AUDIT_DB_USER} 已建好（还没授权，见 scripts/grant_audit_db_privileges.py）"
