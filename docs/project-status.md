# Project Status

本项目当前处于功能完善后的上线前工程化阶段。核心业务链路已经具备，但还需要继续补齐生产安全、测试、部署验证和可观测性。

## 当前验证状态（2026-10-07，以此为准；下文的历史数字是当时的快照）

- **Python 全量**：1825 项通过、14 项跳过（含 8 项需要真实 Redis 的并发 / 缓存安全测试，设置 `REDIS_URL` 才跑）（`python -m unittest discover -s tests -p "test_*.py"`），`-W default` 下没有警告；
  **Java**：143 项通过（`enterprise-business-hub` 里 `mvn test`，跑之前先停掉在 8090 的 Java 进程）；前端 Vitest 32 项、`vue-tsc` 与构建通过；CI（backend / frontend / java / e2e）全绿。
- **真实端到端**（真实 FastAPI + MySQL + Java，模型为确定性替身）：责任协同 117、人事 47、IT 64、财务凭证 43、AI 整理 30、考勤 51 项，
  外加真实浏览器流程（`scripts/e2e_*_browser.py`）和故障演练（`drill_java_down.py`、`drill_batch_restart.py`）。
- **本阶段新增能力**：部门责任执行、文件导入与批量整理、事务性发件箱/收件箱/死信、trace_id 与统一错误、问题中心、
  页面脚本错误上报、Agent 运行失败追踪、试点数据与预检、考勤异常发现（见 `docs/agent-productivity-workflows.md`）。
- **安全专项**（`docs/security-testing.md`）：SSRF、恶意文件、SQL 注入、XSS、提示注入与工具越权、跨部门 / 跨企业越权、重复与并发都有会失败的测试；
  过程中发现并修复了跨企业提权（企业 B 的管理员能审批企业 A 的请假、批准企业 A 的空间删除）、请假 / 采购 / 报销的并发重复处理与预算 / 余额覆盖、SSRF 的 5 类绕过、压缩炸弹等。
- **代码审查后的收口**（2026-10-07，见 `docs/security-testing.md`）：Redis 并发配额改成原子操作（并修了 Redis 客户端并发首次连接时各线程退回内存限流的竞态）、缓存不再用 pickle 且 Redis 里只存模型密钥的密文、登录令牌改放 HttpOnly Cookie + 签名 CSRF 并上线内容安全策略、知识库上传加单文件 / 累计 / 个数上限。同时加了前端 Vitest、漏洞豁免登记表（负责人 / 到期日）、Dependabot、Docker 与直接部署两份生产环境模板、测试用 MySQL / Redis 一条命令。
  **还没做**：OIDC / SAML 单点登录、SCIM / LDAP 同步、管理员 MFA、短期令牌 + 轮换刷新令牌、真实 SAP 连接器、第三方渗透测试、生产规模容量基准与 RTO / RPO 演练。
- **真实基础设施联调**（`docs/observability-integration.md`）：Kafka（KRaft 单节点，消费者组 / 手动提交 / 重试 / 死信 Topic，`scripts/e2e_kafka.py` 28 项）、
  OpenTelemetry Collector → Tempo / Loki、Prometheus、Grafana（`scripts/e2e_observability.py` 40 项，含 Collector / Loki / Tempo 中断后恢复、Sentry 未配置与不可达）。
  联调中发现并修复了 span 全被丢弃、成功请求没有日志、`/metrics` 被 Host 白名单拒绝等问题。
  **仍未验证**：真实的云日志账号和 Sentry 项目（没有账号 / DSN，只验证了“未配置或不可达不影响业务”）。
- **没有真实用户数据**：试点工具齐全（`scripts/pilot_preflight.py`、`scripts/pilot_report.py`），但还没有真实试点样本。

## 已完成能力

