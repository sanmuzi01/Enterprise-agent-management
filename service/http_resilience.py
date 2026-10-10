import os
import random
import threading
import time
import asyncio
from dataclasses import dataclass
from typing import Callable, Dict, Optional

import httpx
import requests

from utils.logger_handler import get_logger
from utils.redis_client import RedisClientManager

logger = get_logger("http_resilience")

_KNOWN_SERVICES_KEY = "circuit:known_services"


class CircuitOpenError(Exception):
    """目标服务熔断打开时抛出，避免继续打满外部服务。"""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class CircuitState:
    failures: int = 0
    opened_until: float = 0


class CircuitBreaker:
    """熔断器：配了 `REDIS_URL` 时多个 API/Worker 进程共享同一份熔断状态
    （Redis 不可用/未配置时退回进程内 Map），跟 `utils/rate_limit.py` 的限流器
    是同一个"Redis 优先、异常/未配置就掉回内存"的模式，不是另起一套。

    Redis key：`circuit:{name}:failures`（计数，TTL=冷却时长，冷却期内没有新失败就
    自然清零）、`circuit:{name}:opened_until`（打开的熔断到什么时候，TTL=冷却时长）、
    `circuit:known_services`（一个 Set，记录见过哪些服务名，只给 `stats()` 遍历用，
    不参与熔断判断本身）。
    """

    def __init__(self):
        # 状态变化监听：listener(name, "opened" | "recovered")。只在状态真的变化时触发一次，
        # 而不是每个失败请求都触发——否则一次故障会刷出成百上千条一样的告警。
        self.listener: Optional[Callable[[str, str], None]] = None
        self._announced_open: set = set()
        self._states: Dict[str, CircuitState] = {}
        self._lock = threading.RLock()
        self._redis = RedisClientManager(decode_responses=True)

    def before_call(self, name: str) -> None:
        client = self._redis.get_client()
        if client:
            try:
                opened_until = client.get(f"circuit:{name}:opened_until")
                if opened_until:
                    remaining = float(opened_until) - time.time()
                    if remaining > 0:
                        raise CircuitOpenError(f"{name} 暂时不可用，熔断保护中，请 {int(remaining) or 1} 秒后重试")
                return
            except CircuitOpenError:
                raise
            except Exception:
                self._redis.mark_failed()

        now = time.time()
        with self._lock:
            state = self._states.get(name)
            if state and state.opened_until > now:
                retry_after = int(state.opened_until - now) or 1
                raise CircuitOpenError(f"{name} 暂时不可用，熔断保护中，请 {retry_after} 秒后重试")

    def _announce(self, name: str, state: str) -> None:
        if state == "opened":
            if name in self._announced_open:
                return
            self._announced_open.add(name)
        else:
            if name not in self._announced_open:
                return
            self._announced_open.discard(name)
        if self.listener:
            try:
                self.listener(name, state)
            except Exception:  # noqa: BLE001 —— 监听失败不能影响请求
                logger.warning("熔断状态监听失败", exc_info=True)

    def record_success(self, name: str) -> None:
        self._announce(name, "recovered")
        client = self._redis.get_client()
        if client:
            try:
                client.delete(f"circuit:{name}:failures", f"circuit:{name}:opened_until")
                return
            except Exception:
                self._redis.mark_failed()

        with self._lock:
            self._states.pop(name, None)

    def record_failure(self, name: str) -> None:
        threshold = _env_int("HTTP_CIRCUIT_FAILURE_THRESHOLD", 5)
        cooldown = _env_int("HTTP_CIRCUIT_COOLDOWN_SECONDS", 30)

        client = self._redis.get_client()
        if client:
            try:
                failures_key = f"circuit:{name}:failures"
                failures = client.incr(failures_key)
                if failures == 1:
                    client.expire(failures_key, cooldown)
                client.sadd(_KNOWN_SERVICES_KEY, name)
                if threshold > 0 and failures >= threshold:
                    client.set(f"circuit:{name}:opened_until", time.time() + cooldown, ex=cooldown)
                    logger.warning(f"外部服务熔断打开: service={name}, failures={failures}, cooldown={cooldown}s")
                    self._announce(name, "opened")
                return
            except Exception:
                self._redis.mark_failed()

        with self._lock:
            state = self._states.setdefault(name, CircuitState())
            state.failures += 1
            if threshold > 0 and state.failures >= threshold:
                state.opened_until = time.time() + cooldown
                logger.warning(f"外部服务熔断打开: service={name}, failures={state.failures}, cooldown={cooldown}s")
                self._announce(name, "opened")

    def stats(self) -> Dict[str, Dict[str, int]]:
        """返回当前熔断状态，用于健康检查和排障。"""

        client = self._redis.get_client()
        if client:
            try:
                names = client.smembers(_KNOWN_SERVICES_KEY)
                now = time.time()
                result = {}
                for name in names:
                    failures = client.get(f"circuit:{name}:failures")
                    opened_until_raw = client.get(f"circuit:{name}:opened_until")
                    opened_until = float(opened_until_raw) if opened_until_raw else 0.0
                    result[name] = {
                        "failures": int(failures) if failures else 0,
                        "open": opened_until > now,
                        "retry_after": max(0, int(opened_until - now)),
                    }
                return result
            except Exception:
                self._redis.mark_failed()

        now = time.time()
        with self._lock:
            return {
                name: {
                    "failures": state.failures,
                    "open": state.opened_until > now,
                    "retry_after": max(0, int(state.opened_until - now)),
                }
                for name, state in self._states.items()
            }


