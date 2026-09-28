# Testing

项目当前使用 Python `unittest`，880 条（2026-09-28 更新）。纯逻辑单测（TTL 缓存、重试熔断、
短信校验、模型厂商适配等）不依赖任何外部资源；但大部分测试是**真实路由级测试**
（`TestClient` + 真 JWT + 真 MySQL），需要本机能连上一个空的 MySQL 库才能跑——没有 MySQL 时
这部分会被跳过（`OK (skipped=N)`），不是全量绿。真实企业业务中心（`enterprise-business-hub`）
的联调测试是独立的 Java/JUnit 套件，见该目录下的说明，不在这个 Python `unittest` 范围内。

## 运行单元测试

```powershell
npm run test:unit
```

当前覆盖（举例，非全列，完整清单见 `tests/` 目录和各功能设计文档）：

- 用户/团队/知识库的多租户数据隔离，跨用户越权访问全部拦截。
- Agent 对话、RAG 检索、知识库权限治理的真实路由级用例。
- 审批流程、部门 Agent 发布、owner 保护等并发安全场景（重复提交、竞态覆盖）。
- 企业业务中心（OA/采购/CRM）Java 集成的 HMAC 签名绑定、幂等去重（Python 侧签名 + Java 侧
  JUnit 集成测试）。
- TTL 缓存过期和深拷贝保护、短信验证码流程、HTTP 外部调用重试熔断、模型厂商识别与适配。
- 生产环境配置校验，防止占位密钥、无 Redis、短信误配置上线。

## 上线前建议测试顺序

```powershell
.venv\Scripts\python.exe -m compileall FasdtApi service models utils scripts tests
npm run test:unit
npm run frontend:build
npm run load:test -- --base-url http://127.0.0.1 --scenario health --requests 200 --concurrency 20
```

## CI 质量门禁

每次 push / PR，GitHub Actions（`.github/workflows/ci.yml`）依次跑：编译检查 → ruff → 依赖漏洞扫描 →
单元测试 + 覆盖率 → （前端）依赖漏洞扫描 → 类型检查 + 构建。任意一步失败，PR 不能合。

### 覆盖率

```powershell
npm run test:coverage
```

等价于 `coverage run -m unittest discover -s tests` + `coverage report --fail-under=64` + `coverage xml`。
配置在 `pyproject.toml` 的 `[tool.coverage.*]`：只统计 `FasdtApi/service/models/utils`（业务代码），
排除 `migrations/` 和 `utils/log_to_csv.py`（没有任何路由/服务调用的一次性运维脚本，放业务代码目录纯属历史遗留）。

当前真实基线约 **67%**（2026-09-28 更新：第四轮安全审计的一批并发/签名回归测试把基线从
64.6% 推高了；`coverage report --sort=-miss` 能重新核对），门槛调到 **65%**（留安全边际，
不直接顶到真实值，避免个别环境跑不到某几个用例就红）。按下面顺序继续调高：

1. 新增功能必须带测试，不能让覆盖率因为新代码被拉低。
2. 挑覆盖率低但**活跃使用**的模块（`coverage report --sort=-miss` 能看出来，目前排在最前的
   是 `service/agent_service.py`（37%）、`service/llm/llm_config_service.py`（30%）、
   `FasdtApi/knowledge.py`（36%）、`service/background_task_service.py`（34%））逐个补测试，
   覆盖率过 70% 后把 `--fail-under` 提到 70，之后同样的方法继续提到 75。
3. 不要为了凑数字给不常用的代码加空测试，也不要为了让数字好看把活跃代码加进 `omit`。

## 已知的无害噪音：Windows 下的 asyncmy teardown ResourceWarning

本机（Windows + ProactorEventLoop）跑全量测试，进程退出前偶尔会打印几行
`ResourceWarning: unclosed transport` / `unclosed socket`。已经确认过不是真的连接泄漏
（`tests/_async_helpers.py` 顶部注释记录过根因和第一版修复：`asyncio.run()` 每次新建/
关闭事件循环，但 `models/async_db.py` 的 `async_engine` 连接池是进程级单例，个别连接的
关闭动作会落到已经关闭的事件循环之外——真正的 bug 是这会报 `AttributeError`，那部分已经
用 `run_async`/`run_async_factory` helper 修好了）。这几行 `ResourceWarning` 是
Windows 专属的、GC 时机相关的诊断噪音，不代表测试结果不可信：
- 全量测试结果始终是 `OK`（880 个测试全绿），这几行出现在测试统计之后，不影响任何用例的
  成功/失败判定。