- 用户系统：注册、登录、当前用户、修改密码、账号禁用。
  **认证安全（token 版本号）**：`user.auth_version`，JWT 里带着签发时的版本号（`ver`），鉴权时两边一对，
  不相等直接 401「登录状态已失效」——不用等 token 自然过期。改密码 / 短信重置密码 / 管理员重置密码
  （`models/user_async_dao.py::update_user_password_async`，用 `auth_version = auth_version + 1` 原地自增，
  和"强制下线"并发也不会丢更新）、管理员强制下线（`POST /admin/users/{id}/revoke-sessions`）、
  用户自己退出所有设备（`POST /user/logout-all`）都会让旧 token 立即失效；普通"退出登录"不牵扯这套，
  前端删本地 token 就够了。统一密码策略 `service/password_policy.py`（10~72 位、不等于用户名/手机号、
  不是常见弱密码、不能和旧密码相同），注册、改密码、找回密码、管理员重置密码四个入口共用同一份判断。
- 管理员后台：用户管理、任务管理、使用统计、操作日志、系统诊断。
- Agent 管理：创建、编辑、复制、选择、删除预检、调试。
- 聊天系统：同步聊天、SSE 流式聊天、会话管理、消息历史、会话导出。
- 模型配置：用户级 API Key、模型列表、自动适配模型 URL、配置测试。
  搜索式模型（Perplexity `sonar` / OpenAI `*-search-preview`）已进目录，供组件「联网检索」数据源用。
  **一次连接，多项能力（普通模式）**：选平台（智谱 / DeepSeek / OpenAI / Kimi / 通义 / Perplexity）+ 粘一次 Key
  → `POST /llm_config/quick_connect` 一个事务里配好「回答问题」+「读取资料」两条配置并逐条测试
  （`service/llm/model_catalog.py::PROVIDER_QUICK_DEFAULTS`；平台不支持资料读取时只配聊天并回报 skip）。
  前端 `LlmConfig.vue`「一步开启」用默认模型时走这个原子端点，改过「技术人员选项」才退回逐条保存。
- Skill 系统：创建、编辑、模板、导入、导出、校验、绑定 Agent。
- 知识库：上传、批量上传、切块、Embedding、检索、片段查看、启停、重建、删除。
  **切块大小可调**：上传 / 重建时传 `chunk_size`（120~2000，落 `knowledge.chunk_size`），入库按文档自选值切分；
  前端「知识库」上传区「切块设置（高级）」输入框。
  **RAG 上下文压缩 / Token 节省统计**（`service/rag/rag_stats.py`）：原文档总字数 / 召回片段数 /
  送入上下文字符数 / 估算省 Token 比例 —— 挂在 `search_for_agent_async` 结果、`retrieval` SSE 事件、
  `retrieval` 运行步骤审计；前端 `RagSavingsBar.vue` 共用组件，调试台快照与聊天页（检索轨迹行 +
  回答下方 compact 版）都展示。
- 知识库空间（阶段1-6，完整）：`/knowledge-spaces` 空间 CRUD + 空间内文档管理，
  `knowledge.space_id` / `agent.kb_*` 字段，向量集合键 `agent_{id}` / `space_{id}` 双制式，存量迁移脚本。
  Agent 可绑定多个空间做联合检索，回答带 `【来源N】` 引用来源，`kb_refuse_when_empty` 无命中拒答，
  未绑定空间的旧 Agent 回退原私有库路径。知识库调试台（`/knowledge-spaces/:id/debug`）：
  跑一次检索看命中片段/上下文/回答/忠诚度，存为测试样例并勾进评估集、导出成 `rag_eval` 用例。
  健康报告（`/knowledge-spaces/:id/health`）：文档失败/过期/未入库率 + 检索命中/拒答/引用率 → 健康分 0~100，
  可按评估集跑一次 RAG 评估；Widget 平台新增「知识库健康」数据源。
  **企业权限**：`space_members` 角色（owner/admin/editor/viewer）分级写权限，`kb_audit_log` 审计，
  `/admin/knowledge-spaces` 管理员企业视角；隔离全部收敛在 `access_control` 的三个函数里。
  前端「知识库中心」/「空间详情（含成员与权限、操作日志）」/「调试台」/「健康报告」页 +
  Agent 编辑「知识库」区块 + 聊天页「参考来源」+ 管理员「企业知识库」。
