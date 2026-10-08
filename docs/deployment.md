# Production Deployment

生产环境按进程部署，用进程管理器（systemd / supervisor / nssm 等）常驻：

- `api`: FastAPI 对外接口 —— `uvicorn FasdtApi.main:app --host 0.0.0.0 --port 8000 --workers 2`
- `worker`: 后台任务 Worker（知识库入库 / 重建索引、自定义组件定时调度）—— `python -m service.background_worker`
- 前端：`npm run frontend:build` 产出 `frontend/dist`，交给 Nginx 做静态托管 + `/api` 反向代理
- `mysql`: 主数据库（自建或云 RDS）
- `redis`: 分布式缓存、限流和并发控制（可选，不配则回退进程内内存）
- `chroma`: 向量库 Server（`pip install chromadb` 自带的 `chroma run` 命令，或用官方镜像单独跑）。
  `api` 用 `--workers 2` 起了多个进程，加上独立的 `worker` 进程，至少 3 个进程会同时碰向量库；
  ChromaDB 内嵌的 `PersistentClient` 不保证多进程并发读写安全，生产必须设置
  `CHROMA_SERVER_HOST`/`CHROMA_SERVER_PORT` 走 Server 模式，不要只填 `VECTOR_DB_PATH`。

  ```bash
  chroma run --path /data/chroma_db --port 8000    # 用 systemd/supervisor 常驻
  ```

## 1. 准备配置

复制生产模板并填写真实密钥。**两份模板按部署方式选一份**（变量一一对应，只有主机地址不同：容器里 `127.0.0.1` 指的是容器自己，所以 Docker 部署要用服务名 `db` / `redis` / `enterprise-hub`）：

```powershell
# 直接在服务器上跑（systemd / 进程）：
Copy-Item .env.production.example .env
# docker-compose.prod.yml 部署：
Copy-Item .env.production.docker.example .env
```

必须修改：

- `DB_PASSWORD`
- `JWT_SECRET_KEY`
- `LLM_ENCRYPTION_KEY`
- `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`（使用阿里云短信认证时）
- `SMS_ALIYUN_SIGN_NAME` / `SMS_ALIYUN_TEMPLATE_CODE`（使用阿里云短信认证时）
- `TRUSTED_HOSTS`
- `CORS_ALLOW_ORIGINS`

`LLM_ENCRYPTION_KEY` 需要是 Fernet key，可用下面命令生成：

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

个人账号可使用阿里云号码认证服务的短信认证。选用控制台提供的系统签名和模板，
使用只授予 `dypns:SendSmsVerifyCode` 的 RAM 用户密钥；项目生成并校验验证码：

```env
SMS_PROVIDER=aliyun
ALIBABA_CLOUD_ACCESS_KEY_ID=change-me-ram-access-key-id
ALIBABA_CLOUD_ACCESS_KEY_SECRET=change-me-ram-access-key-secret
SMS_ALIYUN_SIGN_NAME=change-me-system-sign-name
SMS_ALIYUN_TEMPLATE_CODE=change-me-system-template-code
```

已有自建短信网关也可使用通用 Webhook：

```env
SMS_PROVIDER=webhook
SMS_WEBHOOK_URL=https://your-sms-gateway.example.com/send
SMS_WEBHOOK_TOKEN=change-me-sms-webhook-token
```

接口会向 `SMS_WEBHOOK_URL` 发送 `phone`、`code`、`ttl_seconds`、`scene` 四个字段。`SMS_PROVIDER=console` 只适合本地开发，验证码会写入后端日志；只有显式配置 `SMS_EXPOSE_DEV_CODE=1` 时才会把验证码返回给前端，不要在生产环境开启。

**短信频率限制**（默认值都在 `.env.production.example` 里，填 0 表示关闭该项）：

| 限制 | 默认 | 防什么 |
|---|---|---|
| 同一手机号 | 60 秒 1 条、每天 10 条 | 短信轰炸某个号码 |
| 同一 IP | 每小时 20 条（注册发码、找回密码的"查号码"步骤同样计入，不发短信也算） | 单个来源脚本刷接口 |
| 全站总量 | 每小时 200 条、每天 1000 条（`SMS_CODE_GLOBAL_*`） | 换号码换 IP 分散刷，给短信费用封顶 |
| 校验验证码 | 同一手机号 10 分钟内最多校验 10 次；单个验证码错 5 次即作废 | 暴力猜 6 位验证码 |
| 提交重置密码 | 同一 IP 每小时 10 次 | 批量尝试重置别人的密码 |

