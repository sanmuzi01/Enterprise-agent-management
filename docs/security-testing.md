# 安全专项测试

目标：用**会失败的测试**而不是口头保证来确认这些问题——SQL 注入、XSS、SSRF、跨部门 / 跨企业越权、恶意文件、提示注入与 Agent 越权调用工具、重复与并发（重复审批、重复记账）。
每一类都先写攻击载荷、在真实代码上跑出失败，再修，再让测试留下来防回退。

## 怎么跑

| 内容 | 命令 | 需要 |
|---|---|---|
| 全部 Python 测试（含下面所有单元 / 集成安全测试） | `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"` | MySQL |
| 前端 XSS 载荷矩阵（真实浏览器 + 真实渲染模块） | `$env:PLAYWRIGHT_CHANNEL='msedge'; .venv\Scripts\python.exe scripts\check_xss_browser.py` | Vite 在 5174 端口 |
| 跨部门 / 跨企业越权矩阵 + 重复与并发（真实 Java + MySQL） | `.venv\Scripts\python.exe scripts\e2e_security.py` | Java 业务服务在 8090 |
| Java 全部测试（含跨企业越权用例） | 先停掉 8090 上的 Java，再在 `enterprise-business-hub` 里 `mvn test` | MySQL |
| 真实 Redis 上的并发配额 + 缓存安全 | `$env:REDIS_URL='redis://127.0.0.1:6390/0'; .venv\Scripts\python.exe -m unittest tests.test_redis_concurrency` | Redis（`deploy/test-services`） |
| 登录会话 Cookie / CSRF / CSP 的真实浏览器验证 | `$env:PLAYWRIGHT_CHANNEL='msedge'; .venv\Scripts\python.exe scripts\check_session_browser.py` | 后端 8011 + `npm run preview`（4173） |

## 覆盖与结果

