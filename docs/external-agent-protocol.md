# 外部智能体接入协议（enterprise-agent/1）

平台的智能体默认用自带的运行方式（提示词 + 工具 + 知识库）。如果你已经有自己写好的 Agent 项目
（LangGraph、AutoGen、自研循环……），可以把它作为一个独立的 HTTP 服务接进来：
平台继续负责**谁能用、限流、密级、审计、对话记录**，你的服务负责**怎么思考、调用什么工具、用什么模型**。

最小可运行示例：[examples/external_agent_example.py](../examples/external_agent_example.py)（只用标准库）。

## 接入步骤

1. 管理后台 → 企业智能体 → **接入智能体服务**：登记名称、它能做什么、维护人，填写服务地址，保存。
   企业智能体是工程师开发好的服务，不是在页面里填几段提示词“造”出来的；平台没有“空白创建”的入口，只有“接入服务”和“启用内置智能体”（选内置模板）两条路。
2. 保存后页面**只显示一次**签名密钥，复制到你的服务里（环境变量或配置中心）。忘记了只能“重新生成”，旧密钥立即失效。
3. 点“测试连接”：平台发一个 `type=ping` 请求（不含任何用户对话）。通过后再点“发布”。外部智能体没有配置地址时不能发布。

只有管理员创建的中央 / 部门智能体可以接外部服务；个人智能体没有这个入口——外部服务会收到提问者的身份和对话内容，
接入哪个服务属于企业级决定。

## 请求（平台 → 你的服务）

`POST <服务地址>`，`Content-Type: application/json`

| 请求头 | 含义 |
| --- | --- |
| `X-Agent-Timestamp` | 发送时刻的 Unix 秒 |
| `X-Agent-Signature` | `sha256=` + HMAC-SHA256(签名密钥, `时间戳 + "." + 原始请求体`) 的十六进制 |
| `X-Agent-Request-Id` | 本次请求编号，写日志时带上便于对账 |
| `Accept` | `text/event-stream, application/json`：你可以返回事件流，也可以返回普通 JSON |
| 管理员配置的附加请求头 | 例如 `Authorization`；不能覆盖上面几个 |

请求体：

```json
{
  "protocol": "enterprise-agent/1",
  "type": "chat",
  "request_id": "9f3c…",
  "run_id": 123,
  "conversation_id": 45,
  "agent": {"id": 7, "name": "销售助手"},
  "user": {"id": 28, "name": "张三", "departments": [{"id": 3, "name": "销售一部", "department_code": "sales", "role": "member"}]},
  "message": "这个客户上次报价是多少？",
  "history": [{"role": "user", "content": "…"}, {"role": "assistant", "content": "…"}],
  "knowledge": {"context": "…检索到的资料片段…", "citations": [{"title": "报价手册"}]}
}
```

- `type` 为 `ping` 时是连接测试：`history` 为空，`user.departments` 为空，直接返回 `{"answer": "pong"}` 即可。
- `history` 最多 20 条、每条最多 4000 字，只含 `user` / `assistant`，不含系统提示词。
- `knowledge` 只在管理员勾选“同时发送检索到的资料片段”且智能体开启了知识库时出现，并且已按密级过滤：
  **机密、绝密空间的内容不会发给外部服务**。
- 不会发送：手机号、密码、令牌、其他用户的数据。

### 你必须做的校验

1. 用保存的密钥重新计算签名，用**常量时间比较**（`hmac.compare_digest`）；不一致返回 401。
2. 时间戳与当前时间相差超过 5 分钟的请求拒绝，防止重放。
3. 只对 `user.id` 对应的这个人的权限范围内的数据做事——平台已经确认他能用这个智能体，但**你自己的业务系统里的数据权限要你自己判断**。

## 响应（你的服务 → 平台）

### 方式一：普通 JSON

```json
{
  "answer": "上次报价是 12.8 万元。",
  "steps": [{"title": "查询报价", "detail": "查了 CRM 的最近报价"}],
  "citations": [{"title": "报价单 2026-09"}],
  "usage": {"total_tokens": 842}
}
```

只有 `answer` 必填。`steps` 会作为运行轨迹展示，`citations` 作为回答来源，`usage.total_tokens` 计入用量。

### 方式二：事件流（`Content-Type: text/event-stream`）

每个事件一行 `data: {JSON}`，事件之间空一行：

```
data: {"type": "step", "title": "查询报价", "detail": "查了 CRM"}

data: {"type": "delta", "text": "上次报价"}

data: {"type": "delta", "text": "是 12.8 万元。"}

data: {"type": "citations", "items": [{"title": "报价单 2026-09"}]}

data: {"type": "done", "usage": {"total_tokens": 842}}
```

出错时发 `{"type": "error", "message": "…"}`，平台会向用户显示一句通用的失败提示（你的内部错误细节不会透给用户）。

## 平台对你的服务的限制

| 项目 | 默认 | 配置 |
| --- | --- | --- |
| 超时 | 60 秒（管理员可设，上限 300 秒） | `EXTERNAL_AGENT_MAX_TIMEOUT_SECONDS` |
| 响应体大小 | 1 MB | `EXTERNAL_AGENT_MAX_RESPONSE_BYTES` |
| 答案长度 | 50000 字，超出截断 | `EXTERNAL_AGENT_MAX_ANSWER_CHARS` |
| 重定向 | 不跟随（302 视为失败） | — |
| 连续失败 | 触发熔断，暂停一段时间再试 | 与其他外部调用共用熔断器 |

平台**不会自动重试**对你服务的请求（避免带副作用的操作被重复执行）。

## 地址安全（SSRF 防护）

- 默认只允许公网地址；`localhost`、内网段、链路本地地址一律拒绝；云厂商元数据地址（如 `169.254.169.254`）任何情况下都拒绝。
- 你的服务在企业内网时，由运维把它的主机写进 `EXTERNAL_AGENT_ALLOWED_HOSTS`（逗号分隔，可写 `host` 或 `host:port`），才允许保存和调用。
- 生产环境（`APP_ENV=production`）要求 `https`；加入允许名单的内网主机可以用 `http`。
- 地址在**保存时**和**每次调用前**都会重新校验。

## 密钥与审计

- 签名密钥在库里是加密保存的；附加请求头同样加密，页面只显示请求头的名字，不显示内容。
- 配置、轮换密钥、测试连接都会写入审计日志（不含密钥本身）。
- 每次对话照常生成运行记录（`failed` / `finished`），包含耗时和你返回的 `steps`。