超限返回 429 并带 `Retry-After`。全站总量按你的用户规模调整：设得太低，被攻击时正常用户也会暂时收不到验证码；设得太高，起不到封顶费用的作用。

**按 IP 限流的前提：后端要拿到用户的真实 IP。** 登录、注册、短信的"按 IP"限制都用请求的来源地址。经过 nginx 转发后，
后端直接看到的是 nginx / Docker 网关的地址，如果不处理，所有用户会被当成同一个 IP，共用一份额度（正常用户被 429）。
`Dockerfile` 里已经设置了 `FORWARDED_ALLOW_IPS`（只信任本机和内网网段）：从这些地址连进来的请求，后端才采信
`X-Forwarded-For`，并取"从右往左第一个不在内网里的地址"——nginx 追加的真实 IP 在最右边，用户自己伪造的在左边，取不到。
nginx 保持 `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` 不用改。**不要把它改成 `*`**，那样会取最左边，用户能伪造。

上线后确认（在服务器上，先用手机流量随便登录一次，再看限流记录里的 IP 是不是你的公网 IP，而不是 `172.x.x.x`）：

```bash
docker compose -f docker-compose.prod.yml exec redis redis-cli --scan --pattern 'limit:rate:login:ip:*'
```

如果没有用 Docker、后端和 nginx 在同一台机器上直接跑，默认只信任 `127.0.0.1`，同样可用；nginx 在另一台机器时，把那台的地址加进
`FORWARDED_ALLOW_IPS` 环境变量。

## 2. 启动

全新数据库先执行项目初始化入口（建表、补齐字段、创建管理员）：

```powershell
python -m models.init_db
```

Phase 3A（[docs/db-migration-plan.md](db-migration-plan.md)）之后，`migrations/versions/`
下现在只有一个能从空库独立把整套 schema 建完整的可信基线，`alembic upgrade head` 在
全新空库上可以直接跑（CI 里每次都会真的这样跑一遍验证，见 `.github/workflows/ci.yml`）。
`python -m models.init_db` 仍然是默认入口，行为不变；两条路径产出的结构已经验证过
完全一致，选哪条都行。真正想让 Alembic 独占表结构管理（比如接入一套单独的迁移审批
流程）时，设 `DB_AUTO_BOOTSTRAP=0` 关掉自动建表，只用 `alembic upgrade head`。

常驻两个进程（交给 systemd / supervisor / nssm）：

```text
uvicorn FasdtApi.main:app --host 0.0.0.0 --port 8000 --workers 2
python -m service.background_worker
```

前端构建产物由 Nginx 托管，`/api` 反代到 `127.0.0.1:8000`，`/health`、`/metrics` 同样透传。
仓库 `deploy/` 下有可直接改用的模板（均按「与 API 同机」预设，非容器）：

- `deploy/nginx.conf` —— 静态托管 + `/api`、`/health`、`/metrics` 反代（把 `root` 指向 `frontend/dist`）
- `deploy/prometheus.yml` + `deploy/prometheus-rules.yml` —— 抓 `127.0.0.1:8000/metrics`
- `deploy/grafana/provisioning/` —— Grafana 数据源与预置面板

### 2.1 审计写入最小权限账号（可选，推荐生产开启）

审计表（`audit_event`，FastAPI 侧 `agent_sql` 库和 Java `enterprise-business-hub`
侧 `enterprise_business` 库各一张）默认跟主业务共用同一个数据库账号写入——能写就能
改/删，不是真正的防篡改。设置 `.env` 里的 `AUDIT_DB_USER`/`AUDIT_DB_PASSWORD`
（`.env.production.example` 已给默认用户名 `audit_writer`）后，审计写入会改走一个
单独的账号，只授予 `audit_event` 表 `INSERT`/`SELECT`，没有 `UPDATE`/`DELETE`。

