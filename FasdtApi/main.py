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
from FasdtApi.automation_work import router as automation_work_router
from FasdtApi.work_center import router as work_center_router
from FasdtApi.evaluation import router as evaluation_router
from FasdtApi.web_monitor import router as web_monitor_router
from FasdtApi.user_widget import router as user_widget_router
from FasdtApi.notification_channel import router as notification_channel_router
from FasdtApi.agent_pipeline import router as agent_pipeline_router
from FasdtApi.attachment_route import router as attachment_router
from FasdtApi.approval_route import router as approval_router
from models.async_db import async_engine
from models.init_db import SessionLocal, engine, bootstrap_database, User
from service.operation_log_middleware import OperationLogMiddleware
from service.background_task_service import task_execution_mode
from service.http_resilience import circuit_breaker
from service.metrics_async_service import async_metrics_response
from service.metrics_service import update_runtime_metrics
from service.request_context_middleware import RequestContextMiddleware
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
    try:
        yield
    finally:
        await async_engine.dispose()


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
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Process-Time"],
)
app.add_middleware(OperationLogMiddleware)
app.add_middleware(RequestContextMiddleware)

# 用绝对路径挂载 static 目录，避免依赖启动时的工作目录
BASE_DIR = Path(__file__).resolve().parent.parent
app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='my_static')
app.include_router(login_router)
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
app.include_router(automation_work_router)
app.include_router(work_center_router)
app.include_router(evaluation_router)
app.include_router(web_monitor_router)
app.include_router(user_widget_router)
app.include_router(notification_channel_router)
app.include_router(agent_pipeline_router)
app.include_router(attachment_router)
app.include_router(approval_router)

_error_logger = get_logger("app_error")


@app.exception_handler(AppError)
async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """领域异常统一出口：按 code 记一行日志，按 http_status 返回 {detail, code}。"""
    log = _error_logger.warning if exc.http_status < 500 else _error_logger.error
    log(f"[{exc.code}] {request.method} {request.url.path} -> {exc.message}"
        + (f" | {exc.context}" if exc.context else ""))
    return JSONResponse(status_code=exc.http_status, content={"detail": exc.message, "code": exc.code})


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


@app.get("/health", summary="服务健康检查（公开，仅返回是否正常）")
async def health_check():
    """给 Docker healthcheck / 负载均衡这类不带登录态的探活用。
    只回布尔值，详细诊断数据见 `/system/diagnose`（需要登录）。"""
    payload = await _build_health_payload()
    return {"ok": payload["ok"]}


@app.get("/system/diagnose", summary="详细运行诊断（需要登录）")
async def system_diagnose(current_user: User = Depends(get_current_user_async)):
    """设置页 / 管理后台的诊断面板用，完整数据同旧版 `/health`。"""
    return await _build_health_payload()


@app.get("/metrics", summary="Prometheus 指标")
async def prometheus_metrics():
    return await async_metrics_response()


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}
