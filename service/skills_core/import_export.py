"""Skill 导入（yml / zip 包）与导出。"""
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from service.access_control import can_read_skill
from models.skill_dao import create_skill as dao_create, get_skill_by_id as dao_get
from service.skills.loader import SKILLS_ROOT, invalidate_skill_config, load_skill_config
from utils.logger_handler import get_logger

from .common import _list_package_resources, _normalize_permission_payload, _safe_skill_stem, _skill_to_dict
from .validation import validate_skill_config_file

logger = get_logger("skill_service")


def import_skill_from_upload(
        db: Session,
        user_id: int,
        filename: str,
        content: bytes,
        is_public: int = 0,
        commit: bool = True,
) -> Optional[Dict]:
    """导入用户上传的 Skill。

    支持两种格式：
    1. 单个 .yml/.yaml：直接安装到 skills/imported/
    2. .zip Skill Package：读取 manifest.yaml + SKILL.md，转换为运行时YML
    """
    import os
    import re
    import uuid
    import zipfile
    import shutil
    import yaml
    from io import BytesIO

    def safe_stem(name: str) -> str:
        raw = os.path.splitext(os.path.basename(name))[0] or "skill"
        return re.sub(r"[^a-zA-Z0-9_\-一-鿿]+", "_", raw).strip("_") or "skill"

    def ensure_safe_zip_member(member: str) -> bool:
        normalized = member.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            return False
        blocked_exts = {
            ".py", ".pyc", ".pyd", ".exe", ".dll", ".bat", ".cmd", ".ps1",
            ".sh", ".msi", ".scr", ".com", ".jar",
        }
        return os.path.splitext(normalized.lower())[1] not in blocked_exts

    imported_dir = os.path.join(SKILLS_ROOT, "imported")
    os.makedirs(imported_dir, exist_ok=True)
    stem = safe_stem(filename)
    unique = f"u{user_id}_{uuid.uuid4().hex[:10]}_{stem}"
    ext = os.path.splitext(filename.lower())[1]

    try:
        if ext in {".yml", ".yaml"}:
            config_file = f"imported/{unique}.yml"
            yml_path = os.path.join(SKILLS_ROOT, config_file)
            with open(yml_path, "wb") as f:
                f.write(content)
            invalidate_skill_config(config_file)
            validation = validate_skill_config_file(config_file)
            if not validation["ok"]:
                os.remove(yml_path)
                logger.warning(f"导入Skill失败，配置校验未通过: {validation}")
                return None
            cfg = load_skill_config(config_file)
            skill = dao_create(
                db=db,
                user_id=user_id,
                name=cfg.get("name") or stem,
                description=cfg.get("description") or "",
                config_file=config_file,
                is_public=is_public,
            )
            if skill and commit:
                db.commit()
            return _skill_to_dict(skill) if skill else None

        if ext != ".zip":
            logger.warning(f"不支持的Skill导入格式: {filename}")
            return None

        package_root = os.path.join(
            os.path.dirname(SKILLS_ROOT),
            "skills_packages",
            "imported",
            unique,
        )
        os.makedirs(package_root, exist_ok=True)
        from service import archive_guard
        try:
            archive_guard.inspect_zip(content, max_members=3000, max_uncompressed=200 * 1024 * 1024)   # 先体检：成员数 / 总大小 / 压缩比 / 路径
        except archive_guard.ArchiveRejected as exc:
            logger.warning(f"Skill包未通过安全检查: {exc}")
            shutil.rmtree(package_root, ignore_errors=True)
            return None
        with zipfile.ZipFile(BytesIO(content)) as zf:
            unsafe = [n for n in zf.namelist() if not ensure_safe_zip_member(n)]
            if unsafe:
                logger.warning(f"Skill包包含不安全文件: {unsafe[:5]}")
                shutil.rmtree(package_root, ignore_errors=True)
                return None
            zf.extractall(package_root)

        manifest_path = os.path.join(package_root, "manifest.yaml")
        if not os.path.exists(manifest_path):
            manifest_path = os.path.join(package_root, "manifest.yml")
        skill_md_path = os.path.join(package_root, "SKILL.md")
        if not os.path.exists(manifest_path) or not os.path.exists(skill_md_path):
            logger.warning(f"Skill包缺少 manifest.yaml 或 SKILL.md: {filename}")
            shutil.rmtree(package_root, ignore_errors=True)
            return None

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = yaml.safe_load(f) or {}
        with open(skill_md_path, "r", encoding="utf-8") as f:
            skill_prompt = f.read()

        if not isinstance(manifest, dict):
            shutil.rmtree(package_root, ignore_errors=True)
            return None

        tools = manifest.get("tools") or []
        if not isinstance(tools, list):
            shutil.rmtree(package_root, ignore_errors=True)
            return None

        normalized_tools = []
        for item in tools:
            if isinstance(item, str):
                normalized_tools.append({"name": item, "defaults": {}})
            elif isinstance(item, dict) and item.get("name"):
                normalized_tools.append({
                    "name": item.get("name"),
                    "defaults": item.get("defaults") or {},
                })

        constraints = manifest.get("constraints") or ""
        output_format = manifest.get("output_format") or ""
        prompt_parts = []
        if constraints:
            prompt_parts.append(f"【约束】\n{constraints}")
        if output_format:
            prompt_parts.append(f"【输出格式】\n{output_format}")
        prompt_parts.append(skill_prompt)

        runtime_config = {
            "name": manifest.get("display_name") or manifest.get("name") or stem,
            "description": manifest.get("description") or "",
            "version": str(manifest.get("version") or "1.0.0"),
            "tools": normalized_tools,
            "permissions": {
                "network": bool((manifest.get("permissions") or {}).get("network", False)),
                "file_read": (manifest.get("permissions") or {}).get("file_read") or [],
                "exec": False,
            },
            "resource_root": os.path.join(package_root, "resources"),
            "resources": manifest.get("resources") or [
                path
                for path in _list_package_resources(os.path.join(package_root, "resources"))
            ],
            "system_prompt": "\n\n".join(prompt_parts),
        }

        config_file = f"imported/{unique}.yml"
        yml_path = os.path.join(SKILLS_ROOT, config_file)
        with open(yml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(runtime_config, f, allow_unicode=True, sort_keys=False)
        invalidate_skill_config(config_file)

        validation = validate_skill_config_file(config_file)
        if not validation["ok"]:
            shutil.rmtree(package_root, ignore_errors=True)
            os.remove(yml_path)
            logger.warning(f"导入Skill包失败，配置校验未通过: {validation}")
            return None
        cfg = load_skill_config(config_file)
        skill = dao_create(
            db=db,
            user_id=user_id,
            name=cfg.get("name") or stem,
            description=cfg.get("description") or "",
            config_file=config_file,
            is_public=is_public,
        )
        if skill and commit:
            db.commit()
        return _skill_to_dict(skill) if skill else None
    except Exception as e:
        logger.error(f"导入Skill失败: filename={filename}, error={e}")
        return None


def install_public_skill(db: Session, user_id: int, skill_id: int, commit: bool = True) -> Optional[Dict]:
    """把公开 Skill 安装为当前用户自己的私有副本。"""
    import os
    import uuid
    import yaml

    source = dao_get(db, skill_id)
    if not source or not can_read_skill(source, user_id) or source.is_public != 1:
        return None
    if source.user_id == user_id:
        return _skill_to_dict(source)

    try:
        cfg = load_skill_config(source.config_file)
        config_file = f"installed/u{user_id}_{uuid.uuid4().hex[:10]}_{_safe_skill_stem(source.name)}.yml"
        config_path = os.path.join(SKILLS_ROOT, config_file)
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        runtime_config = {
            "name": cfg.get("name") or source.name,
            "description": cfg.get("description") or source.description or "",
            "version": cfg.get("version", "1.0"),
            "tools": cfg.get("tools", []),
            "permissions": _normalize_permission_payload(cfg.get("permissions", {})),
            "resource_root": cfg.get("resource_root", ""),
            "resources": cfg.get("resources", []),
            "system_prompt": cfg.get("system_prompt", ""),
        }
        # 脚本包是共享的只读目录（安装的只是一份指向它的配置），不复制文件
        for key in ("origin", "scripts_root", "scripts", "runnable_scripts", "script_report"):
            if cfg.get(key):
                runtime_config[key] = cfg[key]
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(runtime_config, f, allow_unicode=True, sort_keys=False)
        invalidate_skill_config(config_file)
        validation = validate_skill_config_file(config_file)
        if not validation["ok"]:
            os.remove(config_path)
            logger.warning(f"安装公开Skill失败，配置校验未通过: {validation}")
            return None
        skill = dao_create(
            db=db,
            user_id=user_id,
            name=f"{source.name}",
            description=source.description or cfg.get("description", ""),
            config_file=config_file,
            is_public=0,
        )
        if skill and commit:
            db.commit()
        return _skill_to_dict(skill) if skill else None
    except Exception as e:
        logger.error(f"安装公开Skill失败: skill={skill_id}, user={user_id}, error={e}")
        return None


def export_skill_package(
    db: Session, skill_id: int, user_id: int, allow_admin: bool = False,
) -> Optional[Dict[str, Any]]:
    """把运行时 Skill 导出为标准 zip 包：manifest.yaml + SKILL.md。"""
    import os
    import tempfile
    import zipfile
    import yaml

    skill = dao_get(db, skill_id)
    if skill is None or (not allow_admin and not can_read_skill(skill, user_id)):
        return None
    validation = validate_skill_config_file(skill.config_file)
    if not validation["ok"]:
        raise ValueError("Skill不可导出，请先修复配置错误")
    cfg = load_skill_config(skill.config_file)
    package_name = f"{_safe_skill_stem(skill.name)}_{skill.id}.zip"
    export_path = os.path.join(tempfile.gettempdir(), package_name)
    manifest = {
        "name": _safe_skill_stem(skill.name),
        "display_name": skill.name,
        "description": skill.description or cfg.get("description", ""),
        "version": cfg.get("version", "1.0.0"),
        "source": "export",
        "source_ref": skill.config_file,
        "tools": [
            {
                "name": tool_name,
                "defaults": cfg.get("tool_defaults_map", {}).get(tool_name, {}),
            }
            for tool_name in cfg.get("tool_names", [])
        ],
        "permissions": {"network": False, "file_read": [], "exec": False},
        "resources": cfg.get("resources", []),
        "output_format": "markdown",
    }
    with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.yaml", yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False))
        zf.writestr("SKILL.md", cfg.get("system_prompt", ""))
        resource_root = cfg.get("resource_root", "")
        for resource in cfg.get("resources", []):
            rel_path = resource.get("path") if isinstance(resource, dict) else str(resource)
            if not rel_path or not resource_root:
                continue
            abs_path = os.path.abspath(os.path.join(resource_root, rel_path))
            root = os.path.abspath(resource_root)
            if os.path.isfile(abs_path) and abs_path.startswith(root + os.sep):
                zf.write(abs_path, f"resources/{rel_path}")
        # 带脚本的 Skill：脚本包按原来的相对路径放回压缩包，重新导入时目录结构不变、脚本仍可运行
        bundle_root = cfg.get("scripts_root", "")
        if bundle_root and os.path.isdir(bundle_root):
            root = os.path.abspath(bundle_root)
            written = set(zf.namelist())
            for base, _, names in os.walk(root):
                for n in names:
                    full = os.path.join(base, n)
                    rel = os.path.relpath(full, root).replace("\\", "/")
                    if rel in written or rel.lower() in {"skill.md", "manifest.yaml", "manifest.yml"}:
                        continue
                    zf.write(full, rel)
    return {"path": export_path, "filename": package_name}