**Docker 部署**：`deploy/mysql-init/02-create-audit-user.sh` 在 `db` 容器第一次
初始化时自动建号（只建号，不授权——MySQL 的表级 GRANT 要求表已存在，这时
Alembic/Flyway 都还没建表）。两边迁移都跑完、`docker compose ... up -d` 之后，
补跑一次授权（幂等，重复执行安全）：

```bash
docker compose -f docker-compose.prod.yml exec api python scripts/grant_audit_db_privileges.py
```

**非 Docker 部署**（自己管理 MySQL）：手动建号 + 让两张 `audit_event` 表都建好后，
在项目根目录跑同一个脚本（读本机 `.env`）：

```powershell
mysql -uroot -p -e "CREATE USER 'audit_writer'@'%' IDENTIFIED BY '<AUDIT_DB_PASSWORD>';"
python scripts/grant_audit_db_privileges.py
```

老部署新增这个账号：先在 `.env` 补上 `AUDIT_DB_USER`/`AUDIT_DB_PASSWORD`，手动建号
（Docker 部署下 mysql-init 脚本不会对已初始化过的数据卷重跑），再跑上面的授权脚本，
最后重启 `api`/`worker`（Docker 部署再加 `enterprise-hub`）让新环境变量生效。

### 2.2 业务运行时账号（强烈建议生产开启，`docker-compose.prod.yml` 已默认这样配）

主应用（api/worker）和 Java 企业业务中心（enterprise-hub）之前处理真实业务请求
时用的是 MySQL `root`——一个被攻破的、只该做增删改查的应用进程理论上能改表结构、
建新账号、拿到 `mysql.user` 之类的系统表，权限范围跟它实际需要的完全不对等。

**方案**：业务运行时账号（`DB_USER`/`DB_PASSWORD`，Java 侧
`ENTERPRISE_DB_USER`/`ENTERPRISE_DB_PASSWORD`）只授予对应库的
`SELECT`/`INSERT`/`UPDATE`/`DELETE`，没有 `CREATE`/`ALTER`/`DROP`/`GRANT`；
真正需要建表权限的操作（Python 的 `alembic upgrade head`、Java 的 Flyway
自动迁移）单独用 `MYSQL_ROOT_PASSWORD`——这是一个新的、独立的密码，只给
"建账号/跑迁移"这类管理操作用，业务进程处理请求那条路径完全不会碰到它。

**Docker 部署**：`deploy/mysql-init/03-create-app-runtime-users.sh` 在 `db` 容器
第一次初始化时自动建号（这里的 GRANT 是整库通配 `ON db.*`，不要求表已存在，
不像审计账号那样要拆成两步）。跟着 `docker-compose.prod.yml` 顶部的启动步骤走：
迁移那一步显式覆盖成 `root`（`docker compose run --rm -e DB_USER=root -e
DB_PASSWORD="$MYSQL_ROOT_PASSWORD" api python -m alembic upgrade head`），
`.env` 里正常配置的 `DB_USER`/`DB_PASSWORD`（业务运行时账号）只用来跑
`api`/`worker`。Java 侧的 Flyway 迁移（第五轮审计 P1-3 之后）不再由长驻的
`enterprise-hub` 容器自己在启动时跑——那样 `FLYWAY_DB_USER`/`FLYWAY_DB_PASSWORD`/
`MYSQL_ROOT_PASSWORD` 就得放进一个处理真实业务请求的进程的环境变量里，被攻破
能读到 root 密码。改成单独一个一次性的 `enterprise-hub-migrate` 容器，只跑
Flyway、跑完就退出（`spring.main.web-application-type=none`，本机验证过：
真实 MySQL + 真实迁移，跑完进程正常退出，退出码 0）；`docker compose up -d`
会等这个容器成功退出（`depends_on: condition: service_completed_successfully`）
才起长驻的 `enterprise-hub`，长驻服务的 `SPRING_FLYWAY_ENABLED=false`，环境变量
里完全不出现 root 密码，处理业务请求走的还是 `ENTERPRISE_DB_USER`/
`ENTERPRISE_DB_PASSWORD`。

