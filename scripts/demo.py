"""一键启动 / 停止 / 检查 / 重置演示环境（面试现场用）。

    .venv\\Scripts\\python.exe scripts\\demo.py start      # 迁移 → 起 Java 业务服务、后端、前端 → 首次自动灌演示数据 → 打印地址与账号
    .venv\\Scripts\\python.exe scripts\\demo.py status     # 看各服务是否在跑、就绪检查结果
    .venv\\Scripts\\python.exe scripts\\demo.py reset      # 清掉并重建演示数据（不重启服务）
    .venv\\Scripts\\python.exe scripts\\demo.py stop       # 停掉本脚本启动的服务

前置：本机 MySQL 已启动，.env 配好数据库账号；已装 Java 17+、Node。日志和进程号放在 .demo/ 目录。
演示环境默认用“离线演示模型”（不联网、不需要 Key，规则抽取），所以断网、没额度也能完整演示 AI 整理链路；
要演示真实对话，登录后在设置里连接真实模型即可。绝不用于生产环境。
"""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / ".demo"
PID_FILE = STATE_DIR / "pids.json"
PY = sys.executable
SERVICES = {   # 名称 → (端口, 健康检查地址)
    "hub": (8090, "http://127.0.0.1:8090/actuator/health"),
    "backend": (8011, "http://127.0.0.1:8011/health"),
    "frontend": (5173, "http://127.0.0.1:5173/"),
}
LABELS = {"hub": "企业业务服务（Java）", "backend": "后端（FastAPI）", "frontend": "前端（Vite）"}

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def dotenv_values() -> dict:
    values = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip("\"'")
    return values


def child_env() -> dict:
    """子进程环境：离线演示模型 + 业务库口令沿用 .env 里的 DB_PASSWORD；强制非生产。"""
    env = dict(os.environ)
    values = dotenv_values()
    env.setdefault("ENTERPRISE_DB_PASSWORD", values.get("DB_PASSWORD", ""))
    env.setdefault("ENTERPRISE_DB_USER", values.get("DB_USER", "root"))
    env["OFFLINE_DEMO_MODEL"] = "1"
    env["APP_ENV"] = "development"
    # 演示时同一台机器会频繁切换多个账号，默认的“每 IP 5 分钟 20 次登录”会误伤；只放宽演示环境
    env.setdefault("LOGIN_IP_RATE_LIMIT", "300")
    env.setdefault("LOGIN_USER_RATE_LIMIT", "100")   # 同一个演示账号每 5 分钟默认最多 8 次，彩排和反复演示会触发
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return 200 <= response.status < 300
    except Exception:  # noqa: BLE001
        return False


def read_pids() -> dict:
    try:
        return json.loads(PID_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def spawn(name: str, command: list, cwd: pathlib.Path, env: dict) -> int:
    STATE_DIR.mkdir(exist_ok=True)
    log = open(STATE_DIR / f"{name}.log", "ab")
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    process = subprocess.Popen(command, cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kwargs)
    pids = read_pids()
    pids[name] = process.pid
    PID_FILE.write_text(json.dumps(pids), encoding="utf-8")
    return process.pid


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # 直接问 Windows API，不解析 tasklist 的文字输出：tasklist 慢（几百毫秒）、输出随系统语言变化、
        # 在受限环境里还可能被拒绝，之前正是它让这个判断时灵时不灵。
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, pid)         # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # 5 = 拒绝访问：进程存在但属于更高权限的账号；87 = 参数无效：没有这个 PID
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259                                # STILL_ACTIVE：已退出的进程句柄还可能存在，要看退出码
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True                                                 # 存在，只是不属于当前用户
    except OSError:
        return False


def kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(pid), 15)
        except Exception:  # noqa: BLE001
            pass


def print_log_tail(name: str, lines: int = 8) -> None:
    try:
        tail = (STATE_DIR / f"{name}.log").read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return
    for line in tail:
        print("        | " + line[:160])


def wait_for(name: str, timeout: int, pid: int = 0) -> bool:
    url = SERVICES[name][1]
    deadline = time.time() + timeout
    while time.time() < deadline:
        if healthy(url):
            return True
        if pid and not pid_alive(pid):
            return False   # 进程已经退出，不用傻等到超时
        time.sleep(1.5)
    return False


def require(command: str, hint: str):
    if shutil.which(command) is None:
        print(f"缺少 {command}：{hint}")
        sys.exit(2)


def check_database() -> None:
    values = dotenv_values()
    if not (ROOT / ".env").exists():
        print("缺少 .env：复制 .env.example 并填好数据库账号。")
        sys.exit(2)
    try:
        import pymysql
        pymysql.connect(host=values.get("DB_HOST", "127.0.0.1"), port=int(values.get("DB_PORT", "3306")), user=values.get("DB_USER", "root"),
                        password=values.get("DB_PASSWORD", ""), connect_timeout=3).close()
    except Exception as exc:  # noqa: BLE001
        print(f"连不上 MySQL（{type(exc).__name__}）：确认 MySQL 已启动，.env 里的 DB_HOST/DB_PORT/DB_USER/DB_PASSWORD 正确。")
        sys.exit(2)


