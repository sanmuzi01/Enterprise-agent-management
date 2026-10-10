"""把“对应助手已经不存在”的提示词文件和专业技能配置文件移到 backups/ 下归档（service/data_health.py::orphan_agent_files）。

以前删助手、跑测试都会留下按助手编号命名的文件（prompt/prompts/<id>.yaml、skills/enterprise/agent_<id>.yml），
开发库里积了几千个，没有任何页面会列出它们，备份也跟着变大。

做法是“移走”不是“删除”：移到 backups/orphan_agent_files_<时间>/ 下，附一份清单 manifest.json，误判了可以原样搬回来。
保护：
  - git 跟踪的文件不动（比如内置演示助手的提示词）；
  - 数据库里一个助手都没有时拒绝执行——多半是连错了库（例如空的测试库），那样所有文件都会被当成孤儿。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/archive_orphan_agent_files.py           # 只看：各有多少个
    .venv/Scripts/python.exe scripts/archive_orphan_agent_files.py --apply   # 移到 backups/ 归档，写审计
"""
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from sqlalchemy import text  # noqa: E402

from models.init_db import SessionLocal  # noqa: E402
from service import data_health  # noqa: E402


def _tracked_files() -> set:
    try:
        out = subprocess.run(["git", "ls-files", "prompt/prompts", "skills/enterprise"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {str((ROOT / line).resolve()) for line in out.splitlines() if line.strip()}


def main(argv) -> int:
    with SessionLocal() as db:
        agents = db.execute(text("SELECT COUNT(*) FROM agent")).scalar() or 0
        orphans = data_health.orphan_agent_files(db)
    tracked = _tracked_files()
    plan = {key: [p for p in paths if str(pathlib.Path(p).resolve()) not in tracked] for key, paths in orphans.items()}
    kept = sum(len(v) for v in orphans.values()) - sum(len(v) for v in plan.values())
    print(f"提示词文件 {len(plan['prompt'])} 个、专业技能配置 {len(plan['skill'])} 个对应的助手已不存在"
          + (f"（另有 {kept} 个是 git 跟踪的文件，不动）" if kept else ""))
    if "--apply" not in argv:
        print("只是查看，没有移动。确认后加 --apply。")
        return 0
    if agents == 0:
        print("数据库里一个助手都没有，多半是连错了库（比如空的测试库），拒绝执行。")
        return 1
    if not any(plan.values()):
        print("没有需要归档的文件。")
        return 0

    target = ROOT / "backups" / f"orphan_agent_files_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    manifest = []
    for key, paths in plan.items():
        folder = target / key
        folder.mkdir(parents=True, exist_ok=True)
        for src in paths:
            dst = folder / os.path.basename(src)
            shutil.move(src, dst)
            manifest.append({"from": os.path.relpath(src, ROOT).replace("\\", "/"),
                             "to": os.path.relpath(dst, ROOT).replace("\\", "/")})
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    from service import audit_service
    audit_service.record(0, "data_health.orphan_files_archived", resource_type="file",
                         detail={"archive": os.path.relpath(target, ROOT).replace("\\", "/"),
                                 "prompt": len(plan["prompt"]), "skill": len(plan["skill"])})
    print(f"已移到 {os.path.relpath(target, ROOT)}（清单在 manifest.json，要恢复就按清单把文件搬回原位置）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
