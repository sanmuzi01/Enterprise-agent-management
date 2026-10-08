# Load Testing

本项目内置一个轻量 HTTP 压测脚本：`scripts/load_test.py`。它只依赖 Python 标准库，适合上线前做基础容量摸底和回归验证。

## 场景

- `health`：只请求 `/health`（与 `/ready` 相同：数据库不可用返回 503），用于确认服务和数据库就绪。
- `auth-read`：请求 `/api/user/me`、`/api/agent/list`、`/api/task/?limit=10`，用于测试登录态读接口。
- `mixed-read`：混合 `/health` 和登录态读接口。

脚本不会默认压测聊天、RAG 上传、Embedding 或 LLM 调用，避免误消耗模型额度。

脚本不是浏览器：它用 `POST /auth/token` 换 Bearer 令牌（浏览器登录接口 `/user/login` 响应体里没有 JWT）。生产环境这个接口默认关闭，压测前要设 `AUTH_TOKEN_ENDPOINT_ENABLED=1`，压完关掉；它有单独的限流（`TOKEN_IP_RATE_LIMIT`，默认每 5 分钟 10 次）。

## 本地基础压测

先启动后端：

```powershell
npm run backend:dev
```

另开一个终端执行：

```powershell
npm run load:test -- --base-url http://127.0.0.1:8000 --scenario health --requests 200 --concurrency 20
```

## 生产反代压测

Nginx 反代起来后，压测前端反代入口：

```powershell
npm run load:test -- --base-url http://127.0.0.1 --scenario health --requests 500 --concurrency 50
```

## 登录态读接口压测

```powershell
npm run load:test -- --base-url http://127.0.0.1 --scenario auth-read --username admin --password 139218 --requests 300 --concurrency 30
```

如果使用生产环境，不建议用内置管理员账号做大压测，应创建专门的压测账号，避免污染真实管理日志。

## 保存 JSON 报告

```powershell
npm run load:test -- --base-url http://127.0.0.1 --scenario mixed-read --username testuser --password testpass --requests 300 --concurrency 30 --json > load-report.json
```

## 结果解读

- `success_rate`：上线前基础读接口应接近 100%。
- `rps`：每秒请求数，用于观察吞吐上限。
- `latency_ms.p95`：95% 请求延迟，通常比平均值更能反映用户体验。
- `latency_ms.p99`：尾部延迟，高并发下容易暴露数据库连接池、Redis 或慢查询问题。
- `status`：状态码分布。大量 `429` 说明触发限流，大量 `5xx` 说明服务端异常。

同时查看 `/system/diagnose`（需要登录；`/health` 只回布尔值，不再公开这些细节）：

- `database.pool.checked_out` 不应长期接近 `DB_POOL_SIZE + DB_MAX_OVERFLOW`。
- `cache.*.backend` 生产应为 `redis`。
- `limits.rate.backend` 和 `limits.concurrency.backend` 生产应为 `redis`。

## 上线建议

先从低并发开始，例如 `--concurrency 10`，再逐步提高到 20、50、100。每提高一档都观察：

- API 日志是否出现 5xx。
- MySQL CPU、慢 SQL 和连接数。
- Redis 是否稳定。
- `/system/diagnose` 中连接池借出数量是否长期偏高。
- 前端 Nginx 是否出现超时。

压测结论要结合真实服务器配置，不能只看本地电脑结果。