**必须同时设置 `DB_AUTO_BOOTSTRAP=0`**：不然 `api`/`worker` 进程内的自动建表/
幂等迁移（`models/init_db.py::bootstrap_database`）还是会尝试用业务运行时账号
做 DDL，权限不够会直接启动失败——表结构必须完全交给 `alembic upgrade head`
管，这也是为什么运行时账号可以放心收紧成没有 DDL 权限。

**非 Docker 部署**：自己建两个账号（一个给 Python，一个给 Java），SQL 参考
`deploy/mysql-init/03-create-app-runtime-users.sh`，`GRANT` 目标库分别是
`agent_sql`/`enterprise_business`。

**老部署升级**：先在 `.env` 补上 `MYSQL_ROOT_PASSWORD`（务必设成跟现有
`DB_PASSWORD` 不同的新密码），把 `DB_USER`/`DB_PASSWORD`、
`ENTERPRISE_DB_USER`/`ENTERPRISE_DB_PASSWORD` 改成新账号的值；Docker 部署下
mysql-init 脚本不会对已初始化过的数据卷重跑，把
`deploy/mysql-init/03-create-app-runtime-users.sh` 里的 SQL 抄出来，用
`docker compose exec db mysql -uroot -p` 手动跑一遍建号，再重启
`api`/`worker`/`enterprise-hub`。`scripts/release_check.py`/生产启动校验
（`service/config_validation.py`）会在 `MYSQL_ROOT_PASSWORD` 缺失或跟业务账号
密码重复时直接报错拦下来，不会等到真出事才发现。

## 2.3 登录会话、内容安全策略、Redis 与上传上限

- **登录 Cookie**：令牌放在 HttpOnly Cookie 里（页面脚本读不到），生产必须带 `Secure`（只在 https 上发送），所以**生产必须走 https**。
  `SESSION_COOKIE_SECURE=0` 会被启动校验拒绝；`SESSION_COOKIE_SAMESITE` 默认 `lax`，可改 `strict`。前端和 API 必须同源（Nginx 把 `/api/` 反代到后端，模板已经是这样）。
- **内容安全策略（CSP）**：`deploy/nginx.conf` 里的 `Content-Security-Policy` 只允许加载本站的脚本和向本站发请求。改前端时不要重新引入内联脚本或外部脚本 / 字体 / 图片地址
  （`npm --prefix frontend run preview` 用同一份策略，能在上线前本地看到有没有挡住页面自己的东西；`tests/test_csp_policy.py` 核对两处一致）。API 响应自带最严格的策略。
- **Redis**：里面放着限流计数、验证码摘要、模型配置缓存（只有密文）。只放在内网 / 容器内部网络，**不要对外发布端口**；生产建议设口令（`REDIS_URL=redis://:口令@主机:6379/0`），
  跨主机连接用 `rediss://`（TLS）并配 ACL。没有口令时启动校验会给出警告。缓存内容只用 JSON，不再使用 pickle。
- **上传**：流式写进临时文件（`knowledge_files/.incoming`，和最终位置同一个文件系统，入库时是 rename，不占内存）。上限：`KNOWLEDGE_UPLOAD_MAX_BYTES`（单个文件，默认 50MB）、
  `KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES`（一次请求累计，默认 50MB，与 Nginx `client_max_body_size 50m` 一致）、`KNOWLEDGE_UPLOAD_MAX_FILES`（批量个数，默认 20）、
  `USER_MAX_CONCURRENT_UPLOADS`（同一用户同时上传的个数，默认 3）；Nginx 的 `client_max_body_size` 和 `MAX_REQUEST_BODY_BYTES` 要不小于它们。
  没有写权限或文件类型不支持的请求，在接收文件内容之前就被拒绝。
- **脚本 / 集成的令牌**：浏览器登录（`POST /user/login`）响应体里没有 JWT。需要 Bearer 令牌的脚本走 `POST /auth/token`（单独限流 + 审计 `auth.token_issued`），
  **生产默认关闭**（`AUTH_TOKEN_ENDPOINT_ENABLED=1` 才开，开了启动校验会提醒）。压测脚本 `scripts/load_test.py` 和 `scripts/drill_*.py` 用的就是它。

## 3. 健康检查

三个公开的、不需要登录的探针，只回布尔值，给 Docker healthcheck、负载均衡这类不带登录态的场景用：

