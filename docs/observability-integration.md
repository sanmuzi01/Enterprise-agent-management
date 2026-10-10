# 真实基础设施联调记录：Kafka 与 OpenTelemetry / Loki / Tempo / Prometheus / Grafana

本文记录两件事在**真实组件**上的验证结果（不是假 Broker、不是模拟），以及怎么复现、怎么在生产里配置。

- Kafka 消费端：`scripts/e2e_kafka.py`，28 项
- 链路 / 日志 / 指标 / 中断恢复：`scripts/e2e_observability.py`，40 项

## 一、起观测栈

```bash
docker compose -f deploy/observability/docker-compose.yml up -d
```

包含 Kafka（KRaft，单节点）、OpenTelemetry Collector、Loki、Tempo、Prometheus、Grafana，端口都只绑定 127.0.0.1。
Grafana 默认账号 `admin` / `admin-local-only`（仅本机联调用，生产必须换）。

## 二、应用侧怎么接

| 环境变量 | 作用 |
| --- | --- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 例如 `http://127.0.0.1:4317`。**不设置就完全不启用**，业务代码无需判断 |
| `SERVICE_NAME` | 在 Tempo / Loki 里的服务名，默认 `agent-service` |
| `OTEL_TRACE_SAMPLE_RATIO` | 采样比例，默认 1.0（按 trace_id 取舍，各服务一致） |
| `OTEL_EXPORT_TIMEOUT_SECONDS` | 单次导出超时，默认 3 秒 |
| `SENTRY_DSN` | 不设置就不启用 Sentry；设置了但不可达也不影响业务 |
| `KAFKA_BOOTSTRAP_SERVERS` | 设置后事件走 Kafka（消费者组 / 手动提交 / 死信 Topic），不设置走数据库轮询 |
| `TRUSTED_HOSTS` | Prometheus 在容器里抓取宿主机后端时，Host 是 `host.docker.internal`，要加进白名单，否则 `/metrics` 返回 400 |

span 种类：服务端（以**路由模板**命名，如 `POST /enterprise/oa/leave/mine`）、业务系统调用（`enterprise_hub <operation>`）、
事件发布（`publish <topic>`）、事件消费（`consume <consumer>`）。`/health`、`/metrics` 不记链路。
span 只带方法、路由模板、状态码、业务操作名；不带查询串、请求头、请求体，错误消息先脱敏再截断。
送去 Loki 的日志同样先脱敏、不带异常堆栈（堆栈留在 Sentry 和问题中心）；每个请求结束时有一条
`METHOD 路由模板 -> 状态码 耗时` 的完成日志，按 trace_id 就能在 Loki 里串起整次请求。

trace_id 的约定：Tempo 里的 trace id、Loki 里的 `trace_id`、响应头 `X-Trace-ID`、问题中心里的 trace_id **是同一个值**
（span 的父上下文用我们自己的 32 位 trace_id 构造）。

## 三、联调结果（scripts/e2e_observability.py，40/40）

| 场景 | 结果 |
| --- | --- |
| 一次真实请求 FastAPI → Java → 事件发布 → 事件消费 | Tempo 里同一个 trace 有 server / client / producer / consumer 四类 span，client 的父节点是 server |
| 日志 | Loki 按 trace_id 能查到该请求的日志，没有口令和令牌 |
| 探活 | `/health` 不产生链路 |
| Prometheus / Grafana | 抓到后端 `/metrics`（up=1）；Loki、Prometheus、Tempo 三个数据源可用 |
| **Collector 停掉** | 期间 40 个请求全部成功，耗时中位数没有变长（70ms → 63ms）；恢复后新的链路和日志自动继续 |
| **Loki 停掉**（云日志中断的等价场景） | 期间请求成功；恢复后中断期间产生的日志由 Collector 重试队列补送，不丢 |
| **Tempo 停掉** | 期间请求成功；恢复后中断期间的链路被补送，不丢 |
| Sentry 没配置 | 初始化返回 False，上报是空操作 |
| Sentry 配置了但地址不可达 | 初始化成功；连续上报 50 次 0.4 秒，未抛异常；退出 flush 有超时上限 |

云日志走的是同一条 OTLP 通路：把 `deploy/observability/otel-collector.yml` 里的 exporter 换成云厂商的（Grafana Cloud、阿里云 SLS 等）即可，
应用侧不用改。本项目没有云日志账号，所以“云端中断”用 Loki 中断来验证同一机制（Collector 的 `sending_queue` + `retry_on_failure`，最长重试 120 秒）；
**用真实云账号验证没有做**。Sentry 同样没有真实 DSN，只验证了“未配置 / 不可达不影响业务”，没有验证事件真的出现在 Sentry 界面。

## 四、联调中发现并修复的问题

1. **span 全部被丢弃**：最初用了全局的空 tracer，而不是自己 provider 的 tracer。单元测试里用内存导出器看不出来，只有真实 Collector / Tempo 上查不到链路才暴露。
   现在单测断言“init 之后开出的 span 必须在记录”。
2. **成功的请求没有任何应用日志**：uvicorn 的访问日志不经过根 logger，Loki 里按 trace_id 查不到。新增请求完成日志（没有 handler，只送 Loki，不刷控制台）。
3. **Prometheus 抓 `/metrics` 返回 400**：`TRUSTED_HOSTS` 不含 `host.docker.internal`。
4. **OTel 导出失败日志限流挂错了 logger**：过滤器要挂在真正出错的子 logger 上（`opentelemetry.exporter.otlp.proto.grpc.exporter` 等），挂在父 logger 上无效。
5. Grafana 的 Tempo 数据源没有实现 `/health`，联调脚本改为经 Grafana 代理访问 Tempo 的 `/api/echo`。
6. 之前的 Kafka 联调（4 个问题）见 `scripts/e2e_kafka.py` 顶部说明和提交记录。

## 五、复现

```bash
docker compose -f deploy/observability/docker-compose.yml up -d
.venv\Scripts\python.exe scripts\e2e_observability.py
.venv\Scripts\python.exe scripts\e2e_kafka.py
docker compose -f deploy/observability/docker-compose.yml down -v
```

`e2e_observability.py` 要求 8011 端口没有在运行的后端（它要带上 OTLP 环境变量重新启动）；会自己启动 Java 业务服务（8090 已有则沿用），
中途会依次停止 / 启动 Collector、Loki、Tempo 容器，结束后（包括中途失败）都会把它们拉起来，并清理测试数据。