- 前端信息架构整合：侧栏「主要功能」收为 6 项（工作台 / 知识库中心 / 小窗口与监控 / 技能中心 / 任务中心 / 设置），
  「模型连接」并入设置、「网页监控」并入小窗口（页内 `SectionTabs` 切换），补上任务中心入口。
  单个助手的「聊天 / 知识库 / 记忆 / 运行检查」统一收在 `/agents/:id/*` 下，页内用 `AgentSubnav` 切换，
  侧栏只留一个「助手空间」入口；旧路径 `/chat/:id` 等做重定向，后端接口不变。
- 网页抓取：支持输入公开 URL 抓取正文，保存为 Markdown 后进入知识库后台入库流程，并带基础 SSRF 防护。
- 自定义工作台组件平台：**模板优先**（选模板 + 填字段 → `from-template` 拼 spec，不走大模型，
  10 个模板：知识库健康/检索抽查、我的用量/概览、内部接口表格/数值告警、网页监控/快照、每日 AI 简报、联网查指标），
  自然语言（designer 调用户模型）降级为「高级」入口。统一运行引擎取数/处理/存快照 + 前端统一渲染器。
  - 数据源：内置示例、平台数据服务、我的运行统计、外部 HTTP、网页正文/更新监控、我的知识库、
    **联网检索**（无现成数据源时，用用户已配置的联网模型去查，`service/llm/web_search.py` 按 provider 开开关）。
  - 处理器：归一时序、字段挑选、聚合、JSON 取值、AI 摘要、阈值告警。
  - 到点自动调度：后台 Worker 乐观锁抢占，默认开启；失败指数退避、连续失败自动暂停；数据点保留策略。
  - 展示增强：时序/类目数据自动升级图表，创建前试运行，导出/导入配置，需关注标记。
- 配额阈值提醒：每次聊天运行结束后（`service/quota_service.py::check_and_notify_threshold_async`），
  如果本月用量因为这次运行跨过 80%/95%/100%，复用告警 Webhook 通道推一条提醒——只在
  "上次检查还没到、这次跑完到了"那一刻推送一次，不会每条消息都刷屏。和硬拦截（超额 429）
  是互补关系：提醒让用户在真被拦下来之前就有心理准备。
- 计费/配额体系（MVP）：`plan` 套餐（每月 Token 配额，0=不限量）+ `user_subscription` 一人一订阅，
  未订阅回退管理员配置的默认套餐。`service/quota_service.py::enforce_quota_async` 在
  `/chat/{id}` 与 `/chat/{id}/stream` 真正调用 Agent/LLM 之前拦截超额请求（`QuotaExceeded` 429），
  避免钱已经花出去了才发现超额。管理员后台「套餐配额」页做套餐 CRUD + 默认套餐切换，
  「用户管理」列表内联下拉分配套餐；用户侧 `GET /user/quota` + 工作台「本月用量」卡片
  （进度条按 70%/90% 变色）。范围有意收窄到「每月 Token」这一种资源，Agent 数/知识库空间数等
  维度限额留作后续按需扩展。
- 告警外部推送：`notification_channel` 表（用户可配多个 Webhook：飞书/钉钉/企业微信/Slack 自定义机器人
  或自建接收端），出站 URL 复用 `web_crawler_service.validate_crawl_url` 做 SSRF 校验。组件的
  `threshold_alert` 处理器给出 level=warn/alert 时（`service/widgets/runner.py::_maybe_dispatch_alert`），
  只在等级**变化**那一刻推送（升级/降级/恢复正常），避免持续告警期间每次调度都刷屏；
  `user_widgets.last_alert_level` 记录上次等级做去重。推送 best-effort，失败不影响组件运行本身。
  用户侧「设置」页「告警通知」区块：增删通道、启停、发测试消息。
