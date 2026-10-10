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

**飞书**：企业自建应用 → 开启机器人 → 事件与回调：配置 Encrypt Key（本系统启用接入前必填）和 Verification Token（启用前必填），订阅 `im.message.receive_v1`（接收消息）、`contact.user.deleted_v3`（员工离职），卡片回调也指向上面的地址。权限：接收 / 发送单聊群聊消息、获取通讯录基本信息、获取用户手机号。要用“群消息记录到 CRM”再加：获取群组中所有消息、获取群组信息，并订阅 `im.message.recalled_v1`（撤回的消息不进 CRM），见下面“聊天记录进 CRM”。未配置完整时可以在管理后台保存为停用状态的草稿，但不能启用；这是本系统为避免伪造回调设置的安全要求，不代表飞书开放平台强制所有应用开启加密。

**钉钉**：企业内部应用 → 机器人（消息接收模式 HTTP）→ 填 AppKey、AppSecret、robotCode。要接收离职事件再配置事件订阅（签名 Token、aes_key，订阅 `user_leave_org`）。互动卡片模板可选：模板变量见 `service/integrations/dingtalk/cards.py`；不配置时确认操作发 Markdown，员工到网页“待确认操作”里确认。

## 谁配什么：应用归企业，账号归员工

飞书 / 钉钉的应用（机器人）是**企业级**的：管理员在开放平台建一个、在后台配一次。员工**不需要**各自建应用，
每个人要做的只是“绑定”——让平台知道“这个飞书 / 钉钉账号就是我”。三种绑定方式：

| 方式 | 谁来做 | 怎么做 | 需要的条件 |
|---|---|---|---|
| 绑定码（默认） | 员工自己 | 平台“设置 → 飞书 / 钉钉”点“获取绑定码”，在飞书 / 钉钉里**私聊**机器人发“绑定 123456” | 回调已通 |
| 一键授权 | 员工自己 | 设置页点“用飞书 / 钉钉授权绑定”，授权后自动跳回 | 另配 `INTEGRATION_PUBLIC_BASE_URL` 和开放平台的重定向地址 |
| 批量 / 手动 | 管理员 | 后台“同步组织架构”按手机号对上；对不上的在“人员绑定”里选人（给机器人发过消息的人会自动出现在列表里） | 通讯录、手机号权限 |

规则：绑定码 10 分钟有效、只能用一次、领新码旧码作废、同一外部账号 15 分钟最多试 5 次、只在私聊里有效；
一个外部账号只对应一个平台账号（要换先解绑）；管理员停用的绑定员工不能自己恢复；绑定 / 解绑都写审计。
没绑定的人给机器人发消息，会收到具体的绑定步骤（配了 `INTEGRATION_WEB_BASE_URL` 时附平台地址）。
后台“提醒未绑定员工”会给所有没绑定的成员发站内通知（每人每天最多一次）。

**人员绑定（管理员）**：点“同步组织架构”，按手机号自动对上平台账号；对不上的手动选择。离职 / 已不在通讯录里的自动停用。改绑写审计（`integration.user_rebound`）。

## 聊天记录进 CRM（销售部门）

默认**不读取任何聊天**。只有下面三种由员工主动发起的情况，聊天内容才会进 CRM 客户活动（自动对应客户，对不上的进网页“待归属活动”）：

| 方式 | 怎么用 | 说明 |
|---|---|---|
| 单条保存 | 私聊机器人（或群里 @ 它）发“保存到CRM：华星科技王总说下周三前给答复” | 飞书、钉钉都支持 |
| 转发聊天记录 | 在飞书里选中和客户的聊天，“合并转发”给机器人 | 整段存成一条活动，每条带发送人和时间；同一次转发只存一次。图片、文件记成占位（[图片]、[文件：xx.pdf]），不识别内容。仅飞书（钉钉文档没有说明机器人能收到合并转发的内容，收到时如实回复不支持） |
| 整群记录 | 销售部门负责人或群主在客户群里 @机器人 发“开启CRM记录 客户名称”（客户名可不写）；“关闭CRM记录”关闭；“CRM记录状态”查看 | 开启时机器人在群里公告。之后群里的消息先暂存，一段对话停下 30 分钟（或满 300 条）后整理成一条“群聊”活动并删除暂存；撤回的消息在整理前删除。带客户名时必须对上唯一客户，模糊的不开启。仅飞书（钉钉机器人收不到群里没 @ 它的消息） |

