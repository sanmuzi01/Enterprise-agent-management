# 企业业务中心（Spring Boot）—— 未来阶段规划

状态：**已确认架构方向，尚未开始实施**。这是 Phase 3D/Phase 4 之后的下一阶段
（暂称 Phase 5），记录设计是为了不丢决策依据，不代表现在就动 Java 代码。
按用户给出的十二步实施顺序，第 1-2 步就是 Phase 3D 阶段2-3（部门权限/数据密级、
中央 Agent 受控路由），本仓库当前所有 Python 代码改动都还在这两步范围内。

## 1. 为什么要新建一个 Spring Boot 服务，而不是都塞进 FastAPI

避免两个极端：把 OA/ERP/CRM 硬塞进现有 AI Agent 平台（业务逻辑和 Agent 逻辑混在一起，
以后谁都不好改）；或者真的去对接/重写四套现成大型软件（工作量不可控）。折中方案：
FastAPI 继续只管"人、权限、Agent、知识库、审计"这些平台能力，新增一个 Java 服务专门
管"请假、采购、客户"这类具体业务单据，两边通过内部网络 + 签名请求通信，各管一段。

## 2. 架构

```
Vue3 企业门户 → Nginx/HTTPS → FastAPI（AI Agent 平台）→[Docker 内部网络，HMAC 签名]→ Spring Boot（企业业务中心）
```

FastAPI 侧新增部门 Agent：HR / 采购 / 销售 / 财务 / IT，中央 Agent 按用户可访问范围
路由到具体部门 Agent；部门 Agent 通过注册好的工具（`get_leave_balance`/
`create_purchase_draft`/`get_customer_summary` 等）调用 Java 服务，不提供任意 SQL/
任意 HTTP 工具。

## 3. 数据归属

同一个 MySQL 实例，两个库两个账号，禁止跨库直接改表：

- `agent_sql`（FastAPI 现有库）：用户/企业/部门/角色、Agent/Prompt/Skill、知识空间
  和向量、会话/记忆/运行轨迹、Agent 操作确认、模型配置和 Token 用量。
- `enterprise_business`（Java 新库）：请假申请和审批记录、产品/库存/部门预算、采购
  申请和采购单、客户/联系人/商机/跟进记录、外部系统调用记录、幂等记录、业务审计。

## 4. Java 侧模块（模块化单体，不拆微服务）

```
enterprise-business-hub/
  common/ security/ organization-client/ idempotency/ audit/
  oa/ procurement/ crm/ sap/ integration/
```

三个业务域先各做最小闭环，第一版明确不做：财务总账、税务、生产制造、供应链全模块。

- **OA 请假**：请假类型/余额 → 草稿 → 提交 → 部门负责人审批（同意/拒绝）→ 状态查询 →
  审批记录。
- **ERP 采购（轻量）**：产品/库存/安全库存 → 判断是否低于安全库存 → 查部门预算 →
  采购申请草稿 → 用户确认 → 部门负责人审批 → 生成采购单 → 更新单据状态。
- **CRM 客户跟进**：客户/联系人/历史跟进 → Agent 生成摘要 → 跟进草稿 → 用户确认 →
  保存跟进记录 → 创建/更新商机（含商机阶段、客户负责人、部门数据隔离）。

## 5. SAP：只定义接口，不自建实现

```java
interface SapConnector {
    Supplier getSupplier(...);
    PurchaseOrder getPurchaseOrder(...);
    PurchaseOrderDraft createPurchaseOrderDraft(...);
    HealthStatus healthCheck();
}
```

演示阶段用 `MockSapConnector`；真实客户提供接口后换 `RealSapConnector`，Agent 端
不用改一行代码。

## 6. 跨服务安全：权限来源只在 FastAPI 一处

Java 不自己判断权限，只验证 FastAPI 签发的短时效上下文：

```json
{
  "user_id": 100, "team_id": 2,
  "scopes": ["procurement.request.create"],
  "operation": "create_purchase_draft",
  "trace_id": "...", "timestamp": 1790000000, "nonce": "..."
}
```

必须具备：HMAC 签名、有效期、`nonce` 防重放、幂等 Key 防重复提交、部门数据过滤、
参数 Schema 校验、写操作用户确认、高风险操作审批、敏感字段脱敏、完整业务审计。

## 7. 审批分两类，不混在一张表里

- **平台审批**（FastAPI 负责）：发布高权限 Skill、修改权限策略、导出限制级数据、
  Agent 执行高风险工具——这是 Phase 3D 阶段4已经规划的 `approval_request`。