- Agent 工具生态：内置工具从 4 个扩到 7 个——新增 `calculator`（AST 白名单沙箱，非 eval，
  拒绝任意代码注入）、`datetime_calculator`（日期差/加减/星期几，默认北京时间）、
  `unit_converter`（长度/重量/面积/体积/温度，不做汇率换算避免过期数据）。
  `service/tools/` 目录零配置自动扫描注册（`@ToolRegistry.register`），新工具无需改
  任何路由/Skill 校验代码即可在 Skill 创建页的工具选择器里直接可选。
- 报表导出（CSV）：管理员「使用情况」「操作日志」页新增「导出 CSV」（日志导出按当前筛选条件，
  上限最近 500 条）；用户工作台「本月用量」卡片新增「导出」，导出套餐概况 + 本月运行明细。
  统一走 `utils/csv_export.py`（UTF-8 BOM，避免 Excel 中文乱码）；前端 `utils/download.ts`
  用 axios（带鉴权头）+ blob 触发浏览器保存，不能用裸 `<a href>`（拿不到 Authorization 头）。
  会话导出（markdown/json）此前已支持，不在本次范围内。
- Agent 协作（流水线，MVP）：`agent_pipeline` 表把多个 Agent 串成固定顺序的处理链——
  上一步的回答自动作为下一步的输入消息。范围有意收窄成"线性串行"，不做分支/条件/并行，
  真正的 DAG 工作流引擎留到有明确需求信号再做。每一步都走和 `/chat/{id}` 完全一样的
  `chat_service.chat_with_agent`，配额检查/Token 记账/会话历史全部免费复用——流水线
  不能绕开配额限制。`/pipelines/{id}/run` 复用聊天的限流桶和并发守卫。某一步失败
  （助手被删、配额用尽、模型报错）就停在那一步，返回已完成的部分结果，不整体报错。
  **过程中顺手修了一个独立的、影响全部智能体聊天的生产 bug**：`create_langchain_llm`
  给 LangChain 传的 `base_url` 之前会带上 `/chat/completions` 后缀，LangChain 自己又会拼
  一次，导致所有绑了工具/技能、走 ReAct 引擎的智谱模型 Agent 每次对话都 404——这个 bug
  在流水线做真实联调时才被发现，因为之前的会话大多是较早期的手工验证，没有真的跑完
  一次绑了工具的智谱 Agent 全链路对话。
- 长期记忆：按 Agent 管理长期记忆。
- 缓存：Redis 优先、内存兜底。
- 访问管控：聊天限流、知识库限流、并发控制、后台任务配额。
- 后台任务：独立 Worker 模式，支持任务领取、执行、失败记录、超时重新排队。
- 数据库连接池：已配置池大小、溢出连接、等待超时、连接回收和断线探测。
- 数据库索引：已按主要查询路径补首轮组合索引和幂等迁移。
- 压力测试：已提供轻量 HTTP 压测脚本和使用文档。
- 监控基础：已提供 Prometheus `/metrics`、请求耗时/状态码指标、连接池/缓存状态指标（可接入现有 Prometheus/Grafana）。
- 安全边界：已配置 CORS、Trusted Host、安全响应头、请求体大小限制和 Nginx 安全响应头。
- 熔断/降级：LLM 与 Embedding HTTP 调用已统一超时、重试、指数退避和熔断；熔断状态配了
  `REDIS_URL` 时多个 API/Worker 实例共享同一份（`service/http_resilience.py::CircuitBreaker`，
  跟 `utils/rate_limit.py` 一样的"Redis 优先、异常/未配置就掉回进程内 Map"模式），
  没配或 Redis 不可用时退回原来的进程内状态，不是新增的硬依赖。
- 配置校验：生产环境启动时会拦截占位密钥、缺 Redis、短信误配置、CORS/Host 未收紧等问题。
- 自动化测试：单元测试覆盖缓存、验证码、模型 URL 适配、HTTP 重试熔断、生产配置校验，组件平台
  全链路（连接器 / 处理器 / 调度 / 退避 / 保留 / 形态识别 / 导出导入 / 同步异步边界守卫）；
  真实路由级测试（`tests/test_routes_isolation.py`：TestClient + 真实 JWT + 真实 DB）覆盖登录鉴权、
  widgets / knowledge / agent / skill / conversation 的跨用户 404 隔离、后台任务管理员 403。