def build_hub_jar() -> pathlib.Path:
    jars = sorted((ROOT / "enterprise-business-hub" / "target").glob("enterprise-business-hub-*.jar"))
    newest_source = max((p.stat().st_mtime for p in (ROOT / "enterprise-business-hub" / "src").rglob("*") if p.is_file()), default=0)
    if jars and jars[-1].stat().st_mtime >= newest_source:
        return jars[-1]
    require("mvn", "需要 Maven 来构建企业业务服务（或先手动 mvn -DskipTests package）")
    print("构建企业业务服务（源码比 jar 新，第一次会慢一些）…")
    result = subprocess.run(["mvn", "-B", "-q", "-DskipTests", "package"], cwd=str(ROOT / "enterprise-business-hub"), env=child_env(), shell=os.name == "nt")
    if result.returncode != 0:
        print("构建失败，详见上面的 Maven 输出。")
        sys.exit(2)
    return sorted((ROOT / "enterprise-business-hub" / "target").glob("enterprise-business-hub-*.jar"))[-1]


def cmd_start() -> int:
    check_database()
    require("java", "需要 Java 17+ 来运行企业业务服务")
    require("npm", "需要 Node.js 来运行前端")
    env = child_env()
    print("1/5 数据库迁移…")
    subprocess.run([PY, "-m", "alembic", "upgrade", "head"], cwd=str(ROOT), env=env, check=True, stdout=subprocess.DEVNULL)
    jar = build_hub_jar()
    commands = {
        "hub": (["java", "-jar", str(jar)], ROOT / "enterprise-business-hub"),
        "backend": ([PY, "-m", "uvicorn", "FasdtApi.main:app", "--host", "127.0.0.1", "--port", "8011"], ROOT),
        "frontend": (["npm.cmd" if os.name == "nt" else "npm", "--prefix", "frontend", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort"], ROOT),
    }
    print("2/5 依次启动并等待就绪（先业务服务，再后端，最后前端；一起启动在内存紧张的机器上会被系统杀掉进程）…")
    timeouts = {"hub": 90, "backend": 120, "frontend": 60}
    for name, (command, cwd) in commands.items():
        port = SERVICES[name][0]
        if port_open(port):
            print(f"    {LABELS[name]}：端口 {port} 已有服务在运行，直接使用（不重复启动）")
            continue
        for attempt in (1, 2):
            pid = spawn(name, command, cwd, env)
            print(f"    {LABELS[name]}：已启动（进程 {pid}，日志 .demo/{name}.log）…", end="", flush=True)
            if wait_for(name, timeouts[name], pid):
                print("就绪")
                break
            print("没起来")
            print_log_tail(name)
            kill_pid(pid)
            if attempt == 2:
                print(f"    {LABELS[name]}两次都没能启动，请按上面的日志处理后重试。")
                return 1
            print("    再试一次…")
    print("3/5 服务都已就绪")
    print("4/5 演示数据…")
    seed = subprocess.run([PY, "scripts/seed_enterprise_demo.py"], cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = [line for line in seed.stdout.splitlines() if line.strip()]
    print("    " + ("已存在，沿用" if any("已存在" in line for line in tail) else "已创建") if seed.returncode == 0 else "    灌数据失败：\n" + "\n".join(tail[-6:]))
    print("5/5 就绪检查…")
    print_readiness()
    print_accounts()
    return 0


def print_readiness() -> None:
    sys.path.insert(0, str(ROOT))
    os.environ.update({k: v for k, v in child_env().items() if k in ("OFFLINE_DEMO_MODEL", "ENTERPRISE_DB_PASSWORD", "ENTERPRISE_DB_USER", "APP_ENV")})
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import asyncio
    from service import readiness
    result = asyncio.run(readiness.collect())
    for item in result["checks"]:
        mark = {"ok": "✔", "warn": "!", "error": "✘"}[item["level"]]
        print(f"    [{mark}] {item['label']}：{item['message']}")
        if item["fix"] and not item["ok"]:
            print(f"        处理：{item['fix']}")


def print_accounts() -> None:
    print("\n" + "=" * 62)
    print("  打开 http://localhost:5173  （所有演示账号密码：Demo@12345）")
    print("    demo_emp     销售员工     —— 提交工单/报销/请假，协同办理")
    print("    demo_head    销售负责人   —— 审批请假、报销、入职、IT 申请")
    print("    demo_fin     财务专员     —— 核对记账凭证")
    print("    demo_it      IT 工程师    —— 工单队列、SLA、设备台账")
    print("    demo_hr      人事专员     —— 入转调离")
    print("    demo_owner   企业所有者   —— 落实调岗/离职变更")
    print("  演示脚本：docs/demo-script.md    状态：scripts\\demo.py status    停止：scripts\\demo.py stop")
    print("=" * 62)


def cmd_status() -> int:
    for name, (port, url) in SERVICES.items():
        state = "运行中" if healthy(url) else ("端口被占用但不健康" if port_open(port) else "未运行")
        print(f"  {LABELS[name]:<22} {state}（{port}）")
    if healthy(SERVICES["backend"][1]):
        print_readiness()
    return 0


def cmd_stop() -> int:
    pids = read_pids()
    if not pids:
        print("没有本脚本启动的服务记录。")
        return 0
    for name, pid in pids.items():
        kill_pid(pid)
        print(f"  已停止 {LABELS.get(name, name)}（进程 {pid}）")
    PID_FILE.unlink(missing_ok=True)
    return 0


def cmd_reset() -> int:
    if not healthy(SERVICES["hub"][1]):
        print("企业业务服务没有运行，业务单据会被跳过。先 start，或确认后再继续。")
    return subprocess.run([PY, "scripts/seed_enterprise_demo.py", "--reset"], cwd=str(ROOT), env=child_env()).returncode


def main(argv) -> int:
    commands = {"start": cmd_start, "status": cmd_status, "stop": cmd_stop, "reset": cmd_reset}
    if len(argv) < 2 or argv[1] not in commands:
        print(__doc__)
        return 1
    return commands[argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