- **业务审批**（Java 负责）：请假审批、采购审批、预算审批、客户特殊操作审批——
  这是 Java 服务自己的表，跟上面那张不是同一张。

## 8. 前端新增页面

中央 Agent 工作台、部门 Agent 中心、我的待办、我的申请、OA 请假、库存与采购、
客户与商机、企业操作记录、管理员审批中心、企业集成状态。普通用户只看到自己有权限
的部门模块。

## 9. 实施顺序（用户确认版，本仓库当前进度见括号）

1. 完成 Token 撤销、部门权限和数据密级 —— **= Phase 3D 阶段2**（进行中）
2. 实现中央 Agent 受控路由 —— **= Phase 3D 阶段3**（未开始）
3. 创建 Spring Boot 业务中心和独立数据库 —— 未开始，依赖 1、2
4. 完成 HMAC 认证、幂等、审计和统一异常
5. 完成 OA 请假闭环（先把这一条走完整，再铺采购/CRM，避免三个业务域同时半成品）
6. 完成库存与采购闭环
7. 完成 CRM 客户跟进闭环
8. 将 Java 接口注册成 Agent 工具
9. 完成 SAP Mock Connector
10. 补齐 E2E、越权、重放、重复提交和事务测试
11. Docker Compose 统一部署
12. 压测、监控和备份恢复演练

## 10. 完成标准

中央 Agent 自动路由部门 Agent；不同部门数据不能越权访问；HR Agent 能完成请假申请；
采购 Agent 能根据库存创建采购申请；销售 Agent 能查询客户并记录跟进；写操作必须经过
用户确认；采购等高风险操作必须审批；重复提交不产生重复业务单；Java 服务异常时 AI
平台能明确降级；每次操作能通过 Trace ID 追踪；数据能备份和恢复。

## 11. 决策记录

| 问题 | 决策 |
|---|---|
| 要不要把 OA/ERP/CRM 做成 FastAPI 里的模块？ | **不做**：业务单据逻辑和 Agent/权限平台逻辑分离，新建独立 Java 服务 |
| 要不要拆 OA/采购/CRM 三个微服务？ | **不拆**：一个模块化单体（`enterprise-business-hub`），规模真的起来再拆 |
| Java 服务要不要自己维护一套权限？ | **不维护**：只验证 FastAPI 签发的短时效签名上下文，权限来源始终只有一处 |
| 平台审批和业务审批要不要合并？ | **不合并**：分别属于 FastAPI 和 Java，两张表、两条流程 |

## 12. 执行结果（OA 请假闭环第一版，2026-09-27）

状态更新：本节之前"已确认架构、未开始实施"，现在 OA 请假这一条闭环已经**端到端跑通**
（真实 Spring Boot 服务 + 真实 MySQL + 真实签名 HTTP 调用 + 真实 FastAPI Agent 工具），
按第9.5节自己定的顺序，只做了这一个闭环，没有同时铺采购/CRM。

### Java 侧（`enterprise-business-hub/`）

Maven 项目，Spring Boot 3.3.4 + Java 21（本机 JDK 23 编译目标设为 21，两者兼容）。
包结构：`security`（`RequestContext`/`SignedRequestContextFilter`/`HmacSignatureVerifier`/
`NonceStore`/`ScopeGuard`）、`idempotency`（`IdempotencyRecord`/`IdempotencyService`）、
`audit`（`AuditEvent`/`AuditService`，追加式，跟知识库空间模块自己的 `kb_audit_log`
是两张不同的表，见类注释）、`oa`（`LeaveType`/`LeaveBalance`/`LeaveRequest`/
`LeaveService`/`LeaveController`）。表结构用 Flyway 管（`V1__init_oa_and_shared.sql`），
`spring.jpa.hibernate.ddl-auto=validate`——跟主项目 Alembic 权威化是同一个原则，
Hibernate 不能隐式改表。独立数据库 `enterprise_business`（本机跟 `agent_sql` 同一个
MySQL 实例，生产按设计稿第2节应该分账号，本地开发暂共用 root，见下面"简化"部分）。

流程：`GET /oa/leave/balance` 查余额 → `POST /oa/leave/requests` 建草稿 →
`POST /oa/leave/requests/{id}/submit` 提交（校验余额够不够）→
`POST /oa/leave/requests/{id}/approve|reject`（部门负责人决定，批准才真正扣减余额，
拒绝不动余额）→ `GET /oa/leave/requests/{id}` 查状态。写接口都要 `Idempotency-Key`
头，重复的 key 直接返回第一次的结果，不重新执行。