- 发布自检：`npm run release:check` 检查上线关键文件、组件平台核心模块、`deploy/` 监控配置可解析、
  `deploy/` 无残留容器主机名、组件调度已接入 Worker，再跑编译 + 单元测试 + 前端构建。2026-09-28
  修了几个引用已归档旧迁移文件（`migrations/versions/20260830_0001_baseline.py` 等 4 个，2026-09-24
  重新定基线后挪到了 `migrations/archive_pre_baseline/`）的过期文件路径，实际跑一遍完整
  `scripts/release_check.py`（含全量测试 + 前端构建）确认真的能走完，不再是失败状态。CI
  （`.github/workflows/ci.yml`）新增 `java` job：真实 MySQL 服务容器 + `mvn test` 跑
  `enterprise-business-hub` 的 35 个测试（当时的数字，现为 138 个），之前 Java 权限逻辑改坏了没有任何自动化能拦截。
- 备份恢复：部署文档给出本地 MySQL `mysqldump` 导出 / 恢复命令与应用文件目录清单。
- 数据库迁移：已加入 Alembic 迁移骨架、基线版本和迁移文档，当前处于兼容过渡期。
- 部署基础：阿里云 ECS 上走 `docker-compose.prod.yml`（db / redis / chroma / api / worker 容器化，
  MySQL 为自建容器、备份需自己负责）；前端 `npm run build` 后由宿主机 Nginx + certbot 托管并反代 `/api`、
  `/health`、`/metrics`。原生方案（`deploy/systemd/*.service` 常驻 uvicorn + worker）保留为备选，
  `deploy/` 提供 nginx / prometheus / grafana 模板。根目录 `docker-compose.yml` 仅用于本地演示。
- 前端视觉：整体改为 Apple 风格（中性灰阶 + 唯一强调蓝 `#0071e3`、毛玻璃侧栏、悬浮液态玻璃输入框、
  连续圆角、弹簧动效、大标题）。设计令牌与 Tailwind 色阶/圆角/阴影重映射集中在 `frontend/src/style.css`；
  `sci-*` 星空/轨道/发光样式已删除，通用类为 `ui-card` / `ui-panel` / `ui-field` / `ui-primary` /
  `ui-glass` / `ui-glass-float`。字体走系统字体栈，Inter 通过 `@fontsource-variable/inter` 自托管（不依赖外网）。
  深色模式：`utils/theme.ts` 在 `<html>` 写 `data-theme`（首次跟随系统，手动切换后记住；`index.html` 首屏前先定主题防闪白），
  做法是在 `style.css` 里翻转重映射过的色阶变量 + 少量写死 `bg-white` / `bg-black/[.xx]` 的覆盖；
  切换按钮在侧栏底部、后台顶栏、登录页右上角。助手回答用 `.md-body` 排版（列表 / 代码 / 表格 / 引用）。
  新手指引（`stores/onboarding.ts` + `components/onboarding/`）：只有"什么数据都没有"的新用户登录后才弹欢迎页，
  之后是 4 步清单（连接模型 → 创建助手 → 添加资料〔可选〕→ 聊一句），完成判断全部来自 `/user/dashboard` 的计数，
  每 4 秒轮询；某一步做完会打勾并自动跳到下一步的页面，目标控件（`data-guide` 标记）脉冲高亮。进度按用户存在
  浏览器 `localStorage`（`onboarding:v1:<userId>`），老用户静默标记为已完成；侧栏"更多 → 新手指引"可随时重开。
  手机端（< 768px）：用户端与后台都是"顶栏 + 抽屉导航"；对话页会话栏变抽屉；后台任务 / 日志这类多列列表
  横向滚动；右下角"当前助手"浮窗在手机上隐藏（换助手走工作台）；`viewport-fit=cover` + 安全区留白。
  技能中心导入（`service/skills_core/package_import.py` / `github_import.py`）：兼容 Anthropic 官方 Skill
  （`SKILL.md` + YAML 头）、整个 GitHub 仓库 zip（多 Skill 批量）、GitHub 链接（仓库 / 文件夹，固定走
  codeload.github.com，链接只提取参数不当请求地址）、本平台能力包和 yml；导入结果逐条告知哪些被忽略。
  **脚本沙箱（默认关闭，`SANDBOX_ENABLED`）**：`sandbox/`（独立容器，无外网、无密钥、只读根、资源上限）+
  `service/sandbox.py`（可替换的后端接口）+ `service/tools/skill_script.py`（`run_skill_script` 工具，绑定时
  仅在沙箱开启且 Skill 带 .py 脚本时才自动加进工具列表）。配套：聊天附件上传 / 生成文件下载
  （`service/attachment_service.py`、`/attachment`，按用户隔离、默认保留 7 天）。上架与可用性：技能只有管理员能维护（创建 / 编辑 / 删除 / 导入 / 上架，路由要求管理员，导入策略再按角色判断一次），普通用户只能看商店并把技能直接绑定到助手（商店里的都标「官方」，所有用户绑定同一份配置，所以编辑前自动存快照、最近 20 版可在后台一键回滚）；
  导入时对每个 .py 做静态兼容性检查（缺依赖 / 要联网 / 调系统命令，`service/skills_core/script_report.py`），
  卡片上标「脚本可运行 / 部分可运行 / 脚本暂不支持 / 脚本需管理员启用沙箱」，助手只会被告知能跑的脚本；
  每用户脚本限频、附件总量上限、`sandbox_audit` 审计日志。预制包 `skill-packs/anbeime-skills-all.zip`（75 个）导入后：
  39 个带脚本，其中 15 个全部可运行、13 个部分可运行、11 个暂不支持（缺依赖或要联网）。
  **容器层面的隔离本地没测过**
  （开发机 Docker 未运行），部署后按 docs/deployment.md 第 7 节的两条命令验证。

