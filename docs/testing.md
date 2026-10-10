# Testing

<!-- test-counts:start -->
**测试数量**（2026-10-10 由 `scripts/test_counts.py --write` 统计，不要手改）：Python `unittest` **2031** 条、前端 Vitest **126** 条、Java **143** 条。通过 / 跳过情况随运行环境变化（有没有 MySQL、Redis），以 CI 最近一次结果为准。
<!-- test-counts:end -->

项目使用 Python `unittest`、前端 Vitest（含部门助手组件交互测试）、Java JUnit，数量以上面的统计块为准（其他文档只链接到这里，不各写一个数）。纯逻辑单测（TTL 缓存、重试熔断、
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

## 测试用的 MySQL 和 Redis（一条命令）

没有 MySQL / Redis 时，依赖它们的用例会自动**跳过**——本地容易误以为“全绿”。起一套测试用的：

```powershell
docker compose -f deploy/test-services/docker-compose.yml up -d
$env:DB_HOST='127.0.0.1'; $env:DB_PORT='3307'; $env:DB_USER='root'; $env:DB_PASSWORD='ci-root-password'; $env:DB_NAME='agent_sql'
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
# 真实 Redis 的并发配额 / 缓存安全测试（单独跑；全量测试不要设 REDIS_URL，否则所有测试都会走 Redis）
$env:REDIS_URL='redis://127.0.0.1:6390/0'; .venv\Scripts\python.exe -m unittest tests.test_redis_concurrency
docker compose -f deploy/test-services/docker-compose.yml down -v
```

`tests/test_redis_concurrency.py` 在真实 Redis 上验证：很多线程 / 多个进程（模拟多个 API 实例）同时抢同一个并发名额，放行的数量必须恰好等于上限；
固定窗口限流计数准确且 key 一定带过期时间；Redis 里被塞进 pickle 载荷不会被执行。

## CI 质量门禁

每次 push / PR，GitHub Actions（`.github/workflows/ci.yml`）依次跑：编译检查 → ruff → 依赖漏洞扫描 → 漏洞豁免登记表核对 →
空库跑 Alembic + 模型漂移检查 → 单元测试 + 覆盖率 → （前端）依赖漏洞扫描 → Vitest → 类型检查 + 构建 → Java 测试 → 浏览器冒烟。任意一步失败，PR 不能合。

**漏洞豁免**：CI 里 `pip-audit` 忽略的每一条“暂时没有修复版本”的漏洞，都要登记在 `.github/vuln-exceptions.json`
（负责人、为什么不影响本项目、复查办法、到期日，最长 180 天）；`scripts/check_vuln_exceptions.py` 核对它和 `ci.yml` 一一对应，到期没复查就失败。
`.github/dependabot.yml` 每周为 pip / npm / Maven / GitHub Actions / Docker 开升级 PR，上游出了修复版本会自动提醒。

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

## 测试输出里的噪音（已定位并修复）

全量测试结束后，Windows 本机曾在统计行之后打印两行 `ResourceWarning: unclosed <socket.socket ... laddr=('127.0.0.1', 5xxxx)>`。
旧版本文档把它归因于 asyncmy 连接池 teardown，并决定不处理；这个结论是错的：

- 用 `socket.socketpair` 探针逐个排查后确认：**真正的来源是 `tests/test_sandbox_gateway.py`**。它启动了两个本地
  `ThreadingHTTPServer`，tearDown 只调了 `shutdown()`（停止服务循环），没有 `server_close()`（关闭监听 socket），
  所以两个监听 socket（只有 `laddr`、端口号相邻，正是警告里那两行）一直开到进程退出。已补上 `server_close()`。
  探针同时证明全量运行创建的 2700 多对事件循环自通知 socket 全部正常关闭——asyncmy 和事件循环都不是来源。
- 顺带修了一个真实的小问题：`service/background_worker.py` 常驻的 widget 事件循环从不关闭，会在解释器退出时报
  `unclosed event loop`；现在 `close_widget_loop()` 通过 `atexit` 关闭（循环正在别的线程运行时放弃）。
- 全量运行（`-W default`）现在没有任何 `ResourceWarning` 和弃用警告：`datetime.utcfromtimestamp()`
  已改为 `datetime.fromtimestamp(ts, timezone.utc)`；pydantic 的 `class Config` 已改为 `model_config = ConfigDict(...)`；starlette 的“TestClient 将改用 httpx2”提示是第三方库的升级通知，
  项目代码无可修改，只在 `tests/_route_client.py` 里按这一条消息精确过滤，其他弃用警告照常显示。

