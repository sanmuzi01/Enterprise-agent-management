"""一键备份：MySQL + 应用运行数据，写到 backups/ 下。

跟 docs/deployment.md 里手动敲的命令是一回事，只是包装成一个脚本方便配 cron/
计划任务定时跑，并顺手清理过期备份。默认备份直接部署时的宿主机目录；传
``--docker`` 时通过 Compose 容器流式导出 Python/Java 两个业务库、API 挂载的运行时
目录和 Chroma 具名卷，不需要知道 Docker 自动生成的卷名。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/backup.py
    .venv/Scripts/python.exe scripts/backup.py --keep-days 14   # 清理 14 天前的备份
    .venv/Scripts/python.exe scripts/backup.py --docker
    .venv/Scripts/python.exe scripts/backup.py --docker --compose-file docker-compose.prod.yml
"""

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKUP_DIR = ROOT / "backups"

# 相对项目根目录；和 .env(.production).example 里的默认值、docs/deployment.md 第 5 节保持一致。
DATA_DIRS = ["knowledge_files", "vector_db", "chroma_db", "logs", "skills", "skills_packages",
             os.path.join("prompt", "prompts")]

# 都是镜像内固定路径，不接收用户输入。不存在的目录由容器内脚本跳过，因此本地 compose
# 和生产 compose（挂载目录不完全相同）可以共用同一条备份路径。
DOCKER_APP_PATHS = (
    "data", "knowledge_files", "prompt/prompts", "skills/enterprise", "skills/imported",
    "skills/user_created", "skills/user_templates", "skills_packages/imported", "static/charts",
    "agent_templates", "logs",
)
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def dump_mysql(dest: pathlib.Path) -> bool:
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")
    name = os.getenv("DB_NAME", "agent_sql")

    if shutil.which("mysqldump") is None:
        print("[跳过] 没找到 mysqldump 命令，请自行导出数据库或把 mysqldump 加进 PATH")
        return False

    cmd = [
        "mysqldump", f"-h{host}", f"-P{port}", f"-u{user}",
        "--single-transaction", "--routines", "--triggers", name,
    ]
    env = os.environ.copy()
    if password:
        env["MYSQL_PWD"] = password  # 避免密码出现在进程列表里（-p 参数会被 ps 看到）

    print(f"[dump] mysqldump {name} -> {dest.name}")
    with open(dest, "wb") as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, env=env)
    if result.returncode != 0:
        dest.unlink(missing_ok=True)
        print(f"[失败] mysqldump 退出码 {result.returncode}: {result.stderr.decode('utf-8', 'ignore')[:500]}")
        return False
    return True


def archive_data_dirs(dest: pathlib.Path) -> None:
    existing = [d for d in DATA_DIRS if (ROOT / d).exists()]
    if not existing:
        print("[跳过] 没有找到任何数据目录（knowledge_files/vector_db/...），可能都是空的")
        return
    print(f"[archive] {', '.join(existing)} -> {dest.name}")
    with tarfile.open(dest, "w:gz") as tar:
        for d in existing:
            tar.add(ROOT / d, arcname=d)


def _compose_command(compose_file: pathlib.Path, *args: str) -> list[str]:
    return ["docker", "compose", "-f", str(compose_file), *args]


def _stream_command(dest: pathlib.Path, cmd: list[str], label: str) -> bool:
    """把命令的二进制 stdout 直接写入文件；失败时删除半截备份。"""
    print(f"[{label}] -> {dest.name}")
    try:
        with open(dest, "wb") as output:
            result = subprocess.run(cmd, stdout=output, stderr=subprocess.PIPE)
    except OSError as exc:
        dest.unlink(missing_ok=True)
        print(f"[失败] 无法执行 {cmd[0]}: {exc}")
        return False
    if result.returncode != 0:
        dest.unlink(missing_ok=True)
        error = result.stderr.decode("utf-8", "ignore")[:500]
        print(f"[失败] {label}退出码 {result.returncode}: {error}")
        return False
    return True