- CI（`.github/workflows/ci.yml`）跑在 `ubuntu-latest` 上，Linux 用的是 epoll 事件循环，
  没有 `ProactorEventLoop`，这个噪音在 CI 里根本不会出现。
- 评估过用 `atexit` 在进程退出前统一 `dispose()` 一次连接池，但那时候原来创建这些连接的
  事件循环已经关闭，再 dispose 一次只是把同样的告警挪到另一个时间点，不能真正消除，还多
  引入一个"进程退出阶段跑异步代码"的新脆弱点——收益不确定、风险不低，评估后决定不做，
  留档说明原因，而不是硬塞一个治标不治本的修复。

### Lint（ruff）

```powershell
npm run lint:py
```

`pyproject.toml` 里 `select = ["E9", "F"]`：只挡语法错误和 pyflakes（未使用的导入/变量、用到没定义的名字等）——
大概率是 bug，不是风格问题。**没有**开 ruff 默认更大的规则集（pyupgrade / bugbear / bandit 等），
现状代码库全量跑一遍有约 2900 条，绝大多数是"能跑但不够新"的风格建议，不是缺陷，硬开只会把 CI 变成没人看的噪音。
想扩大检查范围：先加一类规则跑一遍看有多少违规，评估要不要一次清干净，再加进 `select`。

### 依赖漏洞扫描

```powershell
npm run audit:py    # 后端，pip-audit
npm run audit:npm   # 前端，npm audit（只挡 high/critical）
```

`pip-audit` 的输出没有统一的严重度字段（不像 npm audit 有 severity），所以后端这边按"能不能修"分类，
不是按"严重度"分类：**有修复版本却没升级** → 阻断 CI，逼着升级；**上游还没出修复版本** → 记录在
CI 配置里、写清楚原因，不阻断（阻断了也没用，只会让 CI 一直红）。当前免检名单（`.github/workflows/ci.yml`
的 `--ignore-vuln` 参数，定期复查，上游出新版本就把对应这条删掉）：

| 依赖 | 漏洞 | 为什么先不阻断 |
|---|---|---|
| `asyncmy` 0.2.10 | PYSEC-2026-286，SQL 注入（"通过精心构造的 dict key"） | 上游到最新版 0.2.11 仍未修复；本项目原生 SQL 只在极少数地方用固定参数名的 `text()`，不会把用户输入当 dict key 传给驱动，可利用面很小 |
| `chromadb` 1.5.9 | PYSEC-2026-311/3813/3814/3815，未授权访问、代码注入、跨租户越权 | 1.5.9 已是最新版，上游未修复；`docker-compose.prod.yml` 里 `chroma` 服务没有 `ports:` 映射，只有 api/worker 能从容器内网访问，不对公网暴露 |
| `ecdsa` 0.19.2 | PYSEC-2026-1325，Minerva 时序攻击（侧信道泄露私钥） | `python-jose` 的间接依赖；项目 JWT 固定用 `HS256`（对称算法），根本不会走到 `ecdsa` 的签名代码路径；上游明确表示侧信道防护不在其修复范围内 |

前端目前有 1 条 moderate（`echarts` XSS，`GHSA-fgmj-fm8m-jvvx`，修复需升到 6.x 大版本，有破坏性变更，
未安排）；`--audit-level=high` 不会拦它，`npm audit` 手动跑能看到。

发布前想看完整报告（含以上免检项，不只是阻断的那部分）：
```powershell
npm run audit:py
```
不带 `--ignore-vuln` 直接跑，看到的就是全部已知漏洞。

## 浏览器冒烟测试（7 个核心流程）

```powershell
.venv\Scripts\python.exe scripts\e2e_smoke.py                  # 无头跑一遍
.venv\Scripts\python.exe scripts\e2e_smoke.py --headed          # 弹出浏览器窗口，方便看
.venv\Scripts\python.exe scripts\e2e_smoke.py --keep-services   # 结束后不关前后端，方便手动排查
```

真实前端（`vite dev`）+ 真实后端（真实 FastAPI 路由、真实 MySQL、内嵌 ChromaDB）+ 真实浏览器
（Playwright + Chromium），串起：登录、创建 Agent、绑定知识库空间、发起 SSE 聊天并等回答生成完整、
查看 RAG 引用来源、普通用户绑定公开 Skill、管理员编辑并回滚 Skill——七个环节共用一套数据，
一次跑通就是对"这些功能真的接得上"最直接的证据。