circuit_breaker = CircuitBreaker()


def should_retry_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429, 500, 502, 503, 504}


def request_with_retry(
        service_name: str,
        sender: Callable[[float], requests.Response],
        timeout_env: str,
        default_timeout: float,
        retry_env: str = "HTTP_CLIENT_MAX_RETRIES",
        default_retries: int = 2,
) -> requests.Response:
    """同步 HTTP 请求韧性封装：超时、重试、退避、熔断。"""

    timeout = _env_float(timeout_env, default_timeout)
    retries = max(0, _env_int(retry_env, default_retries))
    base_sleep = _env_float("HTTP_CLIENT_RETRY_BASE_SECONDS", 0.3)
    last_error: Optional[Exception] = None

    for attempt in range(retries + 1):
        circuit_breaker.before_call(service_name)
        try:
            response = sender(timeout)
            if should_retry_status(response.status_code):
                last_error = requests.HTTPError(f"HTTP {response.status_code}", response=response)
                raise last_error
            circuit_breaker.record_success(service_name)
            return response
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_error = exc
            circuit_breaker.record_failure(service_name)
            if attempt >= retries:
                break
            sleep_seconds = base_sleep * (2 ** attempt) + random.uniform(0, base_sleep)
            time.sleep(sleep_seconds)

    if last_error:
        raise last_error
    raise RuntimeError(f"{service_name} 请求失败")


async def async_request_with_retry(
        service_name: str,
        sender: Callable[[httpx.AsyncClient], object],
        timeout_env: str,
        default_timeout: float,
        retry_env: str = "HTTP_CLIENT_MAX_RETRIES",
        default_retries: int = 2,
) -> httpx.Response:
    """异步 HTTP 请求韧性封装。"""

    timeout = _env_float(timeout_env, default_timeout)
    retries = max(0, _env_int(retry_env, default_retries))
    base_sleep = _env_float("HTTP_CLIENT_RETRY_BASE_SECONDS", 0.3)
    last_error: Optional[Exception] = None

    for attempt in range(retries + 1):
        circuit_breaker.before_call(service_name)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await sender(client)
            if should_retry_status(response.status_code):
                last_error = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
                raise last_error
            circuit_breaker.record_success(service_name)
            return response
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError, httpx.RemoteProtocolError) as exc:
            last_error = exc
            circuit_breaker.record_failure(service_name)
            if attempt >= retries:
                break
            sleep_seconds = base_sleep * (2 ** attempt) + random.uniform(0, base_sleep)
            await asyncio.sleep(sleep_seconds)

    if last_error:
        raise last_error
    raise RuntimeError(f"{service_name} 请求失败")


def stream_request_with_circuit(
        service_name: str,
        sender: Callable[[float], requests.Response],
        timeout_env: str,
        default_timeout: float,
) -> requests.Response:
    """流式请求熔断封装。

    流式响应不做整段重试，避免用户已收到部分内容后重复输出。
    """

    timeout = _env_float(timeout_env, default_timeout)
    circuit_breaker.before_call(service_name)
    try:
        response = sender(timeout)
        response.raise_for_status()
        circuit_breaker.record_success(service_name)
        return response
    except requests.RequestException as exc:
        circuit_breaker.record_failure(service_name)
        raise exc
