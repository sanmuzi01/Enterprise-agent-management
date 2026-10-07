"""Skill 包导入：兼容 Anthropic 官方 Skill、GitHub 上的 Skill 仓库和本平台能力包。

支持的输入：
1. 官方 Skill 格式：文件夹里有 SKILL.md（顶部带 name / description 的 YAML 头信息）；
   一个 zip 里可以有多个 Skill（例如整个 GitHub 仓库的 "Download ZIP"），会逐个导入。
2. 本平台能力包：SKILL.md + manifest.yaml，manifest 可以在 zip 根目录，也可以在子文件夹里。
3. 单个 .md（就是一个 SKILL.md）或 .yml/.yaml（本平台运行时配置）。

与官方 Skill 的差异（导入结果里会逐条告知用户）：
- Python 脚本会连同 Skill 的其他文件一起保存成"脚本包"，由独立的沙箱容器执行（沙箱没开时只保存不运行）；
  其他语言的脚本（.sh/.js 等）不能运行；
- 官方 allowed-tools 是 Claude Code 的工具，平台没有，忽略；
- 参考文档（.md/.txt/.json/.csv/.yml）会保存，并在字数预算内并入提示词，超出的只保存不启用。
"""
import os
import re
import shutil
import uuid
import zipfile

from service import archive_guard
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import yaml

from models.skill_dao import create_skill as dao_create
from service.skills.loader import SKILLS_ROOT, invalidate_skill_config
from utils.logger_handler import get_logger

from .common import _safe_skill_stem, _skill_to_dict
from .script_report import analyze_bundle
from .validation import validate_skill_config_file

logger = get_logger("skill_service")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ZIP_ENTRIES = 3000
MAX_SKILLS_PER_UPLOAD = 100
MAX_RESOURCES_PER_SKILL = 100
MAX_RESOURCE_BYTES = 200_000        # 与 loader 的单文件读取上限一致
RESOURCE_PROMPT_BUDGET = 16_000     # 所有参考文档并入提示词的字数预算，防止每轮对话都带上一大坨
PER_RESOURCE_PROMPT_CHARS = 12_000  # loader 里每个资源最多注入的字数

TEXT_EXTS = {".txt", ".md", ".markdown", ".json", ".csv", ".yml", ".yaml"}
SCRIPT_EXTS = {
    ".py", ".pyc", ".pyd", ".exe", ".dll", ".bat", ".cmd", ".ps1", ".sh", ".msi", ".scr", ".com", ".jar",
    ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php", ".go", ".rs", ".java", ".c", ".cpp",
}
# 脚本包里不保留的二进制/可执行文件（.py 是唯一可运行的入口）
BUNDLE_BLOCKED_EXTS = {".exe", ".dll", ".so", ".dylib", ".msi", ".scr", ".com", ".jar", ".bat", ".cmd", ".ps1", ".pyd", ".pyc"}
MAX_BUNDLE_FILE_BYTES = 5 * 1024 * 1024
MAX_BUNDLE_TOTAL_BYTES = 30 * 1024 * 1024
MAX_BUNDLE_FILES = 500


class SkillImportError(ValueError):
    """导入失败，message 可以直接展示给用户。"""


# ---------------------------------------------------------------- 解析

def parse_skill_md(text: str) -> Tuple[Dict[str, Any], str]:
    """拆出 SKILL.md 顶部的 YAML 头信息和正文。没有头信息时返回 ({}, 全文)。"""
    text = text.lstrip("﻿")
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not m:
        return {}, text
    head, body = m.group(1), text[m.end():]
    try:
        meta = yaml.safe_load(head)
    except yaml.YAMLError:
        # 官方 Skill 的 description 里常有没加引号的冒号，整段 YAML 解析会失败，退回逐行取值
        meta = {}
        for line in head.splitlines():
            kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
            if kv:
                meta[kv.group(1)] = kv.group(2).strip().strip("'\"")
    return (meta if isinstance(meta, dict) else {}), body


