import os
from contextlib import asynccontextmanager
from pathlib import Path

from service.config_validation import assert_runtime_config, validate_runtime_config

# 生产配置必须在导入路由和数据库模型前完成校验。
# 这样缺少 Redis、使用占位密钥、CORS/Host 未收紧等问题会在启动早期失败，
# 避免应用已经连接数据库或初始化业务模块后才暴露配置错误。
assert_runtime_config()

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from sqlalchemy import text
from service.exceptions import AppError
from utils.logger_handler import get_logger
from FasdtApi.login import router as login_router
from FasdtApi.auth_token import router as auth_token_router
from FasdtApi.agent import router as agent_router
from FasdtApi.llm_config import router as llm_config_router
from FasdtApi.chat import router as chat_router
from FasdtApi.knowledge import router as knowledge_router
from FasdtApi.knowledge_space import router as knowledge_space_router
from FasdtApi.rag_debug import router as rag_debug_router
from FasdtApi.agent_run import router as agent_run_router
from FasdtApi.skill_route import router as skill_router
from FasdtApi.conversation_route import router as conversation_router
from FasdtApi.memory import router as memory_router
from FasdtApi.background_task import router as background_task_router
from FasdtApi.admin import router as admin_router
from FasdtApi.organization_admin import router as organization_admin_router
from FasdtApi.enterprise_workspace import router as enterprise_workspace_router
from FasdtApi.finance_vouchers import router as finance_vouchers_router
from FasdtApi.it_service import router as it_service_router
from FasdtApi.hr_cases import router as hr_cases_router
from FasdtApi.orchestration import router as orchestration_router
from FasdtApi.responsibility import router as responsibility_router
from FasdtApi.attendance import router as attendance_router
from FasdtApi.issues import admin_router as issues_admin_router, client_router as client_errors_router, dept_router as issues_dept_router, events_router
from FasdtApi.automation_work import router as automation_work_router
from FasdtApi.work_center import router as work_center_router
from FasdtApi.evaluation import router as evaluation_router
from FasdtApi.web_monitor import router as web_monitor_router
from FasdtApi.user_widget import router as user_widget_router
from FasdtApi.notification_channel import router as notification_channel_router
from FasdtApi.agent_pipeline import router as agent_pipeline_router
from FasdtApi.attachment_route import router as attachment_router
from FasdtApi.approval_route import router as approval_router
from FasdtApi.integrations import admin_router as integrations_admin_router, public_router as integrations_public_router
from models.async_db import async_engine
from models.init_db import SessionLocal, engine, bootstrap_database, User
from service.operation_log_middleware import OperationLogMiddleware
from service.background_task_service import task_execution_mode
from service.http_resilience import circuit_breaker
from service import health as health_probe
from service.metrics_async_service import async_metrics_response
from service.metrics_service import update_runtime_metrics
from service.request_context_middleware import RequestContextMiddleware
from service.session_renewal import SessionRenewalMiddleware
from service.security_middleware import SecurityHeadersMiddleware
from service.dependencies import get_current_user_async
from utils.cache import config_cache, skill_cache, verification_cache
from utils.rate_limit import concurrency_limiter, rate_limiter

