"""统计测试数量，写进 docs/testing.md 里唯一的一处（README、项目状态文档只链接过去，不再各写一个数）。

以前几份文档各写各的：README 写 Python 1839、前端 32，测试文档写 1990、116，实际跑出来前端是 122——
面试或交付时被追问“到底多少条”就说不清。现在数字只有一个来源，而且是统计出来的，不是手填的。

统计方法（都不依赖手工记录）：
  - Python：unittest 按 CI 同样的方式发现测试（discover -s tests -p "test_*.py"），数用例个数，不运行；
  - 前端：真跑一遍 Vitest（约 20 秒），取 JSON 报告里的总数（it.each 这类参数化用例按展开后的个数算）；
  - Java：enterprise-business-hub 测试源码里的 @Test 个数。
通过 / 跳过的情况每次运行环境不同（有没有 MySQL、Redis），以 CI 最近一次结果为准，这里只记“有多少条”。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/test_counts.py           # 打印
    .venv/Scripts/python.exe scripts/test_counts.py --write   # 更新 docs/testing.md 里的统计块
    .venv/Scripts/python.exe scripts/test_counts.py --check   # 文档里的数和实际不一致就失败（发布自检用）
"""
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "testing.md"
START, END = "<!-- test-counts:start -->", "<!-- test-counts:end -->"


def python_count() -> int:
    os.environ.setdefault("CONSOLE_LOG_LEVEL", "CRITICAL")
    sys.path.insert(0, str(ROOT))
    # 和 CI 一样：discover -s tests（tests 不是包，顶层目录就是 tests；项目根目录另外放进 sys.path 供 from tests import ...）
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    return suite.countTestCases()


def frontend_count() -> int:
    npx = "npx.cmd" if sys.platform.startswith("win") else "npx"
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "vitest.json"
        subprocess.run([npx, "vitest", "run", "--configLoader", "runner", "--no-cache", "--reporter=json", f"--outputFile={out}"],
                       cwd=ROOT / "frontend", capture_output=True)
        if not out.exists():
            raise SystemExit("Vitest 没有生成报告：先在 frontend 目录里 npm install，并确认 npm --prefix frontend test 能跑")
        return int(json.loads(out.read_text(encoding="utf-8"))["numTotalTests"])


def java_count() -> int:
    total = 0
    for path in (ROOT / "enterprise-business-hub" / "src" / "test").rglob("*.java"):
        total += len(re.findall(r"^\s*@Test\b", path.read_text(encoding="utf-8"), flags=re.M))
    return total


def counts() -> dict:
    return {"python": python_count(), "frontend": frontend_count(), "java": java_count()}


def block(c: dict) -> str:
    today = datetime.date.today().isoformat()
    return (f"{START}\n"
            f"**测试数量**（{today} 由 `scripts/test_counts.py --write` 统计，不要手改）：Python `unittest` **{c['python']}** 条、"
            f"前端 Vitest **{c['frontend']}** 条、Java **{c['java']}** 条。"
            f"通过 / 跳过情况随运行环境变化（有没有 MySQL、Redis），以 CI 最近一次结果为准。\n"
            f"{END}")


def doc_counts(text: str) -> dict:
    m = re.search(re.escape(START) + r"(.*?)" + re.escape(END), text, flags=re.S)
    if not m:
        return {}
    nums = re.findall(r"\*\*(\d+)\*\*", m.group(1))
    return dict(zip(("python", "frontend", "java"), map(int, nums))) if len(nums) == 3 else {}


def main(argv) -> int:
    c = counts()
    print(f"Python {c['python']} 条，前端 {c['frontend']} 条，Java {c['java']} 条")
    text = DOC.read_text(encoding="utf-8")
    if "--check" in argv:
        recorded = doc_counts(text)
        if recorded != c:
            print(f"docs/testing.md 里记的是 {recorded or '（没有统计块）'}，和实际不一致：跑 scripts/test_counts.py --write 更新")
            return 1
        print("docs/testing.md 的测试数量是最新的")
        return 0
    if "--write" in argv:
        if START not in text:
            raise SystemExit(f"docs/testing.md 里没有 {START} … {END} 统计块")
        new = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block(c), text, flags=re.S)
        DOC.write_text(new, encoding="utf-8")
        print("已更新 docs/testing.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
