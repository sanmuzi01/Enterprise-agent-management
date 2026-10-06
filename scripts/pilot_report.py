"""生成试点效果报告（Markdown，只有汇总和匿名评价，没有按人展开的数据）。

用法：.venv\\Scripts\\python.exe scripts\\pilot_report.py [--days 30] [--team 部门编号] [--out 路径]
默认写到 docs/pilot-report-YYYYMMDD.md。报告开头会列出“这份数据能说明什么、不能说明什么”（样本量、基准来源等），请连同它一起给别人看。
"""
import argparse
import asyncio
import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


async def main(days: int, team_id, out: pathlib.Path) -> None:
    from models.async_db import AsyncSessionLocal
    from service import pilot_service
    async with AsyncSessionLocal() as db:
        data = await pilot_service.report(db, days, team_id)
    out.write_text(pilot_service.to_markdown(data), encoding="utf-8")
    print(f"已生成 {out}")
    for caveat in data["caveats"][:-1]:
        print(f"  注意：{caveat}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--team", type=int, default=None)
    parser.add_argument("--out", type=pathlib.Path, default=ROOT / "docs" / f"pilot-report-{datetime.now():%Y%m%d}.md")
    args = parser.parse_args()
    asyncio.run(main(args.days, args.team, args.out))
