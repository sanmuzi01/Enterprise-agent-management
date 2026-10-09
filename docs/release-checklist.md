# Release Checklist

每次准备上线或打包前，按这份清单检查，避免留下半成品、临时配置或未验证改动。

## 代码状态

- `git status --short` 中只保留本次明确要发布的源码和配置模板。
- 不提交 `.env`、`.venv`、`.idea`、日志、向量库、上传文件、release 备份包。
- 新增功能有对应文档或页面提示。
- 没有调试打印、临时代码、硬编码密钥。

## 后端检查

```powershell
.venv\Scripts\python.exe -m compileall FasdtApi service models utils
.venv\Scripts\python.exe -c "from FasdtApi.main import app; print(len(app.openapi()['paths']))"
```

也可以直接执行综合自检：

```powershell
npm run release:check
```

综合自检里包含**空库测试**（`scripts/test_fresh_db.py`：临时库上跑迁移 + 两次初始化 + 和空库相关的测试，跑完删库）。
数据库账号没有建库权限时用 `npm run release:check -- --skip-fresh-db`，并以 CI 的 backend 任务结果为准。

必须确认：

- 应用可导入。
- `/live` 返回 200，`/ready`（与 `/health` 相同）在数据库正常时返回 200；停掉数据库后 `/ready` 必须返回 503（Docker healthcheck 用它）。
- 登录、聊天、知识库、任务、管理员路由仍存在。
- 数据库幂等启动迁移仍可执行。
- Alembic 迁移文件存在，已有环境发布前已完成备份和版本确认。

## 数据库迁移

已有数据库首次纳入迁移管理：

```powershell
npm run db:stamp
npm run db:current
```

### 升级到「移除明文口令兜底」版本时（仅需一次）

登录已不再接受非 bcrypt 的存储口令。发布前先在目标库跑：

```powershell
.venv\Scripts\python.exe -m scripts.migrate_plaintext_passwords          # 预演统计
.venv\Scripts\python.exe -m scripts.migrate_plaintext_passwords --apply  # 确认后写库
```

必须确认「非 bcrypt（待迁移）」统计归零后再发布，否则存量明文用户会登录失败。

后续正式结构变更：

```powershell
npm run db:migrate
```

必须确认：

- 迁移前已完成备份。
- 迁移文件经过人工检查。
- 测试环境已经跑过一次迁移和回滚方案评审。

## 前端检查

```powershell
npm run frontend:build
```

必须确认：

- TypeScript 检查通过。
- Vite 生产构建通过。
- 页面不会依赖本地 dev server 专用地址。

## 任务和缓存

- 生产环境使用 `TASK_EXECUTION_MODE=worker`。
- API 和 Worker 都配置同一个 MySQL、Redis、文件挂载目录。
- Redis 不可用时系统能降级，但生产应修复 Redis，而不是长期依赖内存兜底。
- Worker 日志能看到任务领取、成功、失败。

## 部署

- `uvicorn FasdtApi.main:app --host 0.0.0.0 --port 8000 --workers 2` 与 `python -m service.background_worker`
  分别由进程管理器常驻，异常退出能自动拉起。
- `python -m alembic upgrade head` 已执行，表结构与迁移一致。
- 前端 `npm run frontend:build` 产物由 Nginx 托管，`/api`、`/health`、`/metrics` 反代到后端。
- 浏览器访问 `/health` 返回后端健康检查；`/metrics` 返回 Prometheus 文本指标。

## 上线后验收（交付）

- 用一个**从没打开过本站的浏览器**和一个**开着旧版本页面的浏览器**各访问一次：旧页面应自动刷新到新版本，不能白屏（nginx 缓存规则见 `docs/deployment.md` 2.3）。
- `curl -I https://<域名>/` 看到 `Cache-Control: no-cache`；`curl -I https://<域名>/assets/不存在.js` 返回 404，不是 200 的 HTML。
- 登录后持续操作超过 30 分钟不被登出；后台“组织架构”能看到企业（空库首次部署时由启动自动创建）。
- 跑一次数据体检：`.venv/bin/python scripts/data_health_check.py`，没有“严重”项；“注意”项和客户确认后再 `--fix`（先备份）。
- 和客户确认数据保留期限后再打开 `DATA_RETENTION_AUTO`（见 `docs/deployment.md` 5.2）；第一次先 `scripts/data_retention.py` 只看不删。
- 启用每日数据体检定时任务 `deploy/systemd/agent-data-health.timer`。

## 备份恢复

发布前用 `mysqldump --single-transaction --routines --triggers <库名>` 导出数据库。

必须确认：

- 数据库导出文件已生成。
- `knowledge_files`、`vector_db`、`skills`、`skills_packages`、`prompt/prompts` 已一并打包。
- 备份已复制到服务器以外的位置。
- 至少在测试环境做过一次恢复演练。

## 压力测试

基础接口压测：

```powershell
npm run load:test -- --base-url http://127.0.0.1 --scenario health --requests 500 --concurrency 50
```

登录态读接口压测：

```powershell
npm run load:test -- --base-url http://127.0.0.1 --scenario auth-read --username testuser --password testpass --requests 300 --concurrency 30
```

必须确认：

- 成功率接近 100%。
- 没有持续 5xx。
- `/system/diagnose` 中数据库连接池没有长期打满。
- 生产环境缓存和限流后端为 Redis。

## 监控检查

- `/metrics` 已接入现有 Prometheus，`agent-api` target 为 UP。
- `agent_http_requests_total` 有请求计数。
- `agent_http_request_duration_seconds_bucket` 有延迟桶数据。
- `agent_db_pool_connections` 有连接池数据。

## 安全检查

- 生产 `.env` 已替换 `DB_PASSWORD`、`JWT_SECRET_KEY`、`LLM_ENCRYPTION_KEY`。
- `TRUSTED_HOSTS` 已配置为真实域名和内部服务名。
- `CORS_ALLOW_ORIGINS` 已配置为真实 HTTPS 前端域名。
- HTTPS 生效后才开启 `ENABLE_HSTS=1`。
- 请求体限制 `MAX_REQUEST_BODY_BYTES` 与 Nginx `client_max_body_size` 保持一致。
- `LLM_ENCRYPTION_KEY` 是有效 Fernet key。
- 管理员账号只授予明确管理员。
- 普通用户不能访问 `/admin` 接口。
- 用户之间 Agent、Skill、知识库、会话、任务不能串数据。
- 爬虫功能禁止访问内网、localhost、链路本地地址和保留地址。
- `CRAWLER_MAX_BYTES`、`CRAWLER_TIMEOUT_SECONDS`、`KNOWLEDGE_CRAWL_RATE_LIMIT` 已按服务器资源设置。
- 如需抓取强 JS 渲染页面，确认镜像已安装 Playwright Chromium，并按需设置 `CRAWLER_BROWSER_FALLBACK=1`。
- 浏览器抓取会增加 CPU/内存开销，生产环境应配合 `KNOWLEDGE_CRAWL_RATE_LIMIT` 控制频率。

本地排查某个网页为什么抓不了：

```powershell
npm run crawl:check -- https://example.com
npm run crawl:check -- https://example.com --browser
```

## 收尾

- 不留下运行中的 dev server、worker 或测试进程。
- 记录本次改动、验证命令和已知风险。
- 打包前清理本地构建产物和临时文件。