## 当前验证结果

- `python -m compileall` 通过。
- 前端 `npm run build`（含 vue-tsc 类型检查）通过。
- FastAPI 应用导入与路由生成通过。
- `python -m unittest`：860 通过（含真实路由级测试 + agent_runtime / RAG 检索 async 链路、
  quick_connect、chunk_size、RAG 节省统计、web_query 联网检索、工作台模板、计费配额、
  配额阈值提醒、告警推送、新增 Agent 工具、报表导出、知识库 OCR/Excel、LLM 客户端 SSRF
  防护、Agent 流水线、LangChain base_url 拼接测试、Phase 3D 企业 RBAC/审批/审计/中央路由
  测试、Phase 5 OA/采购/CRM 工具测试，需本地 / CI MySQL）。
- Phase 3D（企业化：组织/部门/RBAC，见 [docs/enterprise-rbac-plan.md](enterprise-rbac-plan.md)）：
  阶段1-6 全部完成——资源归属与密级字段、统一授权层覆盖聊天/检索/Skill绑定、高风险操作
  审批（知识库空间删除已接入）、乐观锁/发布生命周期字段、通用审计表、中央 Agent 受控路由
  （关键词规则匹配到用户可用的部门 Agent，HR/采购/销售部门现在都有真实 Agent 可路由）。
  2026-09-28 修了两个 P1：`POST /agent` 不再接受客户端传的 `agent_type`/
  `department_code`（之前任何用户都能自称"部门 Agent"，现状下无跨用户危害但语义
  不该允许）；多部门用户的 `team_id` 推导改成优先用当前部门 Agent 自己的
  `team_id`，不再是"在职的第一个部门"，见 [enterprise-rbac-plan.md](enterprise-rbac-plan.md)
  第15/16节。企业组织管理后台已补齐（`/admin/org/*` + `/admin/organization` 页面）：
  部门增删改、成员分配/改角色/移出、部门负责人设置、企业角色调整、部门权限关系
  查看，之前只能靠迁移脚本或直接改库，见第17节。
  2026-09-28 补了一个 P0：组织后台上线后立刻发现"停用部门/企业"完全不生效——
  鉴权链路（`enterprise_access.py`/`enterprise_dao.py`）只查
  `team_members.status`/`organization_members.status`，没查 `teams.status`/
  `organizations.status`，成员权限在部门被停用后仍然有效。已修复并补齐"停用即
  失效"的端到端测试。同时修了平台审批的两个洞：`decide()` 禁止自审批、
  `try_consume_approved()` 从"先查后改"改成条件 UPDATE 防止并发重复消费
  （真并发测试验证）。CRM 也补上了第16节的多部门 `team_id` 修复（之前漏了）。
  见第18节。Skill 的发布生命周期（draft/reviewing/published/retired）也接上了
  强制逻辑：非作者只能绑已发布的 Skill，运行时跳过已退役的绑定；`row_version`
  乐观锁用条件 UPDATE 实现（不是先查后改）。Agent 那一半也接上了：路由只认
  `lifecycle_status='published'` 的央/部门 Agent；新增 `/admin/org/agents` +
  `AdminOrganization.vue` 第三个 tab，中央/部门 Agent 的创建/绑部门/发布/停用
  终于有了管理入口，不用再直接改数据库——"中央 Agent 管理部门 Agent"这个目标
  的最后一块缺口补上了。见第20节。组织后台的写操作（建部门、分配成员、改角色、
  建/发布 Agent）现在都写审计了，之前完全没有留痕；企业最后一个 owner 不能被
  降级/停用/移除。2026-09-28 又补上了审计表账号最小权限：审计写入（Python
  `audit_event` + Java enterprise-business-hub 自己的 `audit_event`）现在各自
  走一个独立连接，接一个只被授予该表 INSERT/SELECT 权限的专用 MySQL 账号，
  不再复用主业务账号；用真实受限权限账号验证过 INSERT/SELECT 能成功、
  UPDATE/DELETE 会被 MySQL 拒绝，见 enterprise-rbac-plan.md 第23节。