这几种都只对**绑定了账号、属于销售部门**的员工有效（整群记录里，群里其他人——包括客户——的消息都会被记录，所以开启要负责人或群主、并在群里公告）。
网页“部门工作台 → 客户沟通收集 → 飞书群记录”能看到开启了的群、已记录条数，负责人可以直接关闭（机器人会在群里告知）。

整群记录需要飞书应用额外开通：**获取群组中所有消息**（敏感权限，需企业管理员审核）、**获取群组信息**（判断群主、取群名），并订阅 `im.message.recalled_v1`。
没开通前者时，机器人只收得到 @ 它的消息——网页上会提示“开启超过 10 分钟还没收到消息”。
开通了这个权限后，所有群的消息都会推给平台；没开启记录的群，平台收到后直接丢弃，不保存内容（`external_event_inbox` 只记事件编号和“已忽略”）。

## 环境变量

| 变量 | 说明 |
|---|---|
| `INTEGRATION_CALLBACK_MAX_SKEW_SECONDS` | 回调时间戳允许的偏差，默认 300 |
| `INTEGRATION_HTTP_TIMEOUT_SECONDS` | 调用飞书 / 钉钉接口的超时，默认 10 |
| `INTEGRATION_PUBLIC_BASE_URL` | 飞书 / 钉钉能访问到的后端地址（公网 HTTPS，经 nginx 时带 `/api`）；后台按它显示回调地址，一键授权用它拼回调 |
| `INTEGRATION_WEB_BASE_URL` | 网页工作台地址；机器人回复里的绑定指引、授权后跳回、卡片“在网页中查看”都用它 |
| `TRUSTED_HOSTS` | **必须包含公网域名**，否则平台的回调会被 Host 白名单拒绝（HTTP 400） |
| `INTEGRATION_BIND_RATE_LIMIT` / `INTEGRATION_BIND_RATE_WINDOW_SECONDS` | 同一外部账号试绑定码的次数上限，默认 15 分钟 5 次 |
| `FEISHU_BASE_URL` / `DINGTALK_BASE_URL` / `DINGTALK_OAPI_URL` | 开放平台地址（私有化部署、测试时替换） |

## 公网 HTTPS 地址怎么弄

飞书 / 钉钉的服务器要能从公网访问到你的回调地址，而且必须是 HTTPS（证书要是正规 CA 签发的，自签证书不行）。
`127.0.0.1`、`localhost`、公司内网地址，平台都访问不到。两条路：

**A. 用已有的服务器和域名（试点 / 正式，推荐）**

仓库里的 `agent-platform-https.conf` 已经是 `sanmuzi.asia` 的 nginx 配置：Let's Encrypt 证书、`/api/` 转发到后端 8000 端口都有了。
在那台服务器上：
1. 部署最新代码，执行 `alembic upgrade head`；
2. `.env` 设置 `INTEGRATION_PUBLIC_BASE_URL=https://sanmuzi.asia/api`、`INTEGRATION_WEB_BASE_URL=https://sanmuzi.asia`，
   `TRUSTED_HOSTS` 里加上 `sanmuzi.asia,www.sanmuzi.asia`，重启后端；
3. 回调地址就是 `https://sanmuzi.asia/api/integrations/feishu/events`（钉钉把 `feishu` 换成 `dingtalk`）。

换别的服务器：域名解析到服务器 → `certbot --nginx -d 你的域名` 申请免费证书 → nginx 把 `/api/` 转发到后端（照抄上面那个配置）。

**B. 本机开发时用内网穿透（临时联调）**

