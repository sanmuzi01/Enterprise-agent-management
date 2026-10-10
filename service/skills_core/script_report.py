"""导入 Skill 时对 Python 脚本做静态兼容性检查，结果用来在商店里提前告诉用户"能不能用"。

只读源码（ast 解析），不执行任何脚本。判断三件事：
1. 缺依赖：脚本 import 了沙箱镜像里没有的第三方库（视频、深度学习、抓取类 Skill 最常见）
2. 要联网：沙箱没有外网，用到 requests / socket / urllib.request 等的脚本一定跑不通
3. 调系统命令：subprocess / os.system，沙箱里只有基础命令（没有 ffmpeg、libreoffice 等），只提醒不拦截

这是启发式判断，不等于"实测能跑"：动态 import、运行时拼出来的命令查不出来；
try/except ImportError 里的可选依赖不算缺失。所以最终验收仍要在真实沙箱里跑一遍。
"""
import ast
import os
import sys
from typing import Any, Dict, List, Set

# 沙箱镜像（sandbox/requirements.txt）里能 import 到的第三方模块。
# 装了什么改了什么，这里要同步——tests/test_skill_sandbox.py 会校验两边一致。
PACKAGE_MODULES: Dict[str, Set[str]] = {
    "pypdf": {"pypdf"},
    "pdfplumber": {"pdfplumber", "pdfminer", "pypdfium2"},
    "reportlab": {"reportlab"},
    "openpyxl": {"openpyxl", "et_xmlfile"},
    "python-docx": {"docx"},
    "python-pptx": {"pptx", "xlsxwriter"},
    "pandas": {"pandas", "dateutil", "pytz", "six"},
    "numpy": {"numpy"},
    "pillow": {"PIL"},
    "pyyaml": {"yaml"},
    "lxml": {"lxml"},
    "beautifulsoup4": {"bs4", "soupsieve"},
    "markdown": {"markdown"},
    "defusedxml": {"defusedxml"},
    "opencv-python-headless": {"cv2"},
    "imageio-ffmpeg": {"imageio_ffmpeg"},
    "scipy": {"scipy"},
    "soundfile": {"soundfile"},
    "pyloudnorm": {"pyloudnorm"},
    "matplotlib": {"matplotlib", "cycler", "kiwisolver", "pyparsing", "contourpy", "fontTools"},
}
SANDBOX_MODULES: Set[str] = set().union(*PACKAGE_MODULES.values())

# 用到这些就说明脚本要联网（沙箱在 internal 网络里，没有外网出口）
NETWORK_PREFIXES = (
    "requests", "httpx", "urllib3", "aiohttp", "websockets", "websocket", "socket", "http.client",
    "urllib.request", "ftplib", "smtplib", "imaplib", "poplib", "telnetlib", "paramiko", "boto3", "openai",
    "anthropic", "dashscope", "zhipuai", "playwright", "selenium", "pyppeteer", "tweepy", "yt_dlp", "youtube_dl",
    "google", "azure", "oss2",
)
SYSTEM_MODULES = {"subprocess", "pty"}
SYSTEM_CALLS = {("os", "system"), ("os", "popen"), ("os", "execv"), ("os", "execvp"), ("os", "spawnl")}


def _stdlib() -> Set[str]:
    return set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


def _local_modules(root: str) -> Set[str]:
    """脚本包里自带的模块名（同目录的 .py 和包目录），这些 import 不算缺依赖。"""
    found: Set[str] = set()
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        found.update(d for d in dirs)
        found.update(os.path.splitext(f)[0] for f in files if f.endswith(".py"))
    return found


def _optional_import_ids(tree: ast.AST) -> Set[int]:
    """try: import x / except ImportError 里的 import 是可选依赖，缺了脚本自己会降级。"""
    optional: Set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches = False
        for h in node.handlers:
            names = []
            if h.type is None:
                names = ["BaseException"]
            elif isinstance(h.type, ast.Tuple):
                names = [getattr(e, "id", "") for e in h.type.elts]
            else:
                names = [getattr(h.type, "id", "")]
            if any(n in ("ImportError", "ModuleNotFoundError", "Exception", "BaseException") for n in names):
                catches = True
        if catches:
            for stmt in node.body:
                for sub in ast.walk(stmt):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        optional.add(id(sub))
    return optional