安全：每个请求都要带 FastAPI 签的 `X-Context`（base64 JSON）+ `X-Signature`
（对这个 base64 串算的 HMAC-SHA256），`SignedRequestContextFilter` 验签名+
时间戳容差（默认 300 秒）+ nonce 防重放（进程内 Map，见类注释里"多实例部署已知限制"）；
每个 Controller 方法用 `ScopeGuard.require(...)` 显式检查 scope，不是"能连到接口就有权限"。

`enterprise-business-hub/src/test/java/.../LeaveControllerIntegrationTest.java`：6 个
`@SpringBootTest`（真实 HTTP + 真实 MySQL，不是 mock）——完整闭环、缺 scope 403、
签名错 401、余额不足 400、重复 Idempotency-Key 不重复建单、拒绝不扣余额。全绿。

### FastAPI 侧

`service/enterprise_hub_client.py`：签名 + 请求，复用 `service/http_resilience.py`
的超时/重试/熔断（跟 LLM/Embedding 调用同一套韧性策略，没有另起一套）。
`service/tools/oa_leave.py`：6 个 Agent 工具（`get_leave_balance`/`create_leave_draft`/
`submit_leave_request`/`approve_leave_request`/`reject_leave_request`/
`get_leave_status`），走既有的 `service/tools/` 零配置自动注册——不用改任何路由/
Agent 装配代码，HR Agent 绑上这些工具就能用。`tests/test_oa_leave_tools.py`（10 个，
mock 掉 `hub.call`，只验证工具层参数映射/scope/错误转换，不需要真实 Java 服务或 DB）。

手动全链路验证：`scripts/smoke_test_enterprise_hub.py`（真实签名 + 真实 HTTP，不进
`unittest discover`，需要先手动起 Java 服务）跑过一遍查余额(10)→建草稿→提交→
批准→查状态→余额扣减(8)，跟 JUnit 集成测试覆盖的是同一条链路，多一层"从
Python 客户端视角也真的能打通"的确认。

启动方式（本机验证过）：

```bash
# 1. 建库（只需一次）
mysql -uroot -p -e "CREATE DATABASE enterprise_business CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
# 2. 起服务（Flyway 自动建表+种子数据）
cd enterprise-business-hub
ENTERPRISE_DB_PASSWORD=<跟主项目 .env 的 DB_PASSWORD 一致> mvn spring-boot:run
# 3. FastAPI 侧 .env 补 ENTERPRISE_HUB_BASE_URL / ENTERPRISE_HUB_HMAC_SECRET（.env.example 有示例）
```

### 已知的简化（本地验证阶段，不是遗漏，见文档正文对应节）

- 数据库账号：本地开发跟主项目共用 root，第2节"最小数据库权限"原则要到真实部署
  时按需分账号，MVP 阶段不为了这一条把本地开发流程复杂化。
- nonce 防重放：进程内 Map，多实例部署时各实例互相看不到彼此的 nonce（见
  `NonceStore` 类注释）；生产多实例要换 Redis，跟主项目 Python 侧限流用 Redis
  是同一类问题，同一个解法方向。
- 幂等：没有加数据库级唯一约束抢占，极短时间内同 key 并发请求理论上可能都判定
  "没有记录"各跑一次（见 `IdempotencyService` 类注释）；请假场景操作频率低，
  这个窗口期实际发生概率极小，先不做互斥锁。
- 余额扣减用简单的"批准时扣减"，没有做"提交时预扣、拒绝时释放"这种库存占用模型；
  正确性对当前场景够用，账更严格的占用模型留给真有需求（比如需要看到"审批中"
  状态占了多少余额）再加。

### 还没做（阶段3/采购/CRM/SAP，按原计划顺序留白）

Phase 3D 阶段3（中央 Agent 受控路由）现在有了第一个真实目标（HR Agent + OA 请假
工具），可以回头接了，但这次没有顺带做——先把这条闭环单独验证完，路由逻辑是
下一步。采购/CRM/SAP Connector 仍然是空白，按第9.5节的顺序排在 OA 之后。

## 13. 执行结果（库存与采购闭环 + SAP Mock Connector，2026-09-27）

Phase 3D 阶段3（中央 Agent 受控路由）落地后回头做的第二个业务域，跟 OA 是同一套
骨架（`security`/`idempotency`/`audit` 三个共享包完全复用，没有重新写一遍）。

### 数据模型（`V2__init_procurement.sql`）