| 地址 | 含义 | 返回 |
|---|---|---|
| `/live` | 进程还活着、能响应吗？永远轻量，不查任何依赖。失败才需要重启进程 | 始终 200 `{"ok": true}` |
| `/ready` | 现在能接业务流量吗？查数据库（必须）和 Redis。Docker healthcheck 和负载均衡用它 | 就绪 200，**数据库不可用 503**；`{"ok": bool, "degraded": bool}` |
| `/health` | 旧地址，语义和 `/ready` 相同（以前不管依赖是否可用都返回 200，容器永远是 healthy） | 同 `/ready` |

Redis 配置了却连不上算“降级”（`degraded: true`，并有 `agent_redis_up` 指标和告警），**默认仍然就绪**：限流 / 并发控制会按下面的策略收紧，
而不是 Redis 抖一下所有实例同时被摘掉。更看重一致性的部署设 `READY_REQUIRE_REDIS=1`，Redis 不可用时也返回 503。

Redis 不可用时的降级策略（没配置 Redis 的单进程部署不受影响）：

- 登录 / 短信 / 注册 / 找回密码（`RATE_LIMIT_CRITICAL_FALLBACK`）：`strict`（生产默认，各进程用内存额度，但额度按 `API_WORKERS` 均分，合计不超过原额度）/ `open` / `closed`（直接拒绝）；
- 重任务并发名额（`CONCURRENCY_REDIS_DOWN`）：`closed`（生产默认，停止接收新任务）/ `local`；
- 每次降级都记 `agent_limiter_redis_degraded_total{limiter,mode}`，告警规则见 `deploy/prometheus-rules.yml`（`AgentRedisDown`、`AgentLimiterDegraded`）。

数据库连接池、缓存/限流用的是不是 Redis、后台任务执行模式这些运行细节
**不再从 `/health` 公开**——这些是内部架构信息，之前任何知道这个 URL 的匿名
请求都能看到，属于不必要的踩点信息暴露。完整诊断现在需要登录，走
`/system/diagnose`（同一份数据也能在产品里看：普通用户在「设置」页，
管理员在后台「系统诊断」页）：

- `database` 为正常
- `redis` 为正常（未配置 Redis 时为回退内存模式）
- `tasks.execution_mode` 为 `worker`
- `tasks.worker_required` 为 `true`
- `database.pool.checked_out` 不应长期接近 `DB_POOL_SIZE + DB_MAX_OVERFLOW`

Prometheus 指标端点：`http://<域名>/metrics`（可接入已有的 Prometheus / Grafana）。
`/metrics` 目前也是公开的，生产环境建议在反向代理层限制只允许内网/监控系统访问。

数据库连接池说明：

```env
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=1800
DB_POOL_PRE_PING=true
```

同步数据库链路使用 `mysql+pymysql`，异步读取链路使用 `mysql+asyncmy`。两条链路复用同一组连接池参数。`asyncmy` 是硬依赖（`requirements.txt` 固定版本，`models/async_db.py` 缺驱动会直接启动失败），已不再保留「未装 asyncmy 时回退同步查询」的旧分支；纯读接口全量走 AsyncSession，写入 / RAG 检索链路仍是同步（见 `docs/sync-async-boundary.md`）。

安全相关配置：

```env
TRUSTED_HOSTS=your-domain.com,www.your-domain.com
CORS_ALLOW_ORIGINS=https://your-domain.com,https://www.your-domain.com
MAX_REQUEST_BODY_BYTES=52428800
ENABLE_HSTS=1
```

`TRUSTED_HOSTS` 控制允许访问后端的 Host，`CORS_ALLOW_ORIGINS` 控制浏览器跨域来源。生产环境不要配置成 `*`。只有站点已经启用 HTTPS 时才开启 `ENABLE_HSTS=1`。

外部服务韧性配置：

```env
HTTP_CLIENT_MAX_RETRIES=2
HTTP_CLIENT_RETRY_BASE_SECONDS=0.3
HTTP_CIRCUIT_FAILURE_THRESHOLD=5
HTTP_CIRCUIT_COOLDOWN_SECONDS=30
LLM_REQUEST_TIMEOUT_SECONDS=60
LLM_STREAM_TIMEOUT_SECONDS=60
EMBEDDING_REQUEST_TIMEOUT_SECONDS=30
```