def analyze_script(path: str, local_modules: Set[str], stdlib: Set[str]) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
    except (SyntaxError, ValueError):
        return {"missing": [], "network": False, "system": False, "syntax_error": True}

    optional = _optional_import_ids(tree)
    missing: Set[str] = set()
    network = system = False

    for node in ast.walk(tree):
        mods: List[str] = []   # 要检查"是否装了"的模块（顶层）
        dotted_names: List[str] = []  # 用来判断"是否联网/调系统"的完整名字
        if isinstance(node, ast.Import):
            mods = dotted_names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods = [node.module]
            dotted_names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if (node.func.value.id, node.func.attr) in SYSTEM_CALLS:
                system = True
        for dotted in dotted_names:
            if any(dotted == p or dotted.startswith(p + ".") for p in NETWORK_PREFIXES):
                network = True
            if dotted.split(".")[0] in SYSTEM_MODULES:
                system = True
        if id(node) in optional:
            continue
        for dotted in mods:
            top = dotted.split(".")[0]
            if top not in stdlib and top not in local_modules and top not in SANDBOX_MODULES:
                missing.add(top)
    return {"missing": sorted(missing), "network": network, "system": system, "syntax_error": False}


def analyze_bundle(bundle_dir: str, scripts: List[str]) -> Dict[str, Any]:
    """返回 {status, total, runnable, missing_packages, network, system, problems}。

    status：none 没有脚本 / ready 全部可运行 / partial 部分可运行 / unsupported 一个都不行
    runnable：没有缺依赖、不联网、语法正常的脚本（沙箱开启后助手只会被告知这些）
    problems：有问题的脚本 → 具体原因，只存有问题的，避免配置文件膨胀
    """
    if not scripts:
        return {"status": "none", "total": 0, "runnable": [], "missing_packages": [], "network": 0, "system": 0, "problems": {}}
    local, stdlib = _local_modules(bundle_dir), _stdlib()
    runnable: List[str] = []
    problems: Dict[str, Dict[str, Any]] = {}
    missing_all: Set[str] = set()
    network = system = 0
    for rel in scripts:
        res = analyze_script(os.path.join(bundle_dir, *rel.split("/")), local, stdlib)
        network += res["network"]
        system += res["system"]
        missing_all.update(res["missing"])
        if res["missing"] or res["network"] or res["syntax_error"]:
            problems[rel] = {k: v for k, v in res.items() if v}
        else:
            runnable.append(rel)
            if res["system"]:
                problems[rel] = {"system": True}  # 能用，但会调系统命令，只做提醒
    status = "ready" if len(runnable) == len(scripts) else ("partial" if runnable else "unsupported")
    return {
        "status": status, "total": len(scripts), "runnable": runnable,
        "missing_packages": sorted(missing_all), "network": network, "system": system, "problems": problems,
    }


def reanalyze_config(config_file: str) -> Dict[str, Any]:
    """重新检查一个已导入 Skill 的脚本，改写配置里的 runnable_scripts / script_report。

    沙箱依赖变了（往 sandbox/requirements.txt 和 PACKAGE_MODULES 里加了库）之后用，
    不用把 Skill 删掉重新导入。返回 {"changed": 是否有变化, "before": 旧状态, "after": 新状态}。
    没有脚本包的配置返回 {"skipped": True}。
    """
    import yaml

    from service.skills.loader import _get_yml_path, invalidate_skill_config, localize_root

    path = _get_yml_path(config_file)
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict) or not cfg.get("scripts_root") or not cfg.get("scripts"):
        return {"skipped": True}

    # 配置可能是在别的机器上导入的（Windows 路径带到 Linux 容器），先换算成本机路径
    report = analyze_bundle(localize_root(cfg["scripts_root"]), list(cfg["scripts"]))
    before = (cfg.get("script_report") or {}).get("status", "unknown")
    new_report = {k: report[k] for k in ("status", "total", "missing_packages", "network", "system", "problems")}
    changed = cfg.get("runnable_scripts") != report["runnable"] or cfg.get("script_report") != new_report
    if changed:
        cfg["runnable_scripts"] = report["runnable"]
        cfg["script_report"] = new_report
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        invalidate_skill_config(config_file)
    return {"skipped": False, "changed": changed, "before": before, "after": report["status"]}
