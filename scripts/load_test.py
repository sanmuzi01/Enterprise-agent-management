import argparse
import json
import statistics
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from queue import Queue
from typing import Dict, Iterable, List, Optional
from urllib import error, request


@dataclass
class Sample:
    method: str
    path: str
    status: int
    latency_ms: float
    ok: bool
    error: str = ""


class LoadTester:
    """轻量压测器：只依赖 Python 标准库，适合上线前做基础接口冒烟压测。"""

    def __init__(self, base_url: str, timeout: float, token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = token

    def request(self, method: str, path: str, payload: Optional[Dict] = None) -> Sample:
        url = f"{self.base_url}{path}"
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = request.Request(url, data=body, headers=headers, method=method)
        start = time.perf_counter()
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                resp.read()
                latency_ms = (time.perf_counter() - start) * 1000
                return Sample(method, path, resp.status, latency_ms, 200 <= resp.status < 400)
        except error.HTTPError as exc:
            exc.read()
            latency_ms = (time.perf_counter() - start) * 1000
            return Sample(method, path, exc.code, latency_ms, False, HTTPStatus(exc.code).phrase if exc.code in HTTPStatus._value2member_map_ else str(exc))
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            return Sample(method, path, 0, latency_ms, False, str(exc))

    def post_json(self, path: str, payload: Dict) -> Dict:
        """发送 JSON 请求并返回响应体，用于登录等准备动作。"""

        req = request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def login(self, username: str, password: str, api_prefix: str = "/api") -> str:
        # 压测脚本不是浏览器：走专用的令牌接口（生产默认关闭，压测前要设 AUTH_TOKEN_ENDPOINT_ENABLED=1）
        data = self.post_json(f"{api_prefix}/auth/token", {"name": username, "password": password})
        token = data.get("access_token")
        if not token:
            raise RuntimeError(data.get("message") or "登录响应中没有 access_token")
        return token


def scenario_paths(name: str, api_prefix: str = "/api") -> List[tuple]:
    """`api_prefix` 默认 `/api`，匹配走 Nginx 反代的部署形态（见 docs/deployment.md）；
    直接压测后端进程（没有 Nginx 在前面，比如本地基准测试）传 `--api-prefix ""`。
    `/health` 不受影响——它在 Nginx 层和后端本身都是同一个路径，不加前缀。
    """
    scenarios = {
        "health": [("GET", "/health", None)],
        "auth-read": [
            ("GET", f"{api_prefix}/user/me", None),
            ("GET", f"{api_prefix}/agent/list", None),
            ("GET", f"{api_prefix}/task/?limit=10", None),
        ],
        "mixed-read": [
            ("GET", "/health", None),
            ("GET", f"{api_prefix}/user/me", None),
            ("GET", f"{api_prefix}/agent/list", None),
            ("GET", f"{api_prefix}/task/?limit=10", None),
        ],
    }
    if name not in scenarios:
        raise ValueError(f"未知场景: {name}，可选: {', '.join(sorted(scenarios))}")
    return scenarios[name]


def percentile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


def summarize(samples: Iterable[Sample], elapsed: float) -> Dict:
    rows = list(samples)
    latencies = [item.latency_ms for item in rows]
    ok_count = sum(1 for item in rows if item.ok)
    status_counts: Dict[str, int] = {}
    for item in rows:
        key = str(item.status) if item.status else "network_error"
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "requests": len(rows),
        "ok": ok_count,
        "failed": len(rows) - ok_count,
        "success_rate": round(ok_count / len(rows) * 100, 2) if rows else 0,
        "elapsed_seconds": round(elapsed, 3),
        "rps": round(len(rows) / elapsed, 2) if elapsed > 0 else 0,
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else 0,
            "avg": round(statistics.mean(latencies), 2) if latencies else 0,
            "p50": round(percentile(latencies, 0.50), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "p99": round(percentile(latencies, 0.99), 2),
            "max": round(max(latencies), 2) if latencies else 0,
        },
        "status": status_counts,
        "errors": [
            {
                "method": item.method,
                "path": item.path,
                "status": item.status,
                "error": item.error[:200],
                "latency_ms": round(item.latency_ms, 2),
            }
            for item in rows
            if not item.ok
        ][:20],
    }


def run_load(tester: LoadTester, scenario: List[tuple], total: int, concurrency: int) -> Dict:
    jobs: Queue = Queue()
    samples: List[Sample] = []
    lock = threading.Lock()

    for index in range(total):
        jobs.put(scenario[index % len(scenario)])

    def worker():
        while True:
            try:
                method, path, payload = jobs.get_nowait()
            except Exception:
                return
            sample = tester.request(method, path, payload)
            with lock:
                samples.append(sample)
            jobs.task_done()

    start = time.perf_counter()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, concurrency))]
    for thread in threads:
        thread.start()
    jobs.join()
    elapsed = time.perf_counter() - start
    return summarize(samples, elapsed)


def main():
    parser = argparse.ArgumentParser(description="Agent 平台轻量 HTTP 压测脚本")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="后端或前端反代地址")
    parser.add_argument("--scenario", default="health", choices=["health", "auth-read", "mixed-read"], help="压测场景")
    parser.add_argument("--requests", type=int, default=100, help="总请求数")
    parser.add_argument("--concurrency", type=int, default=10, help="并发线程数")
    parser.add_argument("--timeout", type=float, default=10, help="单请求超时时间，秒")
    parser.add_argument("--username", default="", help="需要登录态场景时使用的用户名")
    parser.add_argument("--password", default="", help="需要登录态场景时使用的密码")
    parser.add_argument("--api-prefix", default="/api",
                         help="业务接口前缀，默认 /api（走 Nginx 反代）；直接压测后端进程传空串 \"\"")
    parser.add_argument("--json", action="store_true", help="只输出 JSON，便于保存报告")
    args = parser.parse_args()

    tester = LoadTester(args.base_url, args.timeout)
    if args.scenario in {"auth-read", "mixed-read"}:
        if not args.username or not args.password:
            raise SystemExit("auth-read/mixed-read 场景需要 --username 和 --password")
        tester.token = tester.login(args.username, args.password, api_prefix=args.api_prefix)

    result = run_load(
        tester=tester,
        scenario=scenario_paths(args.scenario, api_prefix=args.api_prefix),
        total=max(1, args.requests),
        concurrency=max(1, args.concurrency),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"场景: {args.scenario}")
    print(f"请求: {result['requests']}，成功: {result['ok']}，失败: {result['failed']}，成功率: {result['success_rate']}%")
    print(f"耗时: {result['elapsed_seconds']}s，吞吐: {result['rps']} req/s")
    print(
        "延迟(ms): "
        f"avg={result['latency_ms']['avg']} "
        f"p50={result['latency_ms']['p50']} "
        f"p95={result['latency_ms']['p95']} "
        f"p99={result['latency_ms']['p99']} "
        f"max={result['latency_ms']['max']}"
    )
    print(f"状态码: {result['status']}")
    if result["errors"]:
        print("前 20 条错误:")
        for item in result["errors"]:
            print(f"- {item['method']} {item['path']} status={item['status']} latency={item['latency_ms']}ms error={item['error']}")


if __name__ == "__main__":
    main()