def _target_within(base: str, rel: str) -> str:
    """写文件前的兜底：拼出来的真实路径必须仍在 base 里（不信任任何字符串层面的路径判断）。"""
    if not archive_guard.safe_member_name(rel):
        raise SkillImportError("压缩包里含有不安全的文件路径，已拒绝导入")
    try:
        return archive_guard.ensure_within(base, os.path.join(base, *rel.split("/")))
    except archive_guard.ArchiveRejected as exc:
        raise SkillImportError(str(exc)) from None


def _norm(name: str) -> str:
    name = name.replace("\\", "/")
    return name[2:] if name.startswith("./") else name


def _find_roots(names: List[str]) -> List[str]:
    roots = set()
    for n in names:
        parts = n.split("/")
        if parts[-1].lower() == "skill.md":
            roots.add("/".join(parts[:-1]))
    return sorted(roots)


def _owner_root(name: str, roots: List[str]) -> Optional[str]:
    """文件归属于最深的那个 Skill 根目录。"""
    best = None
    for r in roots:
        if r == "" or name.startswith(r + "/"):
            if best is None or len(r) > len(best):
                best = r
    return best


def _strip_top(root: str) -> str:
    return root.split("/", 1)[1] if "/" in root else ""


# ---------------------------------------------------------------- 入口