def backup_docker(compose_file: pathlib.Path, timestamp: str) -> bool:
    """备份 Compose 管理的持久化数据，不依赖宿主机卷名或 tar 临时容器。"""
    if shutil.which("docker") is None:
        print("[失败] 没找到 docker 命令")
        return False
    if not compose_file.is_file():
        print(f"[失败] Compose 文件不存在: {compose_file}")
        return False

    enterprise_db = os.getenv("ENTERPRISE_DB_NAME", "enterprise_business")
    if not _DB_NAME_RE.fullmatch(enterprise_db):
        print("[失败] ENTERPRISE_DB_NAME 只能包含字母、数字和下划线")
        return False
    db_script = (
        'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction '
        f'--routines --triggers --databases "$MYSQL_DATABASE" "{enterprise_db}"'
    )
    db_ok = _stream_command(
        BACKUP_DIR / f"docker_db_{timestamp}.sql",
        _compose_command(compose_file, "exec", "-T", "db", "sh", "-c", db_script),
        "docker-db",
    )

    quoted_paths = " ".join(f"'{path}'" for path in DOCKER_APP_PATHS)
    app_script = (
        "set --; for p in " + quoted_paths + "; do "
        '[ -e "/app/$p" ] && set -- "$@" "$p"; '
        'done; [ "$#" -gt 0 ] || exit 3; exec tar czf - -C /app "$@"'
    )
    app_ok = _stream_command(
        BACKUP_DIR / f"docker_app_{timestamp}.tar.gz",
        _compose_command(compose_file, "exec", "-T", "api", "sh", "-c", app_script),
        "docker-app-data",
    )

    # Chroma 1.x 官方镜像把数据存在 /data（本地和生产 compose 都把 chroma_data 卷挂在这里）。
    # 目录为空直接失败：以前本地 compose 把卷挂错了位置，备份“成功”却只是一个空包。
    chroma_script = (
        '[ -d /data ] || exit 3; '
        '[ -n "$(ls -A /data)" ] || { echo "Chroma 数据目录 /data 是空的，没有可备份的向量数据" >&2; exit 4; }; '
        'exec tar czf - -C /data .'
    )
    chroma_ok = _stream_command(
        BACKUP_DIR / f"docker_chroma_{timestamp}.tar.gz",
        _compose_command(compose_file, "exec", "-T", "chroma", "sh", "-c", chroma_script),
        "docker-chroma",
    )
    return db_ok and app_ok and chroma_ok


def cleanup_old_backups(keep_days: int) -> None:
    if keep_days <= 0:
        return
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for item in BACKUP_DIR.glob("*"):
        if item.is_file() and item.stat().st_mtime < cutoff:
            item.unlink()
            removed += 1
    if removed:
        print(f"[清理] 删除了 {removed} 个 {keep_days} 天前的备份文件")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-days", type=int, default=0, help="清理超过多少天的旧备份，0 表示不清理")
    parser.add_argument("--docker", action="store_true", help="备份 Docker Compose 的数据库和具名卷")
    parser.add_argument("--compose-file", default="docker-compose.yml", help="--docker 使用的 Compose 文件")
    args = parser.parse_args()

    BACKUP_DIR.mkdir(exist_ok=True)
    ts = _timestamp()

    if args.docker:
        compose_file = pathlib.Path(args.compose_file)
        if not compose_file.is_absolute():
            compose_file = ROOT / compose_file
        ok = backup_docker(compose_file.resolve(), ts)
    else:
        ok = dump_mysql(BACKUP_DIR / f"db_{ts}.sql")
        archive_data_dirs(BACKUP_DIR / f"data_{ts}.tar.gz")
    cleanup_old_backups(args.keep_days)

    print("完成" if ok else "备份失败（未完成的半截文件已删除，请检查上面的提示）")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
