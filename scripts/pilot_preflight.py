"""试点开始前的预检（只读）：真实模型、备份、死信、成员模型连接、部门与负责人、考勤日历……

用法（项目根目录）：
    .venv\\Scripts\\python.exe scripts\\pilot_preflight.py --org 12
    .venv\\Scripts\\python.exe scripts\\pilot_preflight.py --org 12 --md      # 输出 Markdown 表格，可直接贴进试点记录
有 error 时退出码为 1；warn 是提醒，不阻止开始。
"""
import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org", type=int, help="试点企业编号（不填则只做环境和运维检查）")
    parser.add_argument("--md", action="store_true", help="输出 Markdown")
    args = parser.parse_args()
    from models.init_db import SessionLocal
    from service import pilot_preflight
    db = SessionLocal()
    try:
        result = pilot_preflight.run(db, args.org)
    finally:
        db.close()
    if args.md:
        print(pilot_preflight.to_markdown(result))
    else:
        icon = {"ok": "PASS", "warn": "WARN", "error": "FAIL"}
        for c in result["checks"]:
            print(f"[{icon[c['level']]}] {c['label']}：{c['message']}")
            if c["fix"] and c["level"] != "ok":
                print(f"       → {c['fix']}")
        print(f"\n{'可以开始试点' if result['ok'] else '不能开始：%d 项必须先处理' % result['errors']}（{result['warnings']} 项提醒）")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