**两处换成假实现**（`tests_e2e/fakes.py`），别的都走真实链路：

- 大模型：`service.tools.executor.create_langchain_llm` 换成 LangChain 官方自带的测试替身
  `FakeListChatModel`（固定回一句话），不发真实网络请求，不花钱。手写一个假 HTTP 服务器去精确
  模仿 LangChain `ChatOpenAI` 的流式协议试过，细节太容易对不上，改用官方测试替身后完全没有这个问题。
- 向量化：`service.rag.embedding_service._get_client[_async]` 换成本地哈希词袋 Embedding
  （中文按单字切、英文数字按连续串切，是词袋能匹配上的关键——整句当一个 token 切，两句几乎永远
  零重合）。ChromaDB 本身、检索排序、相似度阈值全部走真实逻辑。

后台任务（知识库文档入库）用 `TASK_EXECUTION_MODE=worker` + 进程内一个轮询线程跑
`service.background_worker.run_once()`——和真的独立 Worker 进程做一样的事，只是不用另开进程
（ChromaDB 内嵌 PersistentClient 要求全程只有一个进程碰它）。**踩过的坑**：一开始图省事用
`TASK_EXECUTION_MODE=inline`（FastAPI `BackgroundTasks` 直接在本次请求里跑），实测在这个项目的
中间件链下背景任务从来不执行，任务永远停在 `queued`——这条路径本来就只有"本地开发"在用、
生产和大多数本地开发也是起独立 Worker 进程，几乎没有真正被走过。

**这条测试顺手挖出的两个真实生产 bug**（不是测试环境特有的，已经在 `FasdtApi/chat.py` 修掉）：

1. 聊天接口（同步 / 流式两个路由）在收尾阶段重复访问 `current_user.id`。`AsyncSession` 默认
   `expire_on_commit=True`，本次请求中途只要有一次提交，`current_user` 这个 ORM 对象的所有属性
   就被标记为"过期"；流式响应收尾（生成器 `finally` 块）时才第一次真正触发这次过期后的隐式懒加载，
   如果这次访问发生在请求本来的 greenlet 上下文之外，SQLAlchemy 找不到桥接会直接
   `MissingGreenlet` 崩掉，前端看到"发送失败：服务暂时异常"。修法是在路由最开头把
   `user_id = current_user.id` 存成普通 int，后面全用这个值，不再重复读那个属性。
2. `agent_runtime.py` 的异常日志只打了 `error={e}`，遇到消息本身是空字符串的异常（比如这次的
   `NotImplementedError`）日志里什么都看不出来。顺手加上了异常类型和完整堆栈
   （`logger.error(..., exc_info=True)`），后续再出问题排查会快很多。

CI 里独立一个 `e2e` job（`.github/workflows/ci.yml`），失败时把失败截图和页面 DOM 快照
（`tests_e2e/.e2e_data/fail_*.png` / `.html`）打包成 artifact 方便下载查看。

## 企业化改造前的安全收口（Step 0）

在把项目往"单企业私有化部署"方向改造前，先核对了一遍现有能力开关的默认状态，
只发现一处真实缺口，已修：

- **Skill 导入/创建/编辑/删除/模板/版本回滚**：路由层已经全部是
  `get_current_admin_user`（[FasdtApi/skill_route.py](../FasdtApi/skill_route.py)），
  普通用户拿不到这些接口。不需要新增改动。
- **Skill 脚本目录越界**：[service/skills/loader.py](../service/skills/loader.py) 的
  `_ensure_managed_dir` 已经用 `realpath` 把 `resource_root`/`scripts_root` 限制在
  `skills/`、`skills_packages/` 下，同时挡 `../` 和符号链接逃逸；回归测试见
  `tests/test_skill_script_policy.py` 的
  `test_loader_refuses_scripts_root_outside_skills_packages` /
  `test_loader_refuses_resource_root_outside_managed_dirs`。这条是已修复的历史问题，
  不是仍然存在的漏洞。
- **脚本沙箱**：生产默认关闭的开关叫 `SANDBOX_ENABLED`（不是 `SANDBOX_ALLOW_USER_SCRIPTS`
  ——这只是命名，行为一致），`.env.production.example` 里已经是 `false`。
