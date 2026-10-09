"""数据保留策略（service/data_retention.py）：看哪些过程记录到期了，确认后清理。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/data_retention.py              # 默认只看：每一类保留多少天、到期多少条（不删）
    .venv/Scripts/python.exe scripts/data_retention.py --apply      # 按策略分批删除，写审计
    .venv/Scripts/python.exe scripts/data_retention.py --apply --only agent_runs,operation_logs

保留天数用环境变量调（0 = 这一类不清理，最少 7 天）：
    AGENT_RUN_RETENTION_DAYS=180  OPERATION_LOG_RETENTION_DAYS=90  NOTIFICATION_RETENTION_DAYS=180
    BACKGROUND_TASK_RETENTION_DAYS=90  DEAD_LETTER_RETENTION_DAYS=180
第一次 --apply 之前先备份（scripts/backup.py），并和客户确认保留期限。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")

from models.init_db import SessionLocal  # noqa: E402
from service import data_retention  # noqa: E402


def main(argv) -> int:
    only = None
    if "--only" in argv:
        i = argv.index("--only")
        only = [s.strip() for s in (argv[i + 1] if i + 1 < len(argv) else "").split(",") if s.strip()]
    with SessionLocal() as db:
        rows = data_retention.plan(db)
        for r in rows:
            if only and r["name"] not in only:
                continue
            keep = "不清理" if r["days"] == 0 else f"保留 {r['days']} 天"
            print(f"{r['label']}（{r['name']}，{r['env']}）：{keep}，到期 {r['due']} 条")
        print("永远保留：" + "；".join(f"{k}（{v}）" for k, v in data_retention.NEVER_PURGED.items()))
        if "--apply" not in argv:
            print("\n只是查看，没有删除。确认后加 --apply 执行（先备份）。")
            return 0
        result = data_retention.apply(db, only=only)
    if not result:
        print("\n没有到期数据。")
    for name, n in result.items():
        print(f"[已清理] {name}：{n} 条" if n >= 0 else f"[失败] {name}：看日志")
    return 1 if any(n < 0 for n in result.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