**测试日志**：此前一次全量运行有约 1100 行应用日志和 80 行工具注册打印，预期内的故障日志淹没了真正的失败。
- 工具 / 重排序注册由 `print` 改为 `logger.debug`（只进文件日志）。
- 控制台日志级别可由环境变量 `CONSOLE_LOG_LEVEL` 即时抬高（`utils/logger_handler.py::ConsoleLevelFilter`，不依赖导入顺序；
  文件日志仍完整记录）。`tests/_route_client.py` 默认把它设成 `CRITICAL`；要看细节：`TEST_LOG_LEVEL=INFO`。
- 全量结果始终看最后的 `Ran N tests ... OK`，数量见文首统计块（跳过的是依赖外部服务、本机没有时自动跳过的用例，其中 8 项是真实 Redis 并发 / 缓存安全测试，设置 `REDIS_URL` 才会跑，见下面“测试用的 MySQL 和 Redis”）。

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

## 测试怎么做到“在哪都能复现”

- **数据库探测只做一次，并且很快**：每个路由测试模块导入时都会问一遍“数据库能不能用”。以前每次都等操作系统级的连接超时（Windows 上约 20 秒 × 60 多个模块），MySQL 没起来时看起来就像测试卡死了。
  现在整个进程只探测一次，先用 1.5 秒的端口探测，失败信息里直接给出“一条命令起测试用的 MySQL”。
- **数据库“半通不通”也不会卡住测试**：探测用独立短连接并带连接 / 读 / 写超时（`utils/db_probe.py`）；`tests/test_db_timeouts.py` 用一个“接受连接但从不发握手包”的本机黑洞服务验证
  就绪探针按时返回 503、探针线程不残留、四个数据库引擎（同步 / 异步 / 审计）都带超时参数。
- **Vitest 不依赖磁盘上的临时文件**：`npm test` 带 `--configLoader runner`（在内存里处理 vite.config.ts，不写打包后的临时配置）和 `--no-cache`（不写结果缓存）。Windows 上的 `ENOENT` 都出在往磁盘写临时 / 缓存文件这一步。
- **TestClient 的弃用警告**：Starlette 的 TestClient 要求安装 `httpx2`（`requirements-dev.txt` 已经包含）。不再用过滤器把这条警告藏起来；缺了会直接看到。
- **Vitest 串行跑**（`fileParallelism: false`、`pool: 'forks'`）：几个 worker 同时写临时 / 缓存文件，在 Windows 和一些沙箱里会出现临时文件 ENOENT。CI 里还有一个 `frontend-windows` 任务在 Windows 上跑前端单测。
- **CI 里 Linux 才出现的问题**：OTel SDK 在 Linux 上给批处理器注册了 fork 回调，批处理器被回收后，之后任何一次 fork 都会报 `'NoneType' object is not callable`
  （`otel.shutdown()` 现在会保留已关闭的 provider）；新增的文件被 `.gitignore` 的 `.env.*` 静默排除（本地测试通过、CI 里找不到）。Windows 本地全绿不代表 Linux CI 全绿，两边都要看。

## 空库测试：推送前在本地复现 CI 的起点

```powershell
.venv\Scripts\python.exe scripts\test_fresh_db.py          # 和“空库”最相关的一组（约 1 分钟）
.venv\Scripts\python.exe scripts\test_fresh_db.py --all    # 全量，和 CI 一样
```

建一个临时库 `agent_sql_fresh_<时间戳>` → `alembic upgrade head` → 所有表的自增起点抬到 9 亿 → 跑两次 `bootstrap_database`（验证幂等）→ 把 `DB_NAME` 指向它跑测试 → 删库（`--keep` 保留排查）。
抬自增起点是因为提示词、专业技能配置等文件按 id 命名、和开发库共用目录：空库里的 1 号助手会覆盖并在清理时删掉开发库 1 号助手的文件（踩过一次，已从 git 恢复）。
已经接进 `npm run release:check`（账号没有建库权限时加 `--skip-fresh-db`）。

## 测试问题复盘（2026-10）

