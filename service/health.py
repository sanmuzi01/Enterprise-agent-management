"""存活（live）与就绪（ready）探针：给 Docker healthcheck / 负载均衡 / Kubernetes 用，不带登录态，只回布尔值。

两个问题不能混在一起：
  live   进程还活着、能响应请求吗？—— 永远轻量、不查任何依赖；失败才需要重启进程。
  ready  现在能不能接业务流量？—— 查数据库（必须）和 Redis；失败返回 503，让负载均衡把这个实例摘掉、容器标成 unhealthy。
之前 /health 无论数据库 / Redis 是否可用都返回 200，容器一直是 healthy，负载均衡还会继续往坏实例转发。

就绪的判定：
  · 数据库连不上 / 超时 → 不就绪（硬依赖）；
  · Redis 配置了却连不上 → “降级”（degraded=true，并有 Prometheus 指标和告警），默认仍然就绪：
    限流 / 并发控制会按策略收紧（见 utils/rate_limit.py），而不是 Redis 抖一下所有实例同时被摘掉造成整体不可用。
    如果你的部署更看重一致性，设 READY_REQUIRE_REDIS=1，Redis 不可用时也判为不就绪。
详细诊断（连接池、缓存、熔断器…）不在这里公开，见需要登录的 /system/diagnose。
"""
import asyncio
import os
from typing import Dict

from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from utils import limiter_metrics
from utils.redis_client import RedisClientManager, configured as redis_configured

_redis = RedisClientManager(decode_responses=True)


def live() -> Dict[str, bool]:
    return {"ok": True}


def _timeout_seconds() -> float:
    try:
        return max(0.5, float(os.getenv("READY_TIMEOUT_SECONDS", "3")))
    except ValueError:
        return 3.0


def _check_database() -> bool:
    from models.init_db import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


async def ready() -> Dict[str, bool]:
    """返回 {"ok": 能否接业务流量, "degraded": 是否处于降级}；不返回任何内部细节。"""
    try:
        database_ok = await asyncio.wait_for(run_in_threadpool(_check_database), timeout=_timeout_seconds())
    except asyncio.TimeoutError:
        database_ok = False
    redis_ok = True
    if redis_configured():
        try:
            redis_ok = await asyncio.wait_for(run_in_threadpool(_redis.is_available), timeout=_timeout_seconds())
        except asyncio.TimeoutError:
            redis_ok = False
        limiter_metrics.set_redis_up(redis_ok)
    require_redis = os.getenv("READY_REQUIRE_REDIS", "0") == "1"
    return {"ok": bool(database_ok and (redis_ok or not require_redis)), "degraded": not redis_ok}