- Phase 5（Spring Boot 企业业务中心，见
  [docs/enterprise-business-hub-plan.md](enterprise-business-hub-plan.md)）：OA 请假、
  库存与采购（含 SAP Mock Connector）、CRM 客户跟进三个业务域全部端到端跑通（HMAC 签名
  请求上下文、幂等、审计），35 个 Java 测试（28 个真实 HTTP+MySQL 集成测试 +
  7 个纯逻辑单测）+ 17 个 FastAPI Agent 工具全绿。
  Docker Compose 生产部署集成已完成并用真实 Docker daemon 验证过（构建镜像 →
  启动容器 → `mysql-init` 自动建库 → Flyway 迁移 → healthcheck 通过 → 宿主机
  `curl` 探活成功，见 [enterprise-business-hub-plan.md](enterprise-business-hub-plan.md)
  第15节）。2026-09-28 修复了两个 P0 级权限漏洞：Agent 工具之前能直接把
  `oa.leave.approve`/`procurement.approve` 这类 scope 硬编码签进请求，从没有
  查过调用者是否真的是部门负责人；OA/采购的 `getStatus`/`approve`/`reject`
  也没有资源级隔离（能看/批别人部门的申请）。修复后权限判断收口到
  `service/enterprise_access.py::is_org_admin`/`is_team_admin`（按真实
  `enterprise_role` 算，工具不能自行断言），Java 侧新增
  `security.TeamAccessGuard` 做资源级校验 + 禁止自审批，详见
  [enterprise-business-hub-plan.md](enterprise-business-hub-plan.md)第16节。
  还没做：`RealSapConnector`（等真实企业提供接口）。
- `npm run release:check` 静态检查通过。

## 当前主要风险

