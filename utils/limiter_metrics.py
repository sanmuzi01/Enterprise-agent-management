"""Redis 不可用时限流 / 并发控制的降级指标（Prometheus）。

之前 Redis 一挂，限流和并发控制就悄悄退回各进程自己的内存——多个 Worker 时攻击者能用的额度会乘以进程数，
进程重启额度还会清零，而且只有登录后的诊断页看得到。现在每次降级都计数，并有告警规则（deploy/prometheus-rules.yml）。
prometheus_client 不可用时所有函数是空操作。
"""
try:
    from prometheus_client import Counter, Gauge
except ImportError:  # pragma: no cover
    Counter = Gauge = None

if Counter:
    LIMITER_DEGRADED = Counter(
        "agent_limiter_redis_degraded_total",
        "Redis 不可用时限流 / 并发控制被迫降级的次数",
        ("limiter", "mode"),
    )
    REDIS_UP = Gauge("agent_redis_up", "Redis 是否可用（1 可用，0 不可用；没有配置 Redis 时不设置）")
else:  # pragma: no cover
    LIMITER_DEGRADED = REDIS_UP = None


def observe_degraded(limiter: str, mode: str) -> None:
    """mode：local（各进程独立内存）/ strict（内存且额度按进程数均分）/ closed（直接拒绝）。"""
    if LIMITER_DEGRADED is not None:
        LIMITER_DEGRADED.labels(limiter=limiter, mode=mode).inc()
    set_redis_up(False)


def set_redis_up(up: bool) -> None:
    if REDIS_UP is not None:
        REDIS_UP.set(1 if up else 0)