def import_skill_bundle(
        db, user_id: int, filename: str, content: bytes,
        is_public: int = 0, commit: bool = True, subpath: str = "", allow_scripts: bool = True,
) -> Dict[str, Any]:
    """导入一个上传文件，返回 {"imported": [...], "failed": [...]}。全部失败时抛 SkillImportError。

    allow_scripts=False 时只导入文字说明，不保留任何脚本（普通用户导入陌生人的包时用，
    脚本只信任管理员挑选过的 Skill）。

    subpath：只导入 zip 里这个子目录下的 Skill（GitHub 文件夹链接用，路径已去掉仓库顶层目录）。
    """
    if not content:
        raise SkillImportError("上传的文件是空的")
    if len(content) > MAX_UPLOAD_BYTES:
        raise SkillImportError(f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB，请只打包需要的 Skill 文件夹")

    base = os.path.basename(filename or "skill.zip")
    ext = os.path.splitext(base.lower())[1]

    if ext in {".yml", ".yaml"}:
        return _import_yaml(db, user_id, base, content, is_public, commit)
    if ext == ".md":
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("SKILL.md", content)
        content, base = buf.getvalue(), os.path.splitext(base)[0] + ".zip"
    elif ext != ".zip":
        raise SkillImportError("不支持这种文件。请上传 .zip（官方 Skill 文件夹或 GitHub 仓库压缩包）、SKILL.md 或 .yml/.yaml")

    try:
        zf = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile:
        raise SkillImportError("这不是有效的 zip 压缩包，文件可能已损坏，请重新下载或压缩")

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_ENTRIES:
            raise SkillImportError(f"压缩包里有 {len(infos)} 个文件，超过上限 {MAX_ZIP_ENTRIES}，请只打包需要的 Skill 文件夹")
        if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
            raise SkillImportError("压缩包解压后体积过大，请只打包需要的 Skill 文件夹")
        for info in infos:
            if info.file_size > archive_guard.RATIO_FLOOR_BYTES and info.file_size > archive_guard.MAX_RATIO * max(info.compress_size, 1):
                raise SkillImportError("压缩包里有压缩比异常的文件（疑似压缩炸弹），已拒绝导入")

        files: Dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            name = _norm(info.filename)
            if not archive_guard.safe_member_name(name):       # 含盘符 C:/、UNC、备用数据流、保留设备名、NUL 等 Windows 路径花样
                raise SkillImportError("压缩包里含有不安全的文件路径，已拒绝导入")
            if name.startswith("__MACOSX/") or os.path.basename(name) == ".DS_Store":
                continue
            files[name] = info

        roots = _find_roots(list(files))
        if subpath:
            sub = subpath.strip("/")
            roots = [r for r in roots if _strip_top(r) == sub or _strip_top(r).startswith(sub + "/")]
        if not roots:
            raise SkillImportError(
                "没有找到 Skill：压缩包里需要有 SKILL.md 文件（官方 Skill 文件夹的标准结构）。"
                "如果你的文件是本平台旧版能力包，请确认里面有 SKILL.md 和 manifest.yaml"
            )
        if len(roots) > MAX_SKILLS_PER_UPLOAD:
            raise SkillImportError(f"一次最多导入 {MAX_SKILLS_PER_UPLOAD} 个 Skill，这个压缩包里有 {len(roots)} 个，请分开导入")

        imported: List[Dict[str, Any]] = []
        failed: List[Dict[str, str]] = []
        for root in roots:
            label = os.path.basename(root) or os.path.splitext(base)[0]
            if len(roots) > 1 and label == "template":
                continue  # 官方仓库里的空白模板，不是可用的 Skill
            try:
                own = {n: i for n, i in files.items() if _owner_root(n, roots) == root}
                imported.append(_install_one(db, user_id, zf, root, own, label, is_public, allow_scripts))
            except SkillImportError as e:
                failed.append({"name": label, "error": str(e)})
            except Exception as e:  # noqa: BLE001 - 单个 Skill 出错不应拖垮整批
                logger.error(f"导入Skill异常: root={root!r}, error={e}")
                failed.append({"name": label, "error": "内部错误，请稍后重试"})

    if not imported:
        raise SkillImportError("；".join(f"「{f['name']}」{f['error']}" for f in failed[:3]) or "没有可导入的 Skill")
    if commit:
        db.commit()
    return {"imported": imported, "failed": failed}


# ---------------------------------------------------------------- 单个 Skill

def _report_note(report: Dict[str, Any]) -> str:
    total, ok = report["total"], len(report["runnable"])
    extra = []
    if report["missing_packages"]:
        extra.append("缺少依赖：" + "、".join(report["missing_packages"][:8]) + ("等" if len(report["missing_packages"]) > 8 else ""))
    if report["network"]:
        extra.append(f"{report['network']} 个需要联网（沙箱没有外网）")
    if report["system"]:
        extra.append(f"{report['system']} 个会调用系统命令，沙箱里只有基础命令，可能失败")
    tail = "；".join(extra)
    if report["status"] == "ready":
        return f"脚本兼容性检查：{total} 个都可以在沙箱里运行" + (f"（{tail}）" if tail else "")
    if report["status"] == "partial":
        return f"脚本兼容性检查：{total} 个里 {ok} 个可以在沙箱里运行；{tail}。不能运行的脚本不会交给助手"
    return f"脚本兼容性检查：没有一个脚本能在沙箱里运行（{tail}），只会按文字说明工作"


MAX_TEXT_MEMBER_BYTES = 1_000_000   # SKILL.md / manifest 这类文本文件的上限


def _read_text_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, label: str) -> bytes:
    """读取压缩包里的文本文件，最多读 MAX_TEXT_MEMBER_BYTES（不信任压缩包声明的大小）。"""
    with zf.open(info) as f:
        data = f.read(MAX_TEXT_MEMBER_BYTES + 1)
    if len(data) > MAX_TEXT_MEMBER_BYTES:
        raise SkillImportError(f"{label} 太大（超过 {MAX_TEXT_MEMBER_BYTES // 1000} KB），不像是正常的说明文件")
    return data