| 问题 | 为什么本地没发现 | 改了什么 |
|---|---|---|
| CI 的 backend 连续几轮失败：库里还没有企业记录时，用户管理、企业知识库、企业智能体列表直接报错 | 本地开发库一直有企业记录，跑多少遍都绿；CI 每次是空库 | 列表在没有企业时返回空（`find_default_organization`），写操作才要求企业存在；加 `tests/test_no_enterprise_yet.py`；加空库测试脚本并接进发布自检；首次启动自动建企业 |
| 空库上 `rc.create_user(..., admin=True)` 建的不是管理员 | 开发库里测试账号恰好有别的管理员兜底 | 测试统一用 `rc.admin_env(name)`；这是测试写法问题，不是产品问题 |
| 删除企业助手后留下没有主人的技能记录和 `skills/enterprise/agent_<id>.yml` | 没有任何页面会列出“孤儿”，只有数据体检查出来（开发库里 50 个文件） | `agent_service.delete` 连同专属技能一起删；测试清理也删文件；体检常驻这两项检查 |
| 发布前端后部分浏览器白屏，换浏览器才能进 | 本地开发服务器不缓存；只在 nginx + 旧标签页上出现 | `/assets/` 找不到返回 404 而不是 `index.html`，`index.html` 不缓存，前端加载旧文件失败时自动刷新；`tests/test_nginx_delivery.py` 用正则核对三份配置，并在真实 nginx 容器里验证过响应头 |
| 用着用着被登出 | 测试会话都很短 | 滑动续期 + 最长时限；后台轮询不算“在用”；`tests/test_session_renewal.py` 走真实 HTTP 验证续期后带新 CSRF 的写请求能成功 |
| 中文输入法选词按回车直接把半句话发出去 | 自动化测试不会触发输入法组合事件 | `isImeEnter` 守卫 + 一条扫描所有输入框的测试 |
| 第一次跑空库测试后，`prompt/prompts/1.yaml`、`5.yaml` 被删了 | 空库的 id 从 1 开始，按 id 命名的文件和开发库共用目录 | 从 git 恢复；空库建表后把自增起点抬到 9 亿；跑完看一眼 `git status` |
| 修完“空库列表报错”后，本地全量有 9 条失败（CI 是绿的） | 列表改用 `find_default_organization`，但这几个测试只替换了 `_get_default_organization`；CI 空库里测试自己建的企业恰好 id 最小，所以碰巧对 | 测试改为替换最底层的 `find_default_organization`（另一个也调它）；教训：改了“取数的入口”，要搜一遍测试里替换的是哪个入口 |
| 首次启动自动建企业：`GET_LOCK` 没检查返回值（评审发现） | 单进程测试永远拿得到锁 | 没拿到锁就跳过；数据库哨兵行主键兜底；6 线程并发测试（去掉兜底时建出 6 家企业） |
| 部门助手卡片：一次办理超过 5 张单子时卡片超过上限（评审发现） | 只测了“长列表”和“办理优先”，没测“办理本身超过 5 张” | 上限对任何组合都成立，超出的计入“没展开”数量 |
| 旧页面自动刷新：sessionStorage 不可用时每次都刷新，可能死循环（评审发现） | 测试里存储总是可用 | 依次记在 sessionStorage → window.name → 内存；哪儿都记不下就不自动刷新 |
| 每跑一次全量，`skills/enterprise/` 就多两个孤儿配置文件 | 文件不在数据库里，测试断言看不到；数据体检的数量在涨才发现 | 逐个模块跑定位到 `test_agent_config`、`test_agent_routes` 按名字删助手没删文件；抽出 `tests/_dept_agent_cleanup.py::purge_agents`（连同专属技能和文件），今天跑测试留下的 21 个已清理 |
| 在 Windows 上导入的技能，部署到 Linux 容器后全部“资源不存在” | 开发和测试都在同一台 Windows 上，路径永远存在 | `tests/test_skill_portable_roots.py` 直接写别的机器的 Windows / Linux 绝对路径验证能换算，并验证越权路径仍被拒绝；另外在容器里对 76 个真实技能做了新旧对比（13 → 76 可用） |
| 跑完测试，`prompt/prompts/1.yaml`、`5.yaml` 又被删了（这次没走空库脚本） | 直接在空的测试库（`deploy/test-services`）上跑全量，助手编号从 1 开始，抬自增起点的办法管不到 | 测试一律用临时提示词目录（`tests/_route_client.py` 导入时切换），`tests/test_prompt_isolation.py` 守护；以前测试留下的 3009 个孤儿提示词和 41 个孤儿技能配置用 `scripts/archive_orphan_agent_files.py` 归档 |
| `test_organization_admin_service` 单独在空库上跑全挂 | 全量跑时前面的测试顺带建了企业，掩盖了它依赖“库里已有企业” | 模块级 `setUpModule` 没有企业就临时建一家，跑完删掉 |
| 数据体检的断言在开发库上失败：自己造的孤儿文件不在前 5 条样例里 | 开发库里本来就有别的孤儿 | 测试里放开样例数再断言；体检本身只给 5 条样例是对的 |