| 类别 | 测试 | 发现并修复的真实问题 |
|---|---|---|
| **SSRF** | `tests/test_security_ssrf.py`：字面 / 数字 / IPv6 / 元数据 / 协议 / 解析器技巧载荷矩阵（生产与开发两套环境）、DNS 重绑定、逐跳重定向、Webhook / 组件数据源 / LLM / GitHub 导入各出站点、静态守卫 | ① `requests` 默认跟随重定向（连接器与 LLM 客户端共 5 处）——公网地址 302 到 `169.254.169.254` 绕过出站校验并带上 API Key；② 阿里云元数据 `100.100.100.200`（共享地址空间）不在 `is_private` 里；③ 数字 IP 写法（`2130706433`、`0x7f000001`）依赖系统解析器；④ 开发环境对任意内网 DNS 结果放行；⑤ 校验与建连接之间的 DNS 重绑定窗口；⑥ 企业接口连接器先读完整个响应再判断大小 |
| **恶意文件** | `tests/test_security_files.py`：路径载荷矩阵（`..`、盘符、UNC、备用数据流、保留设备名、NUL…）、压缩炸弹与压缩比、伪造头部、文件头伪装、双扩展名、各入口拒绝且不留残留 | ① 考勤导入接受“400KB 压缩、展开 400MB”的 xlsx；② 技能包安装没拦 Windows 盘符 / 备用数据流 / 设备名；③ 旧版能力包 `extractall` 之前没有任何体积与数量限制；④ 附件名 `con.txt` / `NUL` 在 Windows 上写进设备 |
| **SQL 注入** | `tests/test_security_sql_injection.py`：静态守卫（Python 与 Java 都不允许把变量拼进 SQL，只有登记过的写死标识符）+ 从 OpenAPI 自动枚举约 250 个路由、10 种载荷、普通用户与管理员各跑一遍（5xx、数据库报错泄漏、时间盲注） | 没有注入；顺带发现 `PUT /user/profile` 对新用户必然 500（`UserProfile(created_at=…)`）——已修并加回归测试 |
| **XSS** | `scripts/check_xss_browser.py`（79 个载荷）+ `tests/test_security_xss_backend.py` | 脚本执行类载荷 DOMPurify 全部挡住；但提示注入可以让模型输出钓鱼表单、`style` / Tailwind `class` 全屏覆盖、外链图片（零点击外泄），现已禁止；导出中文标题的会话必然 500（HTTP 头只能是 Latin-1），改为 RFC 6266 |
| **提示注入 / 工具越权** | `tests/test_security_agent_tools.py`、`tests/test_security_prompt_injection.py` | 模型输出（及网页 JSON-LD、外部接口返回）深度嵌套会让 JSON 解析 `RecursionError` 变成 500；检索资料可以伪造分隔符与来源编号（`prompt_guard`）；其余边界（身份不来自模型参数、15 个高风险工具永远只生成待确认单、工具风险快照、只读工具不写）都已成立并被测试固定 |
| **跨部门 / 跨企业越权** | `scripts/e2e_security.py` 第一部分（请假、报销、IT 工单、AI 整理结果、责任计划与任务；同部门同事 / 别部门成员 / 别部门负责人 / 别企业成员与管理员 / 未登录）+ Java `TeamAccessGuardTest`、`LeaveControllerIntegrationTest` + `tests/test_org_admin_scope.py` | **严重**：企业管理员的身份是全局布尔值，企业 B 的管理员可以审批企业 A 的请假、查看企业 A 的 IT 工单。现在签进业务系统的身份按资源所属企业重新计算，并带上该企业的部门范围（`org_team_ids`），Java 要求资源所在部门必须在范围内 |
| **平台审批** | `tests/test_approval_org_scope.py`：企业 A 的“删除知识库空间”审批，企业 B 的管理员看不到、批不了；未登记所属企业的审批类型对所有人不可见 | `/approvals/pending` 与 `/approvals/{id}/decide` 只要求“是任何一个企业的管理员”：企业 B 的管理员能看到并批准企业 A 的空间删除申请（破坏性操作）。现在按资源所属企业限定，新增审批类型必须在 `approval_service.resource_org_id` 登记 |
| **重复与并发** | `scripts/e2e_security.py` 第二部分：同一请求并发 8 次；两张不同的单据争同一份余额 / 预算；相同 Idempotency-Key 重放 | **严重**：请假、采购、CRM 确认没有行锁，同一张请假单并发审批 8 次全部成功（8 条“批准”审计），同一个人的两张请假同时批准会覆盖余额，同部门两张报销 / 采购同时批准会把预算花两遍。现已加行锁（请假单与余额、采购单与预算、报销预算、报销单、CRM 跟进） |
| **并发配额（Redis）** | `tests/test_redis_concurrency.py`（真实 Redis：40 个线程 + 6 个进程抢同一个名额） | ① “清理过期 → 数当前 → 判断 → 占位”分成几次往返，上限 5 的名额实际放行了 13 个——现在是一个 Lua 脚本里的原子操作；② `RedisClientManager` 的并发首次连接：第一个线程还在连，其余线程因为“重试窗口”拿到 `None`，各自退回进程内限流，上限被成倍突破——现在连接加锁，且只有连接失败后才开启重试窗口；③ 固定窗口的 `INCR` 和 `EXPIRE` 分开执行，进程恰好死在中间会留下永不过期的计数 key——现在在同一个事务里 |
| **缓存反序列化 / 密钥** | `tests/test_cache_safety.py`（假 Redis）+ `tests/test_redis_concurrency.py`（真实 Redis） | ① Redis 里的缓存用 `pickle.loads`——只要有人能写 Redis 就能执行代码，现在只用 JSON，无法解析的内容当作没命中并丢弃，旧版本的数据用新前缀读不到；② 解密后的模型 API Key 和完整配置被放进这个缓存——现在缓存里只有数据库里那份密文，解密只在调用方拿到之后在内存里发生 |
| **登录令牌与 CSP** | `tests/test_session_cookie.py`、`tests/test_csp_policy.py`、`frontend` Vitest、`scripts/check_session_browser.py`（真实浏览器 23 项） | ① 令牌放在 `localStorage`，任何一次 XSS 都能读走——现在是 HttpOnly + SameSite Cookie，页面脚本读不到，会改数据的请求要带绑定会话的签名 CSRF 值（攻击者能往子域名塞 Cookie 也伪造不了）；② 没有内容安全策略——现在 Nginx 下发 `script-src 'self'`（页面里没有任何内联脚本）、`connect-src 'self'`，真实浏览器里注入的内联脚本和往外发数据都被拦下；③ API 响应带最严格的策略；④ 操作日志改用 Cookie 登录后会变成匿名——已让它也认会话 Cookie |
| **健康检查语义** | `tests/test_health_probes.py`（含部署文件核对） | `/health` 无论数据库 / Redis 是否可用都返回 200，Docker 容器永远是 healthy、负载均衡继续往坏实例转发——现在拆成 `/live`（始终 200，不查依赖）和 `/ready`（数据库不可用 503；Redis 不可用算“降级”，`READY_REQUIRE_REDIS=1` 才返回 503），`/health` 等同 `/ready`，Docker healthcheck / Nginx 都改用它们 |
| **数据库半通不通** | `tests/test_db_timeouts.py`（本机“黑洞”服务：接受 TCP 连接但从不发握手包） | 就绪探针用 `asyncio.wait_for` 只能按时返回 503，取消不了线程池里的同步 `connect()`；所有引擎都没有驱动级超时，数据库网络半通不通时每次探针留下一个卡死的线程，逐步耗尽线程池（审查时测试脚手架自己也因此卡了 40 秒以上）。实测只设 `connect_timeout` 仍会无限卡在握手，必须加 `read_timeout`。现在：探针用独立短连接并带连接 / 读 / 写超时；四个引擎都带 `DB_CONNECT_TIMEOUT` + 兜底 `DB_READ_TIMEOUT`；测试脚手架的探测也有明确超时 |
| **配置诊断** | `tests/test_config_validation.py`（检查项唯一） | 我上次改配置校验时把 Cookie 检查插进了 Redis 的 if/else 中间：生产报告里 `REDIS_URL` 出现两次、Cookie 成功项缺失，而总结果仍是成功，所以没人发现。现在每个检查只出现一次，并有测试守住 |
| **Redis 降级策略** | `tests/test_redis_degradation.py`；真实 Redis 中途停掉再启动的实测 | Redis 不可用时限流悄悄退回各进程自己的内存，多个 Worker 时攻击者的额度乘以进程数。现在：登录 / 短信 / 注册 / 找回密码按 `RATE_LIMIT_CRITICAL_FALLBACK`（`strict` 生产默认：额度按进程数均分，合计不超过原额度；`open`；`closed`）；重任务并发名额 `CONCURRENCY_REDIS_DOWN=closed`（生产默认，停止接收新任务）；每次降级计入 `agent_limiter_redis_degraded_total`，`AgentRedisDown` / `AgentLimiterDegraded` 告警 |
| **令牌接口分离** | `tests/test_auth_token_endpoint.py`、`tests/test_session_cookie.py` | 浏览器登录响应体里还带着 JWT，登录时运行的页面脚本能读到。现在 `/user/login` 只下发 HttpOnly Cookie；脚本 / 集成走 `POST /auth/token`（单独限流、审计 `auth.token_issued`、可关闭，生产默认关闭） |
| **上传内存模型** | `tests/test_upload_limits.py`（含 4 个并发 50MB 上传的内存峰值测试） | 上一版分块放进列表再 `b"".join()`，批量上限 200MB，并发上传时单个 Worker 仍可能占几百 MB。现在每个分块直接写临时文件、入库时直接 rename 到最终位置（跨磁盘退回流式拷贝），累计上限默认 50MB 与 Nginx 对齐，同一用户同时上传有并发上限；没有写权限或文件类型不对的请求在收文件之前就被拒绝 |
| **外部智能体接入** | `tests/test_external_agent.py`（假的外部服务 + 示例服务，31 条以上） | 新功能的安全边界在上线前就用测试固定：① 地址：只允许 http/https、不能带用户名密码、内网和本机默认拒绝（内网服务必须进 `EXTERNAL_AGENT_ALLOWED_HOSTS`）、云元数据地址即使进了白名单也拒绝、每次调用前重新校验；② 请求带 HMAC 签名并带时间戳（防篡改、防重放），附加请求头不能覆盖签名头；③ 不跟随重定向（302 到元数据地址被拒）；④ 超时、响应体大小、答案长度都有上限；⑤ 对方的报错细节不会透给用户；⑥ 密钥与请求头在库里是密文，查询接口只返回名字，密钥只在生成时返回一次；⑦ 个人智能体不能接外部服务；⑧ 无权使用智能体的人永远不会触达外部服务；⑨ 机密、绝密资料按外部模型处理，不会发出去 |
| **部门知识库** | `tests/test_knowledge_departments.py`（同步与异步两条访问路径各验证一遍） | 部门成员自动只读但不能写；绝密不继承部门成员资格；`scope_type` 不是部门的旧数据不因为有 `team_id` 就被开放；只有部门管理员能发布到部门、调整归属和密级；收回或升为绝密后成员立即失去访问 |
| **上传大小** | `tests/test_upload_limits.py`（假文件数读了多少字节 + 真实路由） | 知识库上传 `await file.read()` 没有上限，批量上传还不限个数——现在分块读取、超过单文件 / 一次请求累计 / 文件个数上限立即 413（不先读完再判断）；批量上传里“不支持的文件类型”（用户输入错误）会被兜底变成 500 并在问题中心记一条假故障——已修 |

