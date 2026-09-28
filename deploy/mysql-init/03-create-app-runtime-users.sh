#!/bin/bash
# 只在 db 容器第一次初始化（数据卷为空）时执行一次。建业务服务运行时专用的
# 最小权限账号——只给 SELECT/INSERT/UPDATE/DELETE，没有 CREATE/ALTER/DROP/
# GRANT，防止一个被攻破的应用进程（api/worker/enterprise-hub）能改表结构、
# 建新账号或者做任何超出正常业务读写范围的事。见 docs/enterprise-rbac-plan.md
# "审计账号最小权限"那一节相关记录——这次补的是"业务账号"那一半，之前只做了
# "审计账号"那一半，主应用和 Java 服务一直还在用 root。
#
# 跟 02-create-audit-user.sh 不一样：这里的 GRANT 是 `ON db.*`（整库通配），
# 不是某一张具体的表，MySQL 的库级通配授权不要求任何表已经存在（实测过），
# 可以放心跟 CREATE USER 放在同一步，不用像审计账号那样拆成"建号"和"补授权"
# 两个阶段。
#
# APP_DB_USER/APP_DB_PASSWORD 建的是 Python 侧（agent_sql 库）运行时账号，
# JAVA_APP_DB_USER/JAVA_APP_DB_PASSWORD 建的是 Java 侧（enterprise_business 库）
# 的——两边分开建，一个服务被攻破不会连带另一个服务的数据库权限也丢了。
set -euo pipefail

: "${APP_DB_USER:=app_runtime}"
: "${JAVA_APP_DB_USER:=app_runtime_java}"

if [ -z "${APP_DB_PASSWORD:-}" ] && [ -z "${JAVA_APP_DB_PASSWORD:-}" ]; then
  echo "APP_DB_PASSWORD/JAVA_APP_DB_PASSWORD 都未设置，跳过创建业务运行时账号（会退回 root，见 .env.production.example）"
  exit 0
fi

if [ -n "${APP_DB_PASSWORD:-}" ]; then
  mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" <<-SQL
    CREATE USER IF NOT EXISTS '${APP_DB_USER}'@'%' IDENTIFIED BY '${APP_DB_PASSWORD}';
    GRANT SELECT, INSERT, UPDATE, DELETE ON \`${MYSQL_DATABASE}\`.* TO '${APP_DB_USER}'@'%';
SQL
  echo "OK：业务运行时账号 ${APP_DB_USER} 已就绪（仅 ${MYSQL_DATABASE} 库 SELECT/INSERT/UPDATE/DELETE，无 DDL）"
fi

if [ -n "${JAVA_APP_DB_PASSWORD:-}" ]; then
  mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" <<-SQL
    CREATE USER IF NOT EXISTS '${JAVA_APP_DB_USER}'@'%' IDENTIFIED BY '${JAVA_APP_DB_PASSWORD}';
    GRANT SELECT, INSERT, UPDATE, DELETE ON \`enterprise_business\`.* TO '${JAVA_APP_DB_USER}'@'%';
SQL
  echo "OK：Java 业务运行时账号 ${JAVA_APP_DB_USER} 已就绪（仅 enterprise_business 库 SELECT/INSERT/UPDATE/DELETE，无 DDL）"
fi

mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "FLUSH PRIVILEGES;"