几条规律：**开发库里的“现成数据”会掩盖问题**（空库测试解决）；**部署层的问题测不到代码里**（直接解析 nginx 配置 + 真实容器验证）；
**每个修复都做反向验证**——把修复去掉，对应测试必须失败，否则测试没有测到点子上。

## 本机跑全量测试前：先停掉 Worker

需要数据库的测试和本机开发共用同一个库。本机的后台 Worker 在跑时，会抢走测试里刚写入的事件和批量任务（`test_outbox`、`test_automation_batch`），
这几条在没有任何改动的基础提交上也会失败。
CI 用全新数据库，不受影响；本机想看到全绿，先停掉 `npm run backend:worker`，并用测试专用库（`deploy/test-services/docker-compose.yml`）。

## 前端单元测试（Vitest）

```powershell
npm --prefix frontend test
```

覆盖：登录会话（令牌在 HttpOnly Cookie 里，页面只看得到 `csrf_token`）、请求拦截器（带 Cookie、改数据的请求带 `X-CSRF-Token`、从不发 Authorization、401 回登录页）、
user store（登录不保存令牌、`isLoggedIn()` 不被缓存、退出登录先让后端清 Cookie）、Markdown 安全渲染（脚本 / 事件处理器 / 钓鱼表单 / 外链图片等载荷）。
更完整的浏览器级验证见 `scripts/check_session_browser.py`（登录 Cookie、CSRF、CSP 真的拦截）和 `scripts/check_xss_browser.py`。

### 部门助手的组件交互测试

用 `@vue/test-utils` 挂载真实组件，接口用替身按真实事件顺序推送（不需要模型和业务服务）：

- `frontend/src/components/EmbeddedAgentChatPanel.test.ts`：确认高风险操作后卡片立刻从草稿变为待审批、步骤打勾、小结写“已办成”；
  确认后执行失败 / 取消的显示；流里返回错误、连接中断、工具返回错误时步骤标红而不是“完成”；切换部门清空对话并开新会话；中文输入法选词的回车不发送。
- `frontend/src/components/department/DepartmentAgentHub.test.ts`：今日发现的数量与风险；一键生成凭证草稿全部成功 / 部分失败（逐笔写原因）/ 全部失败（标红、不刷新不跳转）、
  处理期间不能重复点；接最急的一张工单（接队列第一张并打开）、没有可接的、服务端拒绝；切换部门（包括同为销售类型的“销售一部 → 销售二部”）清空材料和一键处理结果；
  一键处理途中切走部门，旧任务作废（不再对旧部门继续调用、不把结果显示到新部门）。
- `frontend/src/utils/agentCards.test.ts`：整次回答最多 5 张卡片（多个列表累计计算），办理产生的单子优先显示不被列表挤掉。
- `frontend/src/utils/ime.test.ts`：守卫——所有回车提交的输入框都必须先判断输入法（`isImeEnter`），不能用 `keyup.enter`；新加输入框忘了判断会失败并指出文件。

这几组测试加的时候都做过反向验证：把对应的修复去掉，测试会失败。

### 界面上不出现代码里的名字

页面上不显示函数名、工具名、枚举值、模型标识、部门代码、事件名、配置项名字。所有“内部名字 → 中文”的转换都集中在 `frontend/src/utils/displayNames.ts`，
不认识的值用通用说法兜底（“自定义工具”“后台任务”“配置检查”），绝不原样显示。两道守门：

- `frontend/src/utils/displayNames.test.ts`（Vitest）：每个转换函数、兜底说法、中文名本身都不像代码。
- `tests/test_frontend_display_names.py`（unittest）：后端每注册一个内置工具 / 内置模型 / 诊断检查项，前端名表里必须有对应的中文名；
  页面模板不能把 `tool_name`、`task_type`、`department_code`、`event_type` 等内部字段直接打印出来；写死的 `placeholder` / `title` / `aria-label` 里不能带 snake_case。

新增内置工具、模型或诊断检查项而忘了加中文名，后端单测会直接失败。
另外，应用外壳是 `h-dvh` + `main overflow-hidden`，每个页面必须自带滚动容器；页面模板根节点前不能有 HTML 注释（会变成多根片段，路由过渡会卡住并留下空白的 `<main>`）。

## 后续应补充

- 使用测试数据库覆盖注册、登录、权限隔离和管理员接口。
- 使用临时文件目录覆盖知识库上传、入库任务和删除。
- 使用 mock LLM/Embedding 服务覆盖聊天、RAG 检索和外部服务失败降级。
- 在 CI 中自动执行编译、单元测试和前端构建。