`Product`（产品+库存合一，不单独建 Inventory 表——第一版不做供应链全模块，
够用就行）、`DepartmentBudget`（部门+年度维度，跟 `LeaveBalance` 是同一种
"每人/每部门每年一行"模式）、`PurchaseRequest`（申请单头）、
`PurchaseRequestLine`（明细，下单时把 `Product.unitPrice` 快照进去，以后改价不
追溯历史单）、`PurchaseOrder`（批准后生成，供应商信息来自 SAP）。

**踩了一个坑**：`PurchaseRequestLine` 最初用 JPA 的 `@OneToMany(mappedBy 不填) +
@JoinColumn` 单向关联挂在 `PurchaseRequest` 上，指望 Hibernate 自动维护外键——
实测直接报 `Field 'purchase_request_id' doesn't have a default value`：Hibernate
对这种单向 `@OneToMany` 的标准做法是先插子表（不带外键）再单独 UPDATE 补外键，
但外键列是 NOT NULL，第一步插入就先失败了。改成明细表自己带 `purchaseRequestId`
普通外键列 + 独立 `PurchaseRequestLineRepository`（不用 JPA 级联，service 层显式
`save`），跟这个项目里其它表清一色"平铺 long 外键"的风格保持一致，顺便绕开了
这个坑——记在这儿，以后再建有子表的实体优先用这种写法，不要先试 `@OneToMany`。

### 流程

`GET /procurement/products/{sku}` 查库存（`belowSafetyStock` 由前端/Agent 自己
判断要不要下单，不强制"低于安全库存才能建草稿"）→ `GET /procurement/budget`
查部门预算 → `POST /procurement/requests`（多行明细，按 `Product.unitPrice` 算
`totalAmount`）→ `submit`（校验预算够不够）→ 部门负责人 `approve`（批准才扣预算 +
生成 `PurchaseOrder`，查 `SapConnector.getSupplier()` 拿供应商名称塞进去）/`reject`
（不扣预算不生成单）→ `GET /procurement/requests/{id}` 查状态（带出关联的采购单
信息）。跟 OA 的差异：采购操作都要求 `RequestContext.teamId` 不为空（没有部门就
不知道该查哪个部门的预算），Java 端 `requireTeamId()` 显式校验，Python 侧工具在
调用前就检查（`_resolve_team_id` 返回 None 直接报错，不会带着空 team_id 打过去）。

### SAP：只做了设计稿要求的最小范围

`SapConnector` 接口只有 `getSupplier()` + `healthCheck()`——设计稿列的
`getPurchaseOrder()`/`createPurchaseOrderDraft()` 没有做：生成采购单是本地的
`PurchaseOrder` 记录，不需要反向同步到 SAP 的采购单接口，除非真实企业接入后有
双向同步需求。`MockSapConnector`（`@Component`，Spring 直接装配，没有 Real 实现
也没关系）内置 3 个供应商代码返回固定数据，其余代码返回"未知供应商"但不报错。
真实企业提供接口后，新写一个 `RealSapConnector implements SapConnector`、换掉
Spring 里的 Bean（`@Primary` 或 profile），`ProcurementService`/Python 工具都不用改。

### 测试

`ProcurementControllerIntegrationTest`（5 个，真实 HTTP + 真实 MySQL）：完整闭环
（含库存/预算/生成采购单校验供应商名称）、缺 scope 403、预算不足 400、拒绝不扣
预算不生成单、幂等重放不重复建单。`service/tools/procurement.py`（6 个工具）+
`tests/test_procurement_tools.py`（9 个，mock 掉网络层，含"不属于任何部门直接
拒绝、不打后端"这条专门测试）。Java 侧 11 个测试（OA 6 + 采购 5）全绿，Python
766 个测试全绿。

### 还没做

CRM 客户跟进闭环、`RealSapConnector`、Docker Compose 把 `enterprise-business-hub`
接进 `docker-compose.prod.yml`。采购部门（`department_code="procurement"`）现在
可以真的用中央 Agent 路由过去了——阶段3的路由规则本来就列了这个部门，不需要
再改 `central_router.py`，只要真的创建一个 `agent_type="department"` +
`department_code="procurement"` 的 Agent 并绑上这 6 个工具即可。

## 14. 执行结果（CRM 客户跟进闭环，2026-09-27）

三个业务域的最后一个，跟 OA/采购同一套骨架，比它们简单：**没有审批环节**——设计稿
本来就没提"部门负责人审批"这一步，跟进记录只需要用户自己确认，商机没有状态机
约束（哪个阶段能转到哪个阶段不做限制，销售自己判断）。

### 数据模型（`V3__init_crm.sql`）