普通 LLM/Embedding 请求会在超时、连接失败或 429/5xx 时重试；流式输出不会自动重试，避免用户已经收到部分内容后重复输出。连续失败达到阈值后会短暂熔断，熔断状态可在 `/health` 的 `resilience.circuits` 中查看。

`API_WORKERS`（或 `--workers`）会放大总连接数。例如 2 个 worker 时，API 服务理论最大连接数约为 `(DB_POOL_SIZE + DB_MAX_OVERFLOW) * 2`，再加上后台 Worker 进程自己的连接。生产调大这些值前，要确认 MySQL 的 `max_connections` 足够。

## 4. Worker 扩容

后台任务多时，可以多起几个 `python -m service.background_worker` 进程。Worker 会通过数据库领取 `queued` 任务，多个 Worker 不会重复执行同一个任务；自定义组件的定时调度也用同样的乐观锁抢占。

Worker 失败重试配置：

```env
TASK_MAX_AUTO_RETRIES=2
TASK_RETRY_BASE_SECONDS=30
TASK_RETRY_MAX_SECONDS=300
```

任务失败后不会立刻反复执行，而是按退避时间写入 `next_run_at`，到时间后再由 Worker 领取。超过 `TASK_MAX_AUTO_RETRIES` 后任务进入 `failed` 终态，管理员或用户可以在任务中心手动重试。

Redis 连接恢复配置：

```env
REDIS_RECONNECT_INTERVAL_SECONDS=5
```

缓存、验证码、限流和并发控制都会优先使用 Redis。Redis 短暂不可用时接口会回退到进程内内存；到达重连间隔后会自动尝试恢复 Redis，不需要重启 API。

## 5. 数据持久化与备份

需要纳入备份的：

- MySQL：定期导出

  ```powershell
  mysqldump -u root -p --single-transaction --routines --triggers agent_sql > backup/agent_sql_$(Get-Date -Format yyyyMMdd_HHmm).sql
  ```

  恢复：

  ```powershell
  mysql -u root -p agent_sql < backup/agent_sql_20260101_0000.sql
  ```

- 应用文件目录：`knowledge_files`、`vector_db`、`logs`、`skills`、`skills_packages`、`prompt/prompts`。

迁移服务器时，数据库导出文件和上述目录一起打包带走即可。

以上手动步骤已经包装成 `scripts/backup.py`（`npm run backup`），可以直接丢进 cron /
Windows 计划任务定时跑，会在 `backups/` 下生成一份 `db_*.sql` + `data_*.tar.gz`：

```bash
.venv/Scripts/python.exe scripts/backup.py --keep-days 14   # 顺带清理 14 天前的旧备份
```

Docker Compose 部署时，Chroma 数据在 `chroma_data` 具名卷里，不在上面的脚本覆盖范围，
单独备份：

```bash
docker run --rm -v pythonproject1_chroma_data:/data -v "$PWD/backups":/backup \
  alpine tar czf /backup/chroma_$(date +%Y%m%d_%H%M%S).tar.gz -C /data .
```

（卷名前缀跟你项目目录名有关，跑 `docker volume ls` 确认实际名字。）

## 6. 开发临时模式

生产推荐：

```env
TASK_EXECUTION_MODE=worker
```

如果只是本地快速测试，不想单独启动 Worker，可以临时改为：

```env
TASK_EXECUTION_MODE=fastapi
```

不要在生产环境使用 `fastapi` 模式执行重任务。

## 7. Skill 脚本沙箱（可选，默认关闭）

> 现状、保留的内容和后续扩展路线见 [skill-extension.md](skill-extension.md)。

导入的官方 Skill 常带 `scripts/*.py`。开启沙箱后，助手可以用 `run_skill_script` 工具运行这些 Python 脚本，
用户也能在聊天里上传文件（📎）交给脚本处理，脚本生成的文件会变成下载链接。

**这等于让服务器执行陌生人写的代码**，所以：默认关闭；只有导入了带脚本的 Skill 并且真有需求再开。

### 隔离措施（都已写在 docker-compose.prod.yml 的 `sandbox` 服务里）