穿透工具会给本机的端口分配一个公网 HTTPS 域名。穿透直接指向**后端端口**（8011），不经过前端，地址里也就**不带** `/api`：

```bash
cloudflared tunnel --url http://127.0.0.1:8011
```

命令会打印一个 `https://xxxx.trycloudflare.com`。然后：
1. `.env` 设置 `INTEGRATION_PUBLIC_BASE_URL=https://xxxx.trycloudflare.com`，`TRUSTED_HOSTS` 加上 `xxxx.trycloudflare.com`，重启后端；
2. 回调地址是 `https://xxxx.trycloudflare.com/integrations/feishu/events`。

注意：这种临时域名每次重启穿透都会变，变了要同步改 `.env` 和开放平台上的地址；飞书要求 3 秒内应答，
国外节点慢时可能偶发失败，可以换国内的穿透服务（cpolar、natapp 等，用法相同：把本机 8011 端口映射成 HTTPS 域名）。
穿透只用于联调，不要长期把开发机暴露在公网。

## 真机联调步骤

1. **开放平台建测试应用**
   - 飞书：开放平台 → 创建企业自建应用 → 添加“机器人”能力 → 权限管理开通“获取与发送单聊、群组消息”“获取通讯录基本信息”“获取用户手机号”
     （要整群记录到 CRM 再开“获取群组中所有消息”“获取群组信息”，并订阅 `im.message.recalled_v1`）→
     事件与回调：加密策略里拿到 Verification Token 和 Encrypt Key；请求地址填回调地址；订阅 `im.message.receive_v1`、`contact.user.deleted_v3` →
     版本管理与发布：创建版本、可用范围选测试人员、发布（企业管理员审核通过）。
   - 钉钉：开发者后台 → 创建企业内部应用 → 添加“机器人”，消息接收模式选 HTTP，填消息接收地址 → 拿到 AppKey、AppSecret、robotCode →
     需要离职事件时在“事件订阅”里配置签名 Token、aes_key 和请求地址 → 发布。
2. **后台填凭证**：后台 → 飞书 / 钉钉接入，填 App ID / Secret / Token / Encrypt Key（钉钉填 AppKey / AppSecret / robotCode），勾选启用，保存，点“测试连接”。
3. **自测回调链路**（不用等平台）：
   ```bash
   .venv\Scripts\python.exe scripts\check_integration_callback.py feishu
   ```
   它会按平台的算法签名加密、给公网地址发一次“校验回调地址”请求，失败时直接告诉你是穿透、Host 白名单、还是 Token 的问题。
4. **在开放平台保存回调地址**：平台会发校验请求，通过才能保存。
5. **看后台“接入自检”**：全部打勾为止（“已收到回调”要等你给机器人发过一条消息）。
6. **员工绑定并试用**：员工在“设置 → 飞书 / 钉钉”领绑定码，私聊机器人发“绑定 xxxxxx”，再发“帮我查一下这个月的报销”。

## 还需要用真实应用确认的

- 钉钉互动卡片的 HTTP 回调签名：钉钉文档说明不完整，现在按机器人回调同样的方式校验（timestamp + sign，AppSecret）。对不上时回调会被拒绝（401），不会放行；接真实应用时先点一次卡片按钮确认。
- 飞书新版卡片回调（`card.action.trigger`）和旧版消息卡片回调都支持，按应用实际配置的那种走。

- 一键授权用的飞书 `authen/v2/oauth/token`、钉钉 `oauth2/userAccessToken` + `topapi/user/getbyunionid` 按文档实现，真机授权后确认一次。
- 合并转发：按飞书“获取指定消息的内容”接口（返回 1 条合并转发 + N 条子消息，子消息用 `upper_message_id` 指向它）实现；真机转发一次，确认外部联系人（客户）的发送人显示为“外部成员 1、2……”、本企业员工显示姓名。

测试：`tests/test_integrations.py`、`tests/test_integration_binding.py`、`tests/test_crm_chat_capture.py`（验签解密按两家文档的算法自行构造请求；不连真实平台）。
