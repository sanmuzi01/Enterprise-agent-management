"""Skill 相关的共享小工具函数：命名、ORM->dict 转换、权限归一化、模板归属判断。"""
from typing import Any, Dict, List, Optional


def _safe_skill_stem(name: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", (name or "skill")).strip("_") or "skill"


def _skill_to_dict(skill) -> Dict:
    # Skill ORM对象转dict
    return {
        "id": skill.id,
        "user_id": skill.user_id,
        "name": skill.name,
        "description": skill.description,
        "config_file": skill.config_file,
        "is_public": skill.is_public,
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "lifecycle_status": skill.lifecycle_status,
        "row_version": skill.row_version,
    }


def _normalize_permission_payload(permissions: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    permissions = permissions or {}
    return {
        "network": bool(permissions.get("network", False)),
        "file_read": [str(item).replace("\\", "/") for item in permissions.get("file_read", []) if str(item).strip()],
        "exec": False,
    }


def _is_user_template(template_filename: str) -> bool:
    return template_filename.startswith("user_templates/")


def _can_use_template(user_id: int, template_filename: str) -> bool:
    return not _is_user_template(template_filename) or template_filename.startswith(f"user_templates/u{user_id}_")


def _list_package_resources(resources_root: str) -> List[str]:
    import os

    if not os.path.exists(resources_root):
        return []
    result = []
    for root, _, files in os.walk(resources_root):
        for filename in files:
            full_path = os.path.join(root, filename)
            result.append(os.path.relpath(full_path, resources_root).replace("\\", "/"))
    return sorted(result)