- 单独的容器，只挂在 `internal` 网络 `sandbox_net`：**没有外网出口**，也访问不到 db / redis / chroma / **api / worker**。
- **网关**：api / worker 不在 `sandbox_net` 里，它们通过 `sandbox-gw` 间接调用沙箱。网关同时在默认网络和 `sandbox_net` 上，
  但只把 `GET /health` 和 `POST /run` 转发给固定的上游（沙箱本身）。拓扑：
  `api / worker ──▶ sandbox-gw ──▶ sandbox`，沙箱里的脚本看得到的只有网关，而网关只通向沙箱自己。
  （早期版本让 api / worker 直接挂在 `sandbox_net` 上，脚本能访问到 api 容器，所以加了网关。）
- 不加载 `.env`，容器里没有任何业务密钥；调用用一个独立的 `SANDBOX_TOKEN`。
- 根文件系统只读，可写的只有 tmpfs（重启即清空）；非 root 用户；丢弃全部 Linux capability。
- 上限：1 CPU、1GB 内存、128 个进程、单次最长 30 秒（`SANDBOX_TIMEOUT_SECONDS`，最大 60）、同时最多 2 个脚本。
- 只能运行 `.py`；`.sh` / `.js` 等一律不执行。镜像里没有 Node / LibreOffice，只预装 PDF / Excel / Word / pandas 等常用库（见 `sandbox/requirements.txt`）。

### 开启步骤

```bash
# 1. 生成令牌，写进 .env
python3 -c "import secrets; print(secrets.token_hex(32))"
#    .env 里设置：
#      SANDBOX_ENABLED=true
#      SANDBOX_TOKEN=<上面生成的值>

# 2. 构建并启动沙箱（profile 里的服务默认不会随 up -d 启动）
docker compose -f docker-compose.prod.yml --profile sandbox up -d --build sandbox sandbox-gw

# 3. 重启 api 和 worker，让它们读到新的环境变量
docker compose -f docker-compose.prod.yml up -d api worker
```

关闭：把 `SANDBOX_ENABLED` 改回 `false`，重启 api / worker；`docker compose ... stop sandbox sandbox-gw` 停掉容器。
关闭后已导入的 Skill 仍在，只是脚本不再运行，助手会按文字说明工作。

### 部署后必须做的验收（一条命令）

本地开发机测不了容器层面的隔离，所以开启后**必须**在服务器上跑一次：

```bash
docker compose -f docker-compose.prod.yml exec api python scripts/sandbox_acceptance.py
```

它会在真实沙箱里逐项检查并给出 PASS / FAIL：沙箱健康、能运行脚本、**非 root**、**连不出外网**（含解析外部域名）、
**连不到 db / redis / chroma / api / worker**、**根文件系统只读**、**环境里没有业务密钥**、死循环会被终止、
超过内存上限的脚本被拒绝、产出文件能带回来、**镜像里声明装了的依赖真的都能 import**、ffmpeg 可用。
退出码 0 = 没有 FAIL。

**任何隔离类的 FAIL（`no_egress`、`no_internal_access`、`readonly_fs`、`env_clean`）都要立刻关闭沙箱**
（`SANDBOX_ENABLED=false`）并排查网络配置。`memory_limit` 会占用较多内存，服务器内存紧张时先加 `--skip-heavy`。

已知且接受的限制（脚本输出里会以 INFO 列出）：脚本能读到沙箱自己的令牌（同一用户可读 `/proc/1/environ`）。
这个令牌只能调用沙箱本身，而网关只通向沙箱，价值有限。

### 附件文件

用户上传的附件和脚本生成的文件存在 `app_data` 卷的 `/app/data/attachments/`，按用户隔离，
默认保留 7 天（`ATTACHMENT_TTL_DAYS`），过期文件在该用户下次上传时清理。

### 谁能带脚本、上架和额度

- **技能只有管理员能维护**：创建、编辑、删除、导入（含 GitHub 链接）、翻译、导出、上架，全部要求管理员。
  普通用户只能看「能力商店」、在创建/编辑助手时**直接绑定**商店里的技能、上传输入文件、使用。
  商店里的都是管理员上架的，标「官方」。**不开放用户自带脚本**；要开放，须先做到文件隔离、依赖管理、审计和配额都完善。
  代码层面有两道：路由要求管理员，导入策略（`skill_route._import_policy`）再按角色判断一次脚本和上架权限。