- **企业接口连接器（真正的缺口）**：`POST /agent/{agent_id}/api-connectors`
  （[FasdtApi/agent.py](../FasdtApi/agent.py)）原来只要求 `get_current_user`——任何
  普通用户都能给自己的 Agent 配任意（过 SSRF 校验的）外部 HTTP 接口。单企业部署下，
  "能不能接入外部系统"应该是管理员审核后统一配置的能力。新增
  [service/feature_flags.py](../service/feature_flags.py) 统一收口这类开关，
  `create_api_connector` 现在默认要求管理员，设
  `FEATURE_USER_API_CONNECTORS=true` 才放开给普通用户自助配置；前端
  [AgentApiConnectors.vue](../frontend/src/views/AgentApiConnectors.vue) 同步隐藏了
  非管理员看到的"新增接口工具"表单。回归测试：
  `tests/test_agent_api_connector_routes.py` 的
  `test_normal_user_cannot_create_connector_by_default`（默认 403）和
  `test_feature_flag_lets_normal_user_create_connector`（开关生效）；
  `tests/test_feature_flags.py` 覆盖开关本身的取值解析。

## 异步测试的 asyncmy 连接关闭噪音（已修）

跑异步测试时偶尔会看到一串 `Exception terminating connection` +
`AttributeError: 'NoneType' object has no attribute 'send'`（有时还带
`ResourceWarning: loop is closed`）。**不是真的连接泄漏**，是"关闭连接这个动作
发生在了错误的事件循环生命周期之外"：

- `models/async_db.py` 的 `async_engine` 是进程级单例，但测试里到处直接写
  `asyncio.run(coro)`——每次调用都新建一个事件循环、跑完就整个关掉。循环关掉后，
  连接池里还没被显式关闭的 asyncmy 连接要等某次垃圾回收才会真正尝试关闭，那时候
  早就没有活着的事件循环去驱动它的 `await` 了。
- 走真实 HTTP 请求的路由级测试（`rc.make_client()`）同理：只有 `with` 进入/退出
  （或手动 `client.__enter__()`/`client.__exit__()`）才会触发 `FasdtApi/main.py`
  的 `lifespan` shutdown，才会调用它里面的 `async_engine.dispose()`——建了
  `TestClient` 却不进入/退出上下文，这次请求开的连接就没人管。

统一修法：**在当前事件循环还活着的时候主动 `await async_engine.dispose()`**，不要
留给垃圾回收在不确定的时机处理。纯 `asyncio.run(coro)` 的写法改用
`tests/_async_helpers.py` 的 `run_async(coro)`（内部 `finally` 里 dispose，跑完
不管成功失败都清）；路由级测试统一用 `with rc.make_client() as client:` 或
`setUpClass`/`tearDownClass` 里成对的 `__enter__()`/`__exit__()`，不要只
`make_client()` 一下就直接用。

少数测试文件（`test_agent_runtime_async.py`/`test_agent_runtime_async_deps.py`/
`test_agent_runtime_stream_async.py`/`test_rag_search_async.py`）在这次统一之前
已经各自写过等价的 dispose 逻辑，工作正常，没有改动它们——不为了统一而重复造轮子。

## 路由级测试的数据清理

`tests/_route_client.py` 建的 `rt_*` 用户是**真实落库**的。清理机制：

- `cleanup()`（`tearDownClass` 调）—— 按本进程建的 id 删，级联清 agent / 知识库 / 空间 /
  组件 / 会话 / operation_log 等；**每条 DELETE 独立提交**，某表撞 FK 不会连累其它（历史上
  测试用户越积越多就是因为一条失败回滚了整轮）。
- 进程退出兜底：`atexit` 里再跑一次 `cleanup()`，`setUpClass` 崩了 / Ctrl+C 也不会漏。
- 手动清历史残留：`.venv\Scripts\python.exe scripts\purge_test_users.py --dry-run`（统计）/
  `--yes`（删掉库里**所有** `rt_%` 用户，真实用户不动）。

`scripts/e2e_smoke.py` 建的是 `e2e_*` 用户（同一套级联清理逻辑，见 `tests_e2e/fixtures.py::purge_e2e_data`，
内部直接复用这里的 `_purge_users`），每次跑完（不管流程成不成功）都会自动清一遍，不需要额外操心。

## 后续应补充

- 使用测试数据库覆盖注册、登录、权限隔离和管理员接口。
- 使用临时文件目录覆盖知识库上传、入库任务和删除。
- 使用 mock LLM/Embedding 服务覆盖聊天、RAG 检索和外部服务失败降级。
- 在 CI 中自动执行编译、单元测试和前端构建。