- 同步 / 异步边界部分收口：路由层读接口 + **RAG 检索链路（`search_entry.*_async` +
  `search_spaces_async`）** + **聊天执行（`agent_runtime` 非流式与流式，含 `POST /chat/{id}` 与
  `/stream`）** + **evaluation 路由（`/evaluation/*`，含固定评估集 CRUD，Phase 3 收尾完成，
  `rag_service.async_search` 半异步版本已随之删除，见 `docs/sync-async-boundary.md`）** 已 async，
  `service/widgets` 100% async；剩知识库上传/入库/重建/诊断、任务 Worker 主体仍待收口（低收益高风险，
  按 `docs/sync-async-boundary.md` 的既定结论长期保持同步，不是遗漏）。
  详见 `docs/sync-async-boundary.md`、`docs/agent-runtime-async-migration.md`。
- service 层内部零散的 `raise ValueError` / 裸 `Exception` 尚未全部换成领域异常（路由层已在 `except` 里翻译）。
- ~~压力测试还未在真实服务器上形成基准报告~~：本机单进程基线已跑通（health/auth-read/
  mixed-read 三场景零失败），见 [docs/production-readiness-verification.md](production-readiness-verification.md)。
  **仍待办**：这是开发机数字，不是生产容量规划依据，上线后要在真实服务器上照同一套命令
  重新跑一遍；`/metrics` 仍缺生产环境的告警规则阈值调优。
- ~~外部服务熔断目前是进程内状态，多 API/Worker 实例不共享全局熔断~~：已接 Redis
  共享（见上面"已完成能力"熔断/降级条目）。`tests/test_http_resilience.py` 里的假
  Redis 客户端验证了逻辑本身；2026-09-28 又拿真实 Redis 容器 + 两个独立
  `CircuitBreaker` 实例（模拟两个进程，各自连接同一个 Redis，不是共享同一个
  Python 对象）实测过："进程 A 记录 3 次失败触发熔断" → "进程 B 从未调用过
  `record_failure`，但立刻在 `before_call` 看到熔断打开" → "进程 B 调用
  `record_success`，进程 A 也立刻看到熔断关闭"，全部符合预期。跨实例共享已经
  是实测过的行为，不再是纯逻辑推断。
- ~~备份命令已给出，但还需在真实部署环境做恢复演练~~：本机已完整跑通一次
  "备份 → 还原到独立 scratch 库 → 校验表数量/行数一致 → 删除"，见
  [docs/production-readiness-verification.md](production-readiness-verification.md)。
  **仍待办**：真实生产数据库体量更大，上线后要重新做一次演练记录真实耗时。
- ~~数据库迁移已建立骨架，但模型定义和数据库初始化尚未拆分，暂不适合直接开启 Alembic
  自动生成~~：Phase 3A 已解决（`migrations/env.py` 接上 `Base.metadata`，`alembic revision
  --autogenerate` 可用；全新空库 `alembic upgrade head` 验证过跟当前模型完全一致），
  详见 `docs/db-migration-plan.md`。
- 已加 `.gitattributes`（统一 LF）+ `.pre-commit-config.yaml`（BOM / 行尾 / 尾空格等卫生检查）。
  一次性规范化：`git add --renormalize . && git commit`（建议在提交完当前功能改动后单独做）。
  启用 pre-commit：`pip install pre-commit && pre-commit install`。

## 下一步建议

1. ~~`agent_runtime` async 迁移~~ **完成**（阶段 0~4，见 `docs/agent-runtime-async-migration.md`）。
2. ~~RAG 检索彻底 async~~ **完成**（`search_entry.*_async` / `search_spaces_async` +
   `_get_client_async` + async chunk 反查，`tests/test_rag_search_async.py`）；聊天链路、
   `FasdtApi/knowledge.py` 检索端点、evaluation 路由均已切原生 async，半异步的
   `rag_service.async_search` 已删除。
3. service 层内部异常逐模块换成 `service/exceptions.py` 领域异常。
4. 真实服务器压力测试基准报告；告警规则接入。
5. ~~拆分 ORM 模型定义与数据库启动初始化，完成 Alembic 全量接管~~ **完成**（Phase 3A，
   见 `docs/db-migration-plan.md`）。
6. ~~多实例共享熔断状态（接 Redis）~~ **完成**（`service/http_resilience.py::CircuitBreaker`，
   2026-09-27）。