`Customer`（客户，带 `teamId` 做部门隔离）、`Contact`（联系人）、`FollowUp`
（跟进记录，`DRAFT → CONFIRMED` 两步，没有第三态）、`Opportunity`（商机，
`stage`/`amount` 可重复更新，`updatedAt` 跟踪最后一次改动）。这次没有重复
`PurchaseRequestLine` 那个 `@OneToMany` 坑——一开始就用平铺外键列
（`Contact.customerId`/`FollowUp.customerId`/`Opportunity.customerId`）+
独立 repository 按外键查，没有试图用 JPA 级联。

### 部门数据隔离

`CrmService.getCustomerInTeam()`：任何操作先查客户，校验
`customer.teamId == RequestContext.teamId`，不一致直接 404（不区分"客户不存在"
和"客户存在但不是你部门的"，跟 Python 侧 `access_control.py` 的隔离原则一样，
不暴露资源存在性）。`CrmControllerIntegrationTest.differentTeam_customerNotFound`
专门测了这条：同一个客户，用另一个部门的 `team_id` 去查会被当成不存在。

### 流程

`GET /crm/customers/{id}` 查摘要（联系人 + 最近 10 条跟进 + 全部商机，给销售
Agent 生成摘要用，摘要文字本身由 Agent 自己生成，这里只管把数据给全）→
`POST .../followups` 建草稿 → `POST /crm/followups/{id}/confirm` 确认 →
`POST .../opportunities`（`opportunityId` 不填新建、填了更新那一条——
`CrmControllerIntegrationTest` 里验证了更新同一条不会变成两条）。

### 测试

`CrmControllerIntegrationTest`（4个，真实HTTP+MySQL）：完整闭环（摘要→草稿→
确认→建商机→更新商机）、跨部门查客户404、缺scope 403、幂等重放不重复建跟进。
`service/tools/crm.py`（5个工具：get_customer_summary/create_followup_draft/
submit_customer_followup/create_or_update_opportunity/get_opportunities）+
`tests/test_crm_tools.py`（8个，mock网络层）。Java 15个测试全绿（OA6+采购5+
CRM4），Python 774个测试全绿。

### 三个业务域到这里全部做完，还剩

`RealSapConnector`（等真实企业提供接口）、Docker Compose 把
`enterprise-business-hub` 接进 `docker-compose.prod.yml`（本地一直是手动
`mvn spring-boot:run` 起的，没有容器化和生产部署配置）、E2E/越权/重放/重复提交/
事务测试（三个模块各自的集成测试已经覆盖了这些场景，按模块测的，没有再写一份
跨模块的端到端测试）。销售部门（`department_code="sales"`）现在也能被中央
Agent路由过去了，跟采购一样不用改路由代码，建一个绑好这5个工具的 Agent 即可。

## 15. 执行结果（Docker Compose 接入 + 真实容器验证，2026-09-28）

`docker-compose.prod.yml` 新增 `enterprise-hub` 服务（多阶段构建：
`maven:3.9-eclipse-temurin-21` 编译 → `eclipse-temurin:21-jre-alpine` 运行），
`db` 服务挂上 `deploy/mysql-init/01-create-enterprise-business-db.sql`，第一次
初始化（数据卷为空）时自动建好 `enterprise_business` 库。只绑 `127.0.0.1:8090`，
不直接对外，跟 `api` 服务一个模式。

首次接入时本机 Docker daemon 没起，只跑了 `docker compose config` 做语法检查，
容器级验证当时留白。这次把 Docker Desktop 启动起来后补了一次真实验证（独立
project `enthub-verify`，一次性密码/密钥，跑完整个 `down -v` 清干净，没碰任何
正式环境的卷）：

- `docker compose build enterprise-hub` 真实构建镜像，不是只检查语法。
- `docker compose up -d db enterprise-hub`：`db` healthcheck 通过，
  `mysql-init` 脚本确认真的建出了 `enterprise_business` 库（`SHOW DATABASES`
  验证过）。
- `enterprise-hub` 容器里 Flyway 在真实 MySQL 上依次跑完 3 个迁移
  （V1 OA、V2 采购、V3 CRM），Hibernate 校验通过，容器 healthcheck 变
  `healthy`，日志里没有异常。
- 宿主机直接 `curl http://127.0.0.1:8090/actuator/health` 拿到
  `{"status":"UP"}`，确认端口绑定和 healthcheck 配置本身没问题（不是只在
  容器网络内部能通）。

到这里 Docker Compose 这块从"语法验证过，容器级没验证过"变成"真实构建 +
真实启动 + 真实健康检查全部跑通过"。剩下还没做的只有 `RealSapConnector`（等
真实企业提供接口）和销售 Agent 的界面接入（后端工具已经好了，见第14节）。