def _env_list(name: str, default: str) -> list[str]:
    """读取逗号分隔的环境变量列表。"""

    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量，配置错误时回退默认值。"""

    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建表 / 幂等迁移 / 内置管理员初始化：只在服务启动时执行，不在模块导入时执行。
    # DDL 是同步阻塞操作，放线程池避免占用事件循环。
    await run_in_threadpool(bootstrap_database)
    # 全新部署第一次启动：自动建好平台服务的那家企业（已有就什么都不做；见 service/enterprise_bootstrap.py）
    from service import enterprise_bootstrap
    await run_in_threadpool(enterprise_bootstrap.ensure_on_startup)
    from service.observability import otel
    await run_in_threadpool(otel.init)       # 设置了 OTEL_EXPORTER_OTLP_ENDPOINT 才启用；失败只记警告
    from service.events import handlers  # noqa: F401  —— 导入即注册所有事件消费者
    from service.events.runner import Runner, enabled as event_runner_enabled
    runner = Runner() if event_runner_enabled() else None
    if runner:
        runner.start()   # 第一轮会接着处理上次进程退出时遗留的事件
    try:
        yield
    finally:
        if runner:
            await runner.stop()
        await async_engine.dispose()
        await run_in_threadpool(otel.shutdown)   # 退出前尽量送出队列里的遥测，Collector 不可用时最多等几秒


app = FastAPI(lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=_env_list("TRUSTED_HOSTS", "127.0.0.1,localhost,api"),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_env_list("CORS_ALLOW_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID", "X-CSRF-Token", "traceparent", "X-Background-Poll"],
    expose_headers=["X-Request-ID", "X-Trace-ID", "X-Process-Time"],
)
app.add_middleware(OperationLogMiddleware)
app.add_middleware(SessionRenewalMiddleware)   # 一直在用的浏览器会话自动续期（service/session_renewal.py）
app.add_middleware(RequestContextMiddleware)

# 用绝对路径挂载 static 目录，避免依赖启动时的工作目录
BASE_DIR = Path(__file__).resolve().parent.parent
app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='my_static')
app.include_router(login_router)
app.include_router(auth_token_router)
app.include_router(agent_router)
app.include_router(llm_config_router)
app.include_router(chat_router)
app.include_router(knowledge_router)
app.include_router(knowledge_space_router)
app.include_router(rag_debug_router)
app.include_router(agent_run_router)
app.include_router(skill_router)
app.include_router(conversation_router)  # 注册会话路由
app.include_router(memory_router)
app.include_router(background_task_router)
app.include_router(admin_router)
app.include_router(organization_admin_router)
app.include_router(enterprise_workspace_router)
app.include_router(finance_vouchers_router)
app.include_router(it_service_router)
app.include_router(hr_cases_router)
app.include_router(orchestration_router)
app.include_router(responsibility_router)
app.include_router(attendance_router)
app.include_router(issues_admin_router)
app.include_router(issues_dept_router)
app.include_router(client_errors_router)
app.include_router(events_router)
app.include_router(automation_work_router)
app.include_router(work_center_router)
app.include_router(evaluation_router)
app.include_router(web_monitor_router)
app.include_router(user_widget_router)
app.include_router(notification_channel_router)
app.include_router(agent_pipeline_router)
app.include_router(attachment_router)
app.include_router(approval_router)
app.include_router(integrations_public_router)
app.include_router(integrations_admin_router)

_error_logger = get_logger("app_error")

from service.observability import dependency_events  # noqa: E402

dependency_events.install()
from service.observability import sentry_setup  # noqa: E402

sentry_setup.init()


def _trace_of(request: Request) -> str:
    from service.observability import context as trace_context
    return getattr(request.state, "trace_id", None) or trace_context.current_trace_id() or trace_context.new_trace_id()


def _operation_of(request: Request) -> str:
    route = request.scope.get("route")
    return f"{request.method} {getattr(route, 'path', None) or request.url.path}"


def _error_response(request: Request, *, http_status: int, code: str, detail: str | None = None, issue_no: str | None = None,
                    retry_after: int | None = None) -> JSONResponse:
    """统一错误结构。detail 保持兼容（前端一直读它）；其余字段给排障和重试用，永远不含内部堆栈。"""
    from service.observability.error_codes import spec_for
    spec = spec_for(code, http_status)
    trace_id = _trace_of(request)
    body = {"detail": detail or spec.message, "code": code, "message": detail or spec.message, "trace_id": trace_id,
            "retryable": spec.retryable, "suggestion": spec.suggestion}
    if issue_no:
        body["issue_no"] = issue_no
    wait = retry_after if retry_after is not None else spec.retry_after
    headers = {"X-Trace-ID": trace_id, "X-Request-ID": getattr(request.state, "request_id", trace_id)}
    if wait and spec.retryable:
        body["retry_after"] = wait
        headers["Retry-After"] = str(wait)
    return JSONResponse(status_code=http_status, content=body, headers=headers)


async def _report_issue(request: Request, *, code: str, http_status: int, exc: BaseException, message: str) -> str | None:
    """5xx 与依赖故障进问题中心（同一种故障聚合成一条）；记录失败不影响响应。"""
    from starlette.concurrency import run_in_threadpool
    from service.observability.issues import record_occurrence
    from service.observability import sentry_setup
    team = request.query_params.get("team_id")
    event_id = await run_in_threadpool(lambda: sentry_setup.capture(
        exc, trace_id=_trace_of(request), operation=_operation_of(request), error_code=code,
        department_id=int(team) if team and team.isdigit() else None))
    result = await run_in_threadpool(
        lambda: record_occurrence(sentry_event_id=event_id, error_code=code, http_status=http_status, operation=_operation_of(request), exc=exc, message=message,
                                  trace_id=_trace_of(request), department_id=int(team) if team and team.isdigit() else None,
                                  extra={"method": request.method, "path": str(request.url.path)}))
    return result["issue_no"] if result else None


@app.exception_handler(AppError)
async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """领域异常统一出口：按 code 记一行日志，按 http_status 返回统一错误结构；依赖/系统故障（5xx）同时进问题中心。"""
    from service.observability.error_codes import should_report
    log = _error_logger.warning if exc.http_status < 500 else _error_logger.error
    log(f"[{exc.code}] {request.method} {request.url.path} -> {exc.message}"
        + (f" | {exc.context}" if exc.context else ""))
    issue_no = None
    if should_report(exc.code, exc.http_status):
        issue_no = await _report_issue(request, code=exc.code, http_status=exc.http_status, exc=exc, message=exc.message)
    return _error_response(request, http_status=exc.http_status, code=exc.code, detail=exc.message, issue_no=issue_no,
                           retry_after=exc.context.get("retry_after") if exc.context else None)


@app.exception_handler(Exception)
async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """未处理的异常：只在服务端记一份完整堆栈，用户拿到的是 trace_id 和问题编号，没有内部细节。
    Java 业务服务不可用/熔断等依赖故障有明确的错误码，不当成代码缺陷。"""
    from service.observability.dependency_errors import classify
    code, status = classify(exc)
    _error_logger.error(f"[{code}] {request.method} {request.url.path} trace={_trace_of(request)} {type(exc).__name__}: {exc}",
                        exc_info=(status == 500))
    issue_no = await _report_issue(request, code=code, http_status=status, exc=exc, message=f"{type(exc).__name__}: {exc}")
    return _error_response(request, http_status=status, code=code, issue_no=issue_no)


app.state.unhandled_handler = _handle_unexpected_error   # RequestContextMiddleware 就地使用，避免同一次故障被重复打印堆栈


@app.get("/")
async def root():
    return {"message": "Hello World"}


async def _build_health_payload() -> dict:
    """探活 + 详细运行诊断（DB 连接池、缓存后端、限流器、熔断器状态等）。

    这份数据面向"自己人"：要么是不认识别人的基础设施探活（只看返回是不是
    2xx，不解析内容），要么是登录用户在设置页/管理后台看运行状态。之前
    `/health` 把这整份详细数据公开给任何匿名请求，相当于把数据库连接池大小、
    缓存/限流用的是不是 Redis、后台任务执行模式这些内部架构细节告诉了互联网上
    任何知道这个 URL 的人——对判断"这套系统防护弱不弱"是有效的踩点信息。
    现在拆成两层：`/health` 只回 {"ok": bool}，这份完整数据挪到需要登录才能
    访问的 `/system/diagnose`。
    """
    checks = []
    config_status = validate_runtime_config()

    def add_check(name: str, ok: bool, message: str = ""):
        checks.append({"name": name, "ok": ok, "message": message})

    for item in config_status["checks"]:
        add_check(item["name"], item["ok"], item["message"])

    redis_url = os.getenv("REDIS_URL")
    redis_enabled = bool(redis_url)
    config_cache_stats = config_cache.stats()
    skill_cache_stats = skill_cache.stats()
    verification_cache_stats = verification_cache.stats()
    add_check(
        "redis",
        (not redis_enabled)
        or config_cache_stats.get("redis_ok", False)
        or skill_cache_stats.get("redis_ok", False)
        or verification_cache_stats.get("redis_ok", False),
        "未配置 REDIS_URL，当前使用内存缓存"
        if not redis_enabled
        else ("连接正常" if config_cache_stats.get("redis_ok") or skill_cache_stats.get("redis_ok") or verification_cache_stats.get("redis_ok") else "Redis 不可用，已回退内存缓存"),
    )
    sms_provider = os.getenv("SMS_PROVIDER", "console").strip().lower()
    sms_configured = (
        (sms_provider == "webhook" and bool(os.getenv("SMS_WEBHOOK_URL")))
        or (sms_provider == "aliyun" and all(os.getenv(name) for name in (
            "ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
            "SMS_ALIYUN_SIGN_NAME", "SMS_ALIYUN_TEMPLATE_CODE",
        )))
    )
    add_check(
        "sms",
        sms_provider == "console" or sms_configured,
        "本地日志验证码模式" if sms_provider == "console" else ("短信服务已配置" if sms_configured else "短信服务配置不完整"),
    )

    def check_database():
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            return True, "连接正常"
        except Exception as e:
            return False, str(e)
        finally:
            try:
                db.close()
            except Exception:
                pass

    db_ok, db_message = await run_in_threadpool(check_database)
    add_check("database", db_ok, db_message)

    pool_stats = {}
    pool = getattr(engine, "pool", None)
    if pool:
        for field, reader in {
            "size": getattr(pool, "size", None),
            "checked_in": getattr(pool, "checkedin", None),
            "checked_out": getattr(pool, "checkedout", None),
            "overflow": getattr(pool, "overflow", None),
        }.items():
            if callable(reader):
                try:
                    pool_stats[field] = reader()
                except Exception:
                    pool_stats[field] = None

    required_paths = {
        "static": BASE_DIR / "static",
        "knowledge_files": BASE_DIR / "knowledge_files",
        "skills": BASE_DIR / "skills",
    }
    if not os.getenv("CHROMA_SERVER_HOST"):
        required_paths["vector_db"] = BASE_DIR / "vector_db"
    for name, path in required_paths.items():
        add_check(name, path.exists(), str(path))

    cache_payload = {
        "config": config_cache_stats,
        "skill": skill_cache_stats,
        "verification": verification_cache_stats,
    }
    update_runtime_metrics(pool_stats, cache_payload)

    ok = all(item["ok"] for item in checks)
    return {
        "ok": ok,
        "checks": checks,
        "config": config_status,
        "cache": cache_payload,
        "limits": {
            "rate": rate_limiter.stats(),
            "concurrency": concurrency_limiter.stats(),
        },
        "database": {
            "pool": pool_stats,
        },
        "tasks": {
            "execution_mode": task_execution_mode(),
            "worker_required": task_execution_mode() == "worker",
            "running_timeout_seconds": _env_int("TASK_RUNNING_TIMEOUT_SECONDS", 1800),
            "max_auto_retries": _env_int("TASK_MAX_AUTO_RETRIES", 2),
            "retry_base_seconds": _env_int("TASK_RETRY_BASE_SECONDS", 30),
            "retry_max_seconds": _env_int("TASK_RETRY_MAX_SECONDS", 300),
        },
        "resilience": {
            "circuits": circuit_breaker.stats(),
        },
    }


@app.get("/live", summary="存活探针（公开，永远轻量）")
async def live_probe():
    """进程还活着就返回 200，不查任何依赖。"""
    return health_probe.live()


async def _ready_response():
    result = await health_probe.ready()
    return JSONResponse(result, status_code=200 if result["ok"] else 503)


@app.get("/ready", summary="就绪探针（公开，依赖故障时返回 503）")
async def ready_probe():
    """数据库不可用（或 READY_REQUIRE_REDIS=1 时 Redis 不可用）返回 503，Docker healthcheck / 负载均衡据此摘掉实例。
    只回布尔值（ok / degraded），详细诊断见 `/system/diagnose`（需要登录）。"""
    return await _ready_response()


@app.get("/health", summary="服务健康检查（公开；等同 /ready，依赖故障时返回 503）")
async def health_check():
    """沿用的旧地址：语义与 `/ready` 相同。以前不管依赖是否可用都返回 200，容器永远是 healthy。"""
    return await _ready_response()


@app.get("/system/diagnose", summary="详细运行诊断（需要登录）")
async def system_diagnose(current_user: User = Depends(get_current_user_async)):
    """设置页 / 管理后台的诊断面板用，完整数据同旧版 `/health`。"""
    return await _build_health_payload()


@app.get("/system/readiness", summary="运行就绪检查（需要登录）：数据库、迁移、企业业务服务、模型、提醒任务、演示数据")
async def system_readiness(current_user: User = Depends(get_current_user_async)):
    from service import readiness
    return await readiness.collect()


@app.get("/metrics", summary="Prometheus 指标")
async def prometheus_metrics():
    return await async_metrics_response()


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}
