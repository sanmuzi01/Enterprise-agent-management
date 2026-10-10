# Startup Guide

本地开发启动说明。项目按进程分为三块：后端 API、后台 Worker、前端 dev server。

## 1. 本地依赖

- **MySQL**：本机安装并启动，建一个库（默认 `agent_sql`）。连接信息写在项目根目录的 `.env`
  （`DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME`）。
- **Redis**：可选。`.env` 里 `REDIS_URL` 留空即可——缓存 / 限流 / 验证码会回退到进程内内存，
  不影响功能。需要多进程共享状态时再装 Redis 并填 `REDIS_URL`。
- **Python**：用项目自带的 `.venv`（`pip install -r requirements.txt`）。
- **Node**：前端在 `frontend/` 下 `npm install`。

首次或换库后同步表结构（二选一）：

```powershell
npm run db:migrate
```

或直接让应用启动时自动建表（`bootstrap_database()` 会 `create_all` + 幂等迁移）。

## 2. 启动（三个终端，项目根目录）

后端 API：

```powershell
npm run backend:dev
```

后台 Worker（自定义组件的定时调度、知识库入库都在这里跑）：

```powershell
npm run backend:worker
```

前端：

```powershell
npm run frontend:dev
```

访问地址：

```text
前端：http://127.0.0.1:5173
后端：http://127.0.0.1:8011
健康检查：http://127.0.0.1:8011/health
```

前端 dev server 会把 `/api/*` 代理到 `http://127.0.0.1:8011`，不用配 CORS。
根目录的 `frontend:dev` 脚本固定绑定 `127.0.0.1:5173`，并启用 Vite `strictPort`：
如果 5173 被旧进程占用，启动会明确失败，避免自动换端口或 IPv4/IPv6 地址不一致造成“看似启动、页面打不开”。

## 3. 生产 / 服务器部署

用进程管理器（systemd、supervisor、nssm 等）分别常驻两个进程：

```text
uvicorn FasdtApi.main:app --host 0.0.0.0 --port 8000 --workers 2
python -m service.background_worker
```

前端 `npm run frontend:build` 产出 `frontend/dist`，交给 Nginx 之类做静态托管 + `/api` 反代。
其余环境变量与调优见 `docs/deployment.md`。

## 4. 常见问题

### npm run dev 报 package.json 不存在

命令没在项目根目录或前端目录执行。正确方式：

```powershell
cd D:\PyCharm\PythonProject1
npm run frontend:dev
```

### Vite 提示 ECONNREFUSED 127.0.0.1:8011

前端起来了但后端没起。先跑：

```powershell
npm run backend:dev
```

### 8011 端口被占用

```powershell
netstat -ano | findstr :8011
```

结束对应进程前确认不是你正在用的服务。

### 5173 端口被占用或页面返回 404

先确认占用者确实是本项目的旧 Vite 进程，再停止它；不要同时开多个 `frontend:dev`：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 5173 | Select-Object LocalAddress,LocalPort,OwningProcess
Get-CimInstance Win32_Process -Filter "ProcessId=<上一步的 OwningProcess>" | Select-Object ProcessId,CommandLine
Stop-Process -Id <确认后的进程号>
npm run frontend:dev
```

### 登录后反复加载，日志持续出现 counts / unread-count 401

这是失效会话。当前前端会在首个非登录请求收到 401 后同时清理本地用户信息和 `csrf_token`，然后稳定回到登录页，
不会再在登录页和业务页之间循环。升级前遗留的页面先按一次 `Ctrl+F5`；仍未刷新时清除该站点的 Cookie 和 Local Storage。

### 文档 / 组件一直显示处理中

检查 Worker 是否在跑：

```powershell
npm run backend:worker
```

或打开管理后台的系统诊断页，看 Worker 模式、Redis、数据库连接是否正常。
