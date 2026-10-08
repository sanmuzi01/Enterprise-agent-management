# 私有知识库智能助手平台

> 自部署的多用户 AI 助手：用你自己的模型 Key，把内部文档变成能对话、带来源引用、
> 可治理、可评估的知识库。

一个从 0 搭起来的全栈项目——FastAPI + Vue 3，覆盖用户体系、Agent 对话、RAG 检索、
知识库权限治理、长期记忆、可视化工作台和管理后台。数据全部本地，模型走用户自配的
OpenAI 兼容 API。

---

## 能做什么

| 模块 | 说明 |
| --- | --- |
| **Agent 对话** | 用户自建智能体（模型 / 提示词 / RAG / 记忆开关），ReAct 引擎 + 工具调用，SSE 流式回答 |
| **知识库 RAG** | 上传 PDF/Word/TXT/MD → 切块（可调）→ 向量化 → ChromaDB 检索 → rerank → **带 `【来源N】` 引用**，回答里点引用能看原文片段 |
| **知识库空间 + 治理** | 多文档空间、跨空间联合检索；成员角色（owner/admin/editor/viewer）、操作审计、管理员企业视角 |
| **检索质量** | 健康分（文档失败/过期率 + 命中/拒答/引用率）、调试台（一次检索看全过程）、评估集 + RAG 自动评分 |
| **长期记忆** | 按 Agent 维度的对话记忆，自动总结沉淀 |
| **可视化工作台** | 选模板 + 填字段就能建小窗口（知识库健康 / 用量统计 / 内部接口 / 网页监控 / 每日 AI 简报 / 联网查指标），后台定时跑、攒历史、超阈值告警 |
| **网页监控** | 抓取公开网页正文入库或盯「有没有更新」，带 SSRF 防护 |
| **模型连接** | 自配 Key，支持 智谱 / DeepSeek / OpenAI / Kimi / 通义 / Perplexity；「一次连接」自动配好聊天 + 向量两条能力 |
| **管理后台** | 用户管理、任务管理、使用统计、操作日志、系统诊断 |

---

## 技术栈

- **后端**：FastAPI · SQLAlchemy 2.0（异步 `asyncmy`）· MySQL · Redis（可选，缺省回退进程内）· ChromaDB
- **前端**：Vue 3 · Vite · TypeScript · Tailwind · ECharts（按需懒加载）
- **测试**：Python `unittest` 1825 条（含**真实路由级测试**：TestClient + 真 JWT + 真 DB + 跨用户隔离校验；真实 Redis 并发测试）、Java 143 条、前端 Vitest 32 条；CI 还跑迁移漂移检查和真实浏览器冒烟
- **迁移**：Alembic + 启动幂等建表
- **可观测**：Prometheus `/metrics`、请求耗时/状态码、连接池/缓存指标

### 一些工程上的取舍

- **同步 → 异步分阶段迁移**：聊天 / RAG 检索链路已全量 `AsyncSession`，LangGraph 同步引擎经
  `asyncio.to_thread` + `asyncio.Queue` 桥接；每阶段配对照测试，全程可用。见
  [`docs/agent-runtime-async-migration.md`](docs/agent-runtime-async-migration.md)、
  [`docs/sync-async-boundary.md`](docs/sync-async-boundary.md)。
- **领域异常层**：`service/exceptions.py` + `main.py` 统一处理器，路由不裸抛 `HTTPException`。
- **外部调用韧性**：LLM / Embedding / 抓取统一超时、重试、指数退避、进程内熔断。
- **生产配置校验**：启动时拦截占位密钥、缺 Redis、CORS/Host 未收紧等。

---

## 快速开始

### 用 Docker（最省事，一条命令起全套）

```bash
docker compose up -d --build
docker compose exec api python scripts/seed_demo.py   # 可选：塞演示数据
# 打开 http://localhost:8080 ，用 demo / demo12345 登录
```

起的是 MySQL + Redis + 后端 API + 后台 Worker + 前端。仅供本地体验；
生产部署见 [`docs/deployment.md`](docs/deployment.md)。

### 本地开发（不用 Docker）

需要本机 **MySQL**（建一个空库）。Redis 可选，留空自动回退。

```bash
# 1. 后端依赖
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt          # macOS/Linux

# 2. 配置：复制模板，填 DB_* / JWT_SECRET_KEY / LLM_ENCRYPTION_KEY（模板注释里有生成命令）
cp .env.example .env

# 3. 前端依赖
npm --prefix frontend install
```

表结构在后端首次启动时自动建（`bootstrap_database()` = `create_all` + 幂等 `ALTER`），
只需要一个空的 MySQL 库，不用手动迁移。

三个进程（三个终端，项目根目录）：

```bash
npm run backend:dev      # API  → http://127.0.0.1:8011
npm run backend:worker   # 定时调度 + 知识库入库
npm run frontend:dev     # 前端 → http://localhost:5173
```

**塞一份演示数据**（可选，用本地向量模型，不需要任何 Key）：

```bash
npm run seed:demo        # 建 demo/demo12345 + 示例助手 + 一份已入库的知识库文档
```

打开 `http://localhost:5173`，用 `demo / demo12345` 登录——知识库检索、调试台、健康分
开箱即用；聊天再去「模型连接」填一个聊天模型 Key 即可。或直接注册新账号从零开始。

详见 [`docs/startup-guide.md`](docs/startup-guide.md)。

部门工作台的 AI 工作成果（CRM 跟进、报销、请假、采购四类材料整理 → 人工核对 → 业务草稿）与本地验收，见 [`docs/agent-productivity-workflows.md`](docs/agent-productivity-workflows.md)。

---

## 测试

```bash
npm run test:unit          # Python 1825 条；路由级测试需本机 MySQL（没有时会跳过，别误以为全绿——一条命令起测试用的 MySQL / Redis 见 docs/testing.md）
npm run release:check      # 上线自检：关键文件 + 编译 + 单测 + 前端构建
```

---

## 目录

```
FasdtApi/          FastAPI 路由层（按登录用户隔离）
service/           业务层
  runtime/         Agent 运行引擎（ReAct + SSE）
  rag/             检索链路（切块 / 向量 / 检索 / rerank / 联网检索）
  widgets/         工作台组件（连接器 / 处理器 / 调度 / 模板）
  knowledge_space/ 知识库空间 + 权限 + 健康分
  llm/             模型工厂 + 多厂商适配 + 联网开关
  evaluation/      RAG 评估
models/            ORM + DAO（同步 / 异步双轨）
frontend/src/      Vue 3 前端
scripts/           release_check / load_test / 数据迁移 / 测试清理
docs/              架构 / 迁移 / 测试 / 部署 / 各功能设计文档
tests/             unittest（含真实路由级测试脚手架 _route_client.py）
```

---

## 文档

`docs/` 下有完整的架构、迁移、测试、部署和各功能设计说明。入口：
[`docs/project-architecture.md`](docs/project-architecture.md) ·
[`docs/project-status.md`](docs/project-status.md) ·
[`docs/backend-map.md`](docs/backend-map.md)