def _install_one(db, user_id: int, zf: zipfile.ZipFile, root: str,
                 own: Dict[str, zipfile.ZipInfo], label: str, is_public: int,
                 allow_scripts: bool = True) -> Dict[str, Any]:
    prefix = root + "/" if root else ""
    md_name = next(n for n in own if n[len(prefix):].lower() == "skill.md")
    try:
        skill_text = _read_text_member(zf, own[md_name], "SKILL.md").decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SkillImportError("SKILL.md 不是 UTF-8 编码，请另存为 UTF-8 后重试")
    meta, body = parse_skill_md(skill_text)
    if not body.strip():
        raise SkillImportError("SKILL.md 正文是空的，没有可用的内容")

    notes: List[str] = []
    manifest: Dict[str, Any] = {}
    manifest_name = next((n for n in own if n[len(prefix):].lower() in {"manifest.yaml", "manifest.yml"}), None)
    if manifest_name:
        try:
            loaded = yaml.safe_load(_read_text_member(zf, own[manifest_name], "manifest.yaml").decode("utf-8-sig"))
        except (yaml.YAMLError, UnicodeDecodeError):
            raise SkillImportError("manifest.yaml 格式不对，无法解析")
        manifest = loaded if isinstance(loaded, dict) else {}

    name = str(manifest.get("display_name") or manifest.get("name") or meta.get("name") or label).strip() or label
    description = str(manifest.get("description") or meta.get("description") or "").strip()

    # 工具：只认平台已有的；官方 allowed-tools 是 Claude Code 的工具，不适用
    import service.tools  # noqa: F401
    from service.tools.base import ToolRegistry
    available = set(ToolRegistry.list_all())
    tools: List[Dict[str, Any]] = []
    for item in (manifest.get("tools") or meta.get("tools") or []):
        if isinstance(item, str):
            tool_name, defaults = item, {}
        elif isinstance(item, dict):
            tool_name, defaults = item.get("name"), item.get("defaults") or {}
        else:
            continue
        if tool_name in available:
            tools.append({"name": tool_name, "defaults": defaults})
        elif tool_name:
            notes.append(f"工具「{tool_name}」本平台没有，已忽略")
    if meta.get("allowed-tools") or meta.get("allowed_tools"):
        notes.append("已忽略 allowed-tools（那是 Claude Code 的工具，本平台没有）")

    # 附带文件分类
    from service import sandbox

    candidates: List[Tuple[str, zipfile.ZipInfo]] = []
    py_scripts: List[str] = []
    other_scripts = others = 0
    for n, info in own.items():
        rel = n[len(prefix):]
        if rel.lower() in {"skill.md", "manifest.yaml", "manifest.yml"}:
            continue
        fext = os.path.splitext(rel.lower())[1]
        if fext == ".py":
            py_scripts.append(rel)
        elif fext in SCRIPT_EXTS:
            other_scripts += 1
        elif fext in TEXT_EXTS and info.file_size <= MAX_RESOURCE_BYTES:
            candidates.append((rel, info))
        else:
            others += 1
    py_scripts.sort()
    if py_scripts and not allow_scripts:
        notes.append(
            f"包里有 {len(py_scripts)} 个 Python 脚本，没有导入：出于安全，脚本只保留管理员导入的 Skill。"
            "文字说明已导入；需要脚本请联系管理员，由管理员导入并公开到能力商店"
        )
        py_scripts = []
    if py_scripts:
        if sandbox.is_enabled():
            notes.append(f"{len(py_scripts)} 个 Python 脚本已保留，助手可以在隔离沙箱里运行它们")
        else:
            notes.append(
                f"{len(py_scripts)} 个 Python 脚本已保存，但脚本沙箱还没开启，暂时只会按文字说明工作；"
                "管理员开启沙箱后自动生效"
            )
    if other_scripts:
        notes.append(f"{other_scripts} 个非 Python 脚本（.sh / .js 等）不能运行，已跳过")
    if others and not py_scripts:
        notes.append(f"{others} 个非文本文件（图片、PDF、模板、字体等）未导入")
    if len(candidates) > MAX_RESOURCES_PER_SKILL:
        notes.append(f"参考文件超过 {MAX_RESOURCES_PER_SKILL} 个，只保留前 {MAX_RESOURCES_PER_SKILL} 个")
        candidates = sorted(candidates, key=lambda c: (c[0].count("/"), c[1].file_size))[:MAX_RESOURCES_PER_SKILL]

    unique = f"u{user_id}_{uuid.uuid4().hex[:10]}_{_safe_skill_stem(name)}"
    package_root = os.path.join(os.path.dirname(SKILLS_ROOT), "skills_packages", "imported", unique)
    resource_dir = os.path.join(package_root, "resources")
    config_file = f"imported/{unique}.yml"
    yml_path = os.path.join(SKILLS_ROOT, config_file)
    try:
        os.makedirs(os.path.join(SKILLS_ROOT, "imported"), exist_ok=True)
        os.makedirs(resource_dir, exist_ok=True)
        saved: List[str] = []
        sizes: Dict[str, int] = {}
        for rel, info in candidates:
            # 本平台能力包的资源路径是相对 resources/ 的
            store_rel = rel[len("resources/"):] if manifest and rel.startswith("resources/") else rel
            with zf.open(info) as src:
                data = src.read(MAX_RESOURCE_BYTES + 1)
            if len(data) > MAX_RESOURCE_BYTES:
                continue
            target = _target_within(resource_dir, store_rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(data)
            saved.append(store_rel)
            sizes[store_rel] = len(data)

        bundle_dir = ""
        if py_scripts:
            bundle_dir = os.path.join(package_root, "bundle")
            kept = skipped = total_bytes = 0
            for n, info in sorted(own.items()):
                rel = n[len(prefix):]
                if os.path.splitext(rel.lower())[1] in BUNDLE_BLOCKED_EXTS:
                    continue
                if (info.file_size > MAX_BUNDLE_FILE_BYTES or kept >= MAX_BUNDLE_FILES
                        or total_bytes + info.file_size > MAX_BUNDLE_TOTAL_BYTES):
                    skipped += 1
                    continue
                target = _target_within(bundle_dir, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                # 压缩包里声明的大小可以造假，按实际读到的字节数为准
                limit = min(MAX_BUNDLE_FILE_BYTES, MAX_BUNDLE_TOTAL_BYTES - total_bytes)
                with zf.open(info) as src:
                    data = src.read(limit + 1)
                if len(data) > limit:
                    skipped += 1
                    continue
                with open(target, "wb") as dst:
                    dst.write(data)
                kept += 1
                total_bytes += len(data)
            if skipped:
                notes.append(f"{skipped} 个文件太大或太多，没有放进脚本包，依赖它们的脚本可能报错")
            py_scripts = [p for p in py_scripts if os.path.exists(os.path.join(bundle_dir, *p.split("/")))]

        manifest_allowed = (manifest.get("permissions") or {}).get("file_read") if manifest else None
        if manifest_allowed:
            allowed = [p for p in (str(x).replace("\\", "/") for x in manifest_allowed) if p in sizes]
        else:
            allowed, used = [], 0
            # 许可证/版权声明对回答没有帮助，不占提示词预算（文件仍然保存）
            eligible = [p for p in saved if not os.path.basename(p).lower().startswith(("license", "licence", "copying", "notice"))]
            for p in sorted(eligible, key=lambda p: (p.count("/"), sizes[p])):
                cost = min(sizes[p], PER_RESOURCE_PROMPT_CHARS)
                if used + cost <= RESOURCE_PROMPT_BUDGET:
                    allowed.append(p)
                    used += cost
            if len(allowed) < len(eligible):
                notes.append(
                    f"{len(eligible)} 个参考文档里只有 {len(allowed)} 个并入了提示词（为了控制每次对话的用量），"
                    "其余已保存但不会被助手读取"
                )

        parts: List[str] = []
        if description:
            parts.append(f"【适用场景】\n{description[:1000]}")
        if manifest.get("constraints"):
            parts.append(f"【约束】\n{manifest['constraints']}")
        if manifest.get("output_format"):
            parts.append(f"【输出格式】\n{manifest['output_format']}")
        parts.append(body.strip())

        runtime_config = {
            "name": name,
            "description": description,
            "version": str(manifest.get("version") or meta.get("version") or "1.0.0"),
            "tools": tools,
            "permissions": {
                "network": bool((manifest.get("permissions") or {}).get("network", False)),
                "file_read": allowed,
                "exec": False,
            },
            "resource_root": resource_dir,
            "resources": saved,
            "system_prompt": "\n\n".join(parts),
        }
        if not manifest:
            runtime_config["origin"] = "official"  # 运行环境说明（能不能跑脚本）在绑定到助手时按当前沙箱状态生成
        if py_scripts:
            py_scripts = py_scripts[:200]
            report = analyze_bundle(bundle_dir, py_scripts)
            runtime_config["scripts_root"] = bundle_dir
            runtime_config["scripts"] = py_scripts
            runtime_config["runnable_scripts"] = report["runnable"]
            runtime_config["script_report"] = {k: report[k] for k in ("status", "total", "missing_packages", "network", "system", "problems")}
            notes.append(_report_note(report))
        with open(yml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(runtime_config, f, allow_unicode=True, sort_keys=False)
        invalidate_skill_config(config_file)

        validation = validate_skill_config_file(config_file)
        if not validation["ok"]:
            raise SkillImportError("配置校验未通过：" + "；".join(validation["errors"][:3]))
        skill = dao_create(
            db=db, user_id=user_id, name=name[:255], description=description[:500],
            config_file=config_file, is_public=is_public,
        )
        if not skill:
            raise SkillImportError("保存失败，请稍后重试")
    except Exception:
        shutil.rmtree(package_root, ignore_errors=True)
        if os.path.exists(yml_path):
            os.remove(yml_path)
        raise

    result = _skill_to_dict(skill)
    result["notes"] = notes
    result["resource_count"] = len(saved)
    result["prompt_resource_count"] = len(allowed)
    result["script_count"] = len(py_scripts)
    result["script_status"] = runtime_config.get("script_report", {}).get("status", "none")
    result["script_runnable"] = len(runtime_config.get("runnable_scripts", []))
    return result


# ---------------------------------------------------------------- yml

def _import_yaml(db, user_id: int, filename: str, content: bytes, is_public: int, commit: bool) -> Dict[str, Any]:
    from .import_export import import_skill_from_upload

    try:
        raw = yaml.safe_load(content.decode("utf-8-sig"))
    except (yaml.YAMLError, UnicodeDecodeError):
        raise SkillImportError("YAML 文件无法解析，请检查缩进和编码（需要 UTF-8）")
    if not isinstance(raw, dict):
        raise SkillImportError("YAML 根节点必须是键值对（name、tools、system_prompt 等）")
    if not raw.get("name"):
        raise SkillImportError("YAML 缺少 name 字段")
    if not isinstance(raw.get("tools"), list):
        raise SkillImportError("YAML 缺少 tools 字段（不需要工具时写 tools: []）")
    if any(k in raw for k in ("scripts_root", "scripts", "origin")):
        raise SkillImportError("YAML 里不能写 scripts_root / scripts / origin：这些只在导入 Skill 包时由系统生成")
    import service.tools  # noqa: F401
    from service.tools.base import ToolRegistry
    available = set(ToolRegistry.list_all())
    missing = [
        n for n in ((t if isinstance(t, str) else (t or {}).get("name")) for t in raw["tools"])
        if n and n not in available
    ]
    if missing:
        raise SkillImportError(f"YAML 里的工具本平台没有：{', '.join(map(str, missing))}。可用工具：{', '.join(sorted(available))}")

    skill = import_skill_from_upload(db, user_id, filename, content, is_public=is_public, commit=commit)
    if not skill:
        raise SkillImportError("YAML 配置未通过校验，请检查 resources / permissions 字段")
    skill["notes"] = []
    return {"imported": [skill], "failed": []}