- 所有用户绑定的是**同一份**技能配置（不再有各人的副本）：管理员改一次全员生效。
- 管理员在后台「全部技能」里能看到并维护**所有**技能，包括以前由普通用户自己创建的旧技能（编辑、翻译、回滚、删除都可以）。
  配置文件写入是"先写临时文件并校验，通过后才原子替换"，校验不过或中途出错，线上的配置原样保留。
  **有版本和回滚**：每次编辑（提示词 / 工具 / 权限 / 名称 / 说明，含翻译）之前会自动保存一份快照，最多留最近 20 个；
  后台技能卡片上的「历史版本」按钮可以一键恢复，恢复前也会先保存当前状态，恢复错了还能再恢复回来。
  快照存在数据库表 `skill_version`（应用启动时自动建表；也有幂等的 Alembic 迁移 `20260921_0007`）。
  恢复不改变是否公开。把技能设为不公开后，已经绑定它的助手仍能继续用；删除技能才会自动解绑（同时清掉它的历史版本）。
- 每个用户默认 60 秒内最多运行 10 次脚本（`SANDBOX_RATE_LIMIT` / `SANDBOX_RATE_WINDOW_SECONDS`）；
  沙箱同时只跑 2 个脚本（全站共用），超出会提示"沙箱正忙"。
- 每人附件总量默认 100MB、最多 50 个文件（`ATTACHMENT_USER_MAX_MB` / `ATTACHMENT_USER_MAX_FILES`），
  脚本生成的文件也占这份额度，满了会明确告诉用户没保存。
- **审计**：每次脚本运行、被限频、沙箱不可用都会记到 `logs/sandbox_audit_*.log`
  （用户、助手、Skill、脚本、参数个数、退出码、耗时、产出文件数）。

### 脚本能不能跑：导入时的静态检查

导入 Skill 时会读一遍每个 `.py` 的 `import`，对照沙箱镜像里装的库，标出：**缺依赖**、**要联网**、**会调系统命令**。
结果显示在技能卡片上：「脚本可运行」/「部分脚本可运行 x/y」/「脚本暂不支持」/「脚本需管理员启用沙箱」，
助手也只会被告知能跑的那些脚本。这是**静态判断**，不等于实测能跑（动态 import、运行时拼出来的命令查不出来），
所以开启沙箱后仍要挑代表性的 Skill 实际跑一遍。

**改沙箱依赖时，两处必须同步**：`sandbox/requirements.txt` 和 `service/skills_core/script_report.py` 里的
`PACKAGE_MODULES`（测试会校验两边一致）。**沙箱里装了什么、有意没装什么**：装了 PDF / Excel / Word / PPT / pandas / numpy / Pillow 这类文件与数据处理库，
以及图像音频视频处理（opencv-headless、imageio-ffmpeg 自带 ffmpeg、scipy、soundfile、pyloudnorm、matplotlib）。
**有意不装**：`moviepy`（官方 Skill 用的 1.0.3 版和新版 Pillow / NumPy 2 有已知不兼容，能实机验证前不装）、`requests` 等联网库（沙箱没有外网，装了只会让脚本跑一半才报错）、torch / transformers 等深度学习库
（要下载模型权重，同样需要网络，而且镜像会大到几个 GB）、Coze / 浏览器自动化这类平台专属 SDK。
想让更多 Skill 能跑，就是往这两处加库、重建沙箱镜像、
然后在后台「技能管理」点「重新检查脚本」刷新检查结果，不用重新导入。

依赖版本：`sandbox/requirements.txt` 里 `==` 的是本地验证过的版本，`>=` 的几个（pdfplumber、reportlab、
python-pptx、beautifulsoup4）首次构建后请用 `docker compose ... run --rm sandbox pip freeze` 补成精确版本；
镜像里也有一份 `/opt/installed-packages.txt` 记录实际装了什么。

### 内存

沙箱容器上限 1GB，会和 MySQL、向量库、API、Worker 同机运行。开启前先确认服务器可用内存足够
（2 核 4G 的机器建议把沙箱内存上限降到 512MB，并观察一段时间）。
