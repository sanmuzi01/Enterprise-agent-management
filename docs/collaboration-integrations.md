# 飞书 / 钉钉接入

员工在飞书、钉钉里给企业机器人发消息，就能使用平台里的助手：用的是同一个中央 Agent / 部门 Agent、同一套权限、额度和高风险操作确认，不另写一套 Agent。

## 一条消息怎么走

```
飞书 / 钉钉 → POST /integrations/{provider}/events
  1. 验签、解密、检查时间戳（5 分钟内）和 nonce（防重放）     service/integrations/{feishu,dingtalk}/callback.py
  2. 去重：(provider, tenant_id, event_id) 唯一，重推的事件直接确认收到   service/integrations/event_inbox.py
  3. 立即应答平台（飞书要求 3 秒内），后面的处理在应答之后执行          FasdtApi/integrations.py → BackgroundTasks
  4. 外部账号 → 平台账号（绑定必须 active，账号没停用、仍是企业成员）     service/integrations/identity.py
  5. 选 Agent：有可用的中央 Agent 用它，否则用本人部门已发布的部门 Agent   dispatcher.choose_agent
  6. 走网页同一条对话流程：限流 → 中央路由 → 额度 → 并发 → Agent        service/chat_pipeline.py::run_turn
  7. 回复文字；这一轮里 Agent 请求的高风险操作，作为确认卡片私发给本人
```

部门、权限每次都按“此刻”的平台数据算：调岗、停用、离职立即生效。同一个人连续发的消息接着同一个会话（30 分钟没说话或发“新对话”就重新开始）；原会话所在的助手不能用了（比如调了部门），自动按新部门开新会话。

## 安全约束

| 约束 | 做法 |
|---|---|
| 回调不能伪造 | 飞书：只接受配置了 Encrypt Key 的应用（不加密时飞书不签名），签名 = SHA-256(timestamp + nonce + Encrypt Key + 原始请求体)。钉钉机器人：HmacSHA256(AppSecret, timestamp + "\n" + AppSecret)；事件订阅：SHA-1(排序后的 token、timestamp、nonce、encrypt)。 |
| 过期 / 重放 | 时间戳超过 `INTEGRATION_CALLBACK_MAX_SKEW_SECONDS`（默认 300 秒）拒绝；nonce 在有效期内只能用一次（有 Redis 时多进程共享）。 |
| 重复推送 | `external_event_inbox` 唯一约束，同一事件只处理一次，不会建两张单子。 |
| 卡片按钮 | 按钮只带 `{action, token}`：一次性确认令牌，10 分钟有效，只能本人用（`tool_confirmation_service` 校验归属、过期、只能用一次）。要执行什么在生成令牌时已存进数据库，按钮改不了。 |
| 群聊 | 只处理 @ 机器人的消息；确认卡片私发给本人，不在群里展示要确认的内容。 |
| 不读私聊 | 只接收员工主动发给机器人的消息，不读取员工之间的聊天。 |
| 密钥 | App Secret、Encrypt Key / aes_key 加密存库，接口只返回末四位；访问令牌加密后缓存。 |
| 回复地址 | 钉钉 sessionWebhook 只接受钉钉自己的域名（防止被伪造成内网地址）。 |

## 管理员配置（后台 → 外部协作平台）

回调地址 = 你的域名 + `/api/integrations/{provider}/events`（卡片：`/card-actions`），必须是公网 HTTPS。

**飞书**：企业自建应用 → 开启机器人 → 事件与回调：配置 Encrypt Key（必填）和 Verification Token，订阅 `im.message.receive_v1`（接收消息）、`contact.user.deleted_v3`（员工离职），卡片回调也指向上面的地址。权限：接收 / 发送单聊群聊消息、获取通讯录基本信息、获取用户手机号。

**钉钉**：企业内部应用 → 机器人（消息接收模式 HTTP）→ 填 AppKey、AppSecret、robotCode。要接收离职事件再配置事件订阅（签名 Token、aes_key，订阅 `user_leave_org`）。互动卡片模板可选：模板变量见 `service/integrations/dingtalk/cards.py`；不配置时确认操作发 Markdown，员工到网页“待确认操作”里确认。

**人员绑定**：点“同步组织架构”，按手机号自动对上平台账号；对不上的手动选择。离职 / 已不在通讯录里的自动停用。改绑写审计（`integration.user_rebound`）。

## 环境变量

| 变量 | 说明 |
|---|---|
| `INTEGRATION_CALLBACK_MAX_SKEW_SECONDS` | 回调时间戳允许的偏差，默认 300 |
| `INTEGRATION_HTTP_TIMEOUT_SECONDS` | 调用飞书 / 钉钉接口的超时，默认 10 |
| `INTEGRATION_WEB_BASE_URL` | 网页工作台地址；配置后确认卡片带“在网页中查看”按钮 |
| `FEISHU_BASE_URL` / `DINGTALK_BASE_URL` / `DINGTALK_OAPI_URL` | 开放平台地址（私有化部署、测试时替换） |

## 还需要用真实应用确认的

- 钉钉互动卡片的 HTTP 回调签名：钉钉文档说明不完整，现在按机器人回调同样的方式校验（timestamp + sign，AppSecret）。对不上时回调会被拒绝（401），不会放行；接真实应用时先点一次卡片按钮确认。
- 飞书新版卡片回调（`card.action.trigger`）和旧版消息卡片回调都支持，按应用实际配置的那种走。

测试：`tests/test_integrations.py`（验签解密按两家文档的算法自行构造请求；不连真实平台）。
