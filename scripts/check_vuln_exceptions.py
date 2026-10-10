"""核对漏洞豁免登记表（.github/vuln-exceptions.json）和 CI 里实际忽略的漏洞：

1. ci.yml 里每一个 `--ignore-vuln X` 都必须在登记表里，登记表里的每一条也必须还在 ci.yml 里使用（避免“悄悄多忽略一条”或“登记了但早就不用”）；
2. 每条都要有负责人、原因、复查办法和到期日；
3. 到期日已过就失败：逼着有人重新评估（上游可能已经发布了修复版本），而不是让豁免一直挂下去。
用法：python scripts/check_vuln_exceptions.py [--today 2027-01-06]（--today 只用于测试）
"""
import datetime
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTER = ROOT / ".github" / "vuln-exceptions.json"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
REQUIRED = ("id", "package", "owner", "added", "expires", "reason", "recheck")


def check(today: datetime.date) -> list:
    problems = []
    entries = json.loads(REGISTER.read_text(encoding="utf-8"))["exceptions"]
    ids = [e.get("id") for e in entries]
    if len(ids) != len(set(ids)):
        problems.append("登记表里有重复的漏洞编号")
    for entry in entries:
        for key in REQUIRED:
            if not str(entry.get(key, "")).strip():
                problems.append(f"{entry.get('id', '?')}：缺少 {key}")
        try:
            expires = datetime.date.fromisoformat(entry["expires"])
        except (KeyError, ValueError):
            problems.append(f"{entry.get('id', '?')}：到期日格式不对")
            continue
        if expires < today:
            problems.append(f"{entry['id']}（{entry.get('package')}）的豁免已在 {expires} 到期：请重新评估——上游可能已有修复版本；仍需豁免就更新原因和到期日")
        elif (expires - today).days > 180:
            problems.append(f"{entry['id']}：到期日离现在超过 180 天，豁免最长 180 天")
    ignored = set(re.findall(r"--ignore-vuln\s+([A-Za-z0-9_.-]+)", WORKFLOW.read_text(encoding="utf-8")))
    registered = set(ids)
    for missing in sorted(ignored - registered):
        problems.append(f"ci.yml 忽略了 {missing}，但登记表里没有（要写清楚原因、负责人和到期日）")
    for unused in sorted(registered - ignored):
        problems.append(f"登记表里有 {unused}，但 ci.yml 已经不忽略它了：删掉这一条")
    return problems


def main(argv) -> int:
    today = datetime.date.fromisoformat(argv[argv.index("--today") + 1]) if "--today" in argv else datetime.date.today()
    problems = check(today)
    for problem in problems:
        print("FAIL", problem)
    if not problems:
        print(f"漏洞豁免登记表通过检查（{today}）")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