## 修复之后的不变式（e2e 里逐条断言）

- 无权身份对同一对象的任何访问都被拒绝（400 / 401 / 403 / 404 / 409），合法角色确实成功（防止“全部拒绝”这种假通过）；
- 别的企业的管理员即使把 `team_id` 填成本企业的部门也不能越权；
- 同一张请假单并发提交 / 审批：状态、余额、审计各只发生一次；
- 同一份整理结果并发保存：只落一份草稿；同一项责任并发接受 / 提交 / 验收：只记一次；
- 余额 10 天、两张各 6 天的请假同时批准：只批准一张，余额 4 天；预算 1000、两张各 800 的报销同时批准：只批准一张；
- 同一张报销单并发生成凭证：业务库里只有一张（数据库唯一约束兜底）；
- 并发冲突以 4xx 或幂等成功返回，不出现 5xx。

## 仍然没有覆盖的（诚实记录）

- 采购申请的预算 / 库存并发没有端到端用例（代码已加锁，评审过，没有造库存数据去压）；
- Docker / 反向代理层的安全头、TLS、限流在真实部署里的表现；
- 真实模型被注入后的行为（只能验证输出进入业务系统前的确定性防线，不能验证模型“会不会被说服”）；
- 依赖库漏洞：CI 里有 `npm audit` 和 `pip-audit`，没有修复版本的漏洞登记在 `.github/vuln-exceptions.json`（负责人 / 原因 / 到期日）；
- 管理员多因素认证（MFA）、短期访问令牌 + 轮换刷新令牌、Trusted Types：这几项还没做，现在的令牌有效期是 `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`（默认 60 分钟），“退出所有设备”会立即让它失效；
- 第三方渗透测试 / 正式安全审计报告；
- 管理后台（`/admin/organization/...`）按设计只管“默认企业”，由平台管理员账号访问，不是多企业接口，没有做跨企业用例；
- Python 侧用 `require_org_role` 授权的其他接口只剩平台审批一处会跨资源操作（已修）；以后新增这类接口必须同时限定企业范围（`enterprise_access.admin_org_ids_async`）。
