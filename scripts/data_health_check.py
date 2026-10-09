"""数据体检（service/data_health.py）：找出库里已经存在的脏数据和前后不一致的数据。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/data_health_check.py            # 只读，列出问题
    .venv/Scripts/python.exe scripts/data_health_check.py --json     # 机器可读，给监控 / 定时任务用
    .venv/Scripts/python.exe scripts/data_health_check.py --fix      # 自动修“只补数据、不需要人判断”的几类，写审计

退出码：有 error 级别的问题时为 1（定时任务据此告警），否则为 0。
建议：交付 / 升级后跑一次；生产上每天跑一次（见 deploy/systemd/agent-data-health.*）。
--fix 之前先备份（scripts/backup.py）。
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")

from models.init_db import SessionLocal  # noqa: E402
from service import data_health  # noqa: E402

MARK = {"error": "[严重]", "warn": "[注意]", "info": "[提示]"}


def main(argv) -> int:
    with SessionLocal() as db:
        findings = data_health.run_checks(db)
        fixed = data_health.apply_fixes(db, findings) if "--fix" in argv else {}
        if fixed:
            findings = data_health.run_checks(db)
    if "--json" in argv:
        print(json.dumps({"findings": [f.to_dict() for f in findings], "fixed": fixed}, ensure_ascii=False, indent=2))
    else:
        if not findings:
            print("数据体检通过：没有发现问题")
        for f in findings:
            print(f"{MARK.get(f.severity, '')} {f.title}：{f.count} 条" + ("（可自动修复，加 --fix）" if f.fixable else ""))
            for s in f.samples:
                print(f"    - {s}")
            if f.count > len(f.samples):
                print(f"    - ……还有 {f.count - len(f.samples)} 条")
            print(f"    处理：{f.advice}")
        for code, n in fixed.items():
            print(f"[已修复] {code}：{n} 条（已写审计）")
    return 1 if any(f.severity == "error" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
