"""Skill 的增删改查（数据库记录 + 运行时 YML 配置文件）。"""
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from service.access_control import can_manage_skill, can_read_skill
from service.exceptions import InvalidInput
from service.lifecycle import VALID_LIFECYCLE_STATUSES
from models.skill_dao import (
    create_skill as dao_create,
    get_skill_by_id as dao_get,
    list_skills_by_user as dao_list_user,
    list_public_skills as dao_list_public,
    list_all_skills as dao_list_all,
    update_skill as dao_update,
    delete_skill as dao_delete,
)
from service.skills.loader import (
    invalidate_skill_config,
    load_skill_config,
    list_available_templates,
    get_template_path,
)
from utils.logger_handler import get_logger

from .versioning import delete_versions, snapshot_before_edit
from .config_io import atomic_write_validated
from .common import _can_use_template, _normalize_permission_payload, _safe_skill_stem, _skill_to_dict
from .validation import _validate_tool_names

logger = get_logger("skill_service")


def create_skill(db: Session, user_id: int, name: str, description: str,
                 template_filename: str = "", is_public: int = 0,
                 system_prompt: str = "", tool_names: Optional[List[str]] = None,
                 permissions: Optional[Dict[str, Any]] = None,
                 commit: bool = True) -> Optional[Dict]:
    # 新建 Skill 要生成用户自己的运行时 YML，否则只是数据库里的一条模板引用。
    import uuid
    import yaml

    template_cfg = {
        "name": name,
        "description": "",
        "version": "1.0",
        "tool_names": [],
        "tool_defaults_map": {},
        "system_prompt": "",
        "permissions": {"network": False, "file_read": [], "exec": False},
        "resources": [],
        "resource_root": "",
    }
    if template_filename:
        available = list_available_templates()
        if template_filename not in available or not _can_use_template(user_id, template_filename):
            logger.warning(f"创建Skill失败：模板不存在 {template_filename}")
            return None
        try:
            template_cfg = load_skill_config(get_template_path(template_filename))
        except Exception as e:
            logger.warning(f"创建Skill失败：模板配置不可用 {template_filename}, error={e}")
            return None

    if not template_filename and not system_prompt.strip():
        logger.warning("创建Skill失败：空白创建时必须填写Skill指令")
        return None

    selected_tool_names = _validate_tool_names(tool_names if tool_names is not None else template_cfg.get("tool_names", []))
    if selected_tool_names is None:
        logger.warning(f"创建Skill失败：工具不存在或未选择工具 {tool_names}")
        return None
    template_defaults = template_cfg.get("tool_defaults_map", {})
    runtime_tools = [
        {"name": tool_name, "defaults": template_defaults.get(tool_name, {})}
        for tool_name in selected_tool_names
    ]

    prompt_parts = []
    if template_cfg.get("system_prompt"):
        prompt_parts.append(template_cfg["system_prompt"])
    if system_prompt and system_prompt.strip():
        prompt_parts.append(system_prompt.strip())

    config_file = f"user_created/u{user_id}_{uuid.uuid4().hex[:10]}_{_safe_skill_stem(name)}.yml"

    runtime_config = {
        "name": name,
        "description": description or template_cfg.get("description", ""),
        "version": template_cfg.get("version", "1.0"),
        "tools": runtime_tools,
        "permissions": _normalize_permission_payload(permissions or template_cfg.get("permissions")),
        "resource_root": template_cfg.get("resource_root", ""),
        "resources": template_cfg.get("resources", []),
        "system_prompt": "\n\n".join(prompt_parts),
    }

    try:
        validation = atomic_write_validated(
            config_file,
            yaml.safe_dump(runtime_config, allow_unicode=True, sort_keys=False),
        )
        if not validation["ok"]:
            raise ValueError("；".join(validation["errors"][:2]))
    except Exception as e:
        logger.error(f"创建Skill配置文件失败: {e}")
        return None

    skill = dao_create(db=db,user_id=user_id,name=name,
        description=description,config_file=config_file,
        is_public=is_public)
    if not skill:
        return None
    if commit:
        db.commit()
    return _skill_to_dict(skill)


def _write_skill_config(config_file: str, name: str, description: str,
                        system_prompt: str, tool_names: List[str],
                        permissions: Optional[Dict[str, Any]] = None) -> bool:
    import yaml

    selected_tool_names = _validate_tool_names(tool_names)
    if selected_tool_names is None:
        logger.warning(f"保存Skill配置失败：工具不存在或未选择工具 {tool_names}")
        return False

    try:
        old_cfg = load_skill_config(config_file)
        runtime_config = {
            "name": name,
            "description": description,
            "version": old_cfg.get("version", "1.0"),
            "tools": [{"name": tool_name, "defaults": old_cfg.get("tool_defaults_map", {}).get(tool_name, {})}
                      for tool_name in selected_tool_names],
            "permissions": _normalize_permission_payload(permissions or old_cfg.get("permissions")),
            "resource_root": old_cfg.get("resource_root", ""),
            "resources": old_cfg.get("resources", []),
            "system_prompt": system_prompt,
        }
        # 导入的 Skill 自带的脚本包信息：编辑提示词/工具时不能丢
        for key in ("origin", "scripts_root", "scripts", "runnable_scripts", "script_report"):
            if old_cfg.get(key):
                runtime_config[key] = old_cfg[key]
        validation = atomic_write_validated(
            config_file,
            yaml.safe_dump(runtime_config, allow_unicode=True, sort_keys=False),
        )
        if not validation["ok"]:
            logger.warning(f"保存Skill配置校验失败: config_file={config_file}, errors={validation['errors'][:2]}")
            return False
        return True
    except Exception as e:
        logger.error(f"保存Skill配置失败: config_file={config_file}, error={e}")
        return False


def update_skill_config(db: Session, skill_id: int, user_id: int, *,
                        system_prompt: Optional[str] = None,
                        tool_names: Optional[List[str]] = None,
                        permissions: Optional[Dict[str, Any]] = None,
                        allow_admin: bool = False,
                        snapshot: bool = True) -> bool:
    skill = dao_get(db, skill_id)
    if not can_manage_skill(skill, user_id, allow_admin):
        return False
    if not skill.config_file.startswith(("user_created/", "imported/")):
        logger.warning(f"拒绝修改内置模板Skill配置: skill_id={skill_id}, config={skill.config_file}")
        return False
    if snapshot:
        snapshot_before_edit(db, skill_id, user_id, allow_admin)
    cfg = load_skill_config(skill.config_file)
    return _write_skill_config(
        config_file=skill.config_file,
        name=skill.name,
        description=skill.description or cfg.get("description", ""),
        system_prompt=cfg.get("system_prompt", "") if system_prompt is None else system_prompt,
        tool_names=cfg.get("tool_names", []) if tool_names is None else tool_names,
        permissions=cfg.get("permissions", {}) if permissions is None else permissions,
    )


def get_skill_config(
    db: Session, skill_id: int, user_id: int, allow_admin: bool = False,
) -> Optional[Dict[str, Any]]:
    skill = dao_get(db, skill_id)
    if skill is None or (not allow_admin and not can_read_skill(skill, user_id)):
        return None
    cfg = load_skill_config(skill.config_file)
    return {
        "name": cfg.get("name", skill.name),
        "description": cfg.get("description", skill.description or ""),
        "version": cfg.get("version", "1.0"),
        "tools": cfg.get("tools", []),
        "tool_names": cfg.get("tool_names", []),
        "system_prompt": cfg.get("system_prompt", ""),
        "permissions": cfg.get("permissions", {"network": False, "file_read": [], "exec": False}),
        "resources": cfg.get("resources", []),
    }


def reanalyze_all_scripts(db: Session) -> Dict[str, int]:
    """重新检查所有带脚本的 Skill（含用户从商店安装的副本）。管理员在沙箱依赖变化后使用。"""
    from .script_report import reanalyze_config

    checked = changed = failed = 0
    for skill in dao_list_all(db):
        try:
            res = reanalyze_config(skill.config_file)
        except Exception as e:  # noqa: BLE001 - 单个配置坏了不能拖垮整批
            logger.warning(f"重新检查脚本失败: skill={skill.id}, error={e}")
            failed += 1
            continue
        if res.get("skipped"):
            continue
        checked += 1
        changed += bool(res.get("changed"))
    return {"checked": checked, "changed": changed, "failed": failed}


def get_skill(
    db: Session, skill_id: int, user_id: int = None, allow_admin: bool = False,
) -> Optional[Dict]:
    skill = dao_get(db, skill_id)
    if skill and user_id is not None and not allow_admin and not can_read_skill(skill, user_id):
        logger.warning(f"权限拒绝：用户{user_id}尝试查看私有Skill {skill_id}")
        return None
    return _skill_to_dict(skill) if skill else None


def list_user_skills(db: Session, user_id: int) -> List[Dict]:
    skills = dao_list_user(db, user_id)
    return [_skill_to_dict(s) for s in skills]


def list_public_skills(db: Session) -> List[Dict]:
    skills = dao_list_public(db)
    return [_skill_to_dict(s) for s in skills]


def list_all_skills(db: Session) -> List[Dict]:
    skills = dao_list_all(db)
    return [_skill_to_dict(s) for s in skills]


def update_skill(
    db: Session, skill_id: int, user_id: int, commit: bool = True,
    allow_admin: bool = False, snapshot: bool = True, expected_row_version: Optional[int] = None, **kwargs,
) -> Optional[Dict]:
    # 先查Skill是否存在
    skill = dao_get(db, skill_id)
    if not skill:
        logger.warning(f"更新Skill失败：不存在 id={skill_id}")
        return None
    # 权限校验：只有创建者能更新
    if not can_manage_skill(skill, user_id, allow_admin):
        logger.warning(f"权限拒绝：用户{user_id}尝试更新别人的Skill {skill_id}")
        return None
    if "lifecycle_status" in kwargs and kwargs["lifecycle_status"] not in VALID_LIFECYCLE_STATUSES:
        raise InvalidInput(f"lifecycle_status 只能是 {sorted(VALID_LIFECYCLE_STATUSES)} 之一")
    if snapshot:
        snapshot_before_edit(db, skill_id, user_id, allow_admin)
    # 如果更新了模板文件，校验是否存在
    if "template_filename" in kwargs:
        template = kwargs.pop("template_filename")
        if template:
            available = list_available_templates()
            if template not in available:
                logger.warning(f"更新Skill失败：模板不存在 {template}")
                return None
            kwargs["config_file"] = get_template_path(template)

    updated_skill = dao_update(db, skill_id, expected_row_version=expected_row_version, **kwargs)
    if updated_skill and commit:
        db.commit()
    return _skill_to_dict(updated_skill) if updated_skill else None


def update_skill_with_config(
        db: Session,
        skill_id: int,
        user_id: int,
        fields: Dict[str, Any],
        config_fields: Dict[str, Any],
        allow_admin: bool = False,
        expected_row_version: Optional[int] = None,
) -> Optional[Dict]:
    """统一更新 Skill 基础信息和运行配置。

    路由层不再拆分事务；基础字段和 YML 运行配置都成功后，才提交数据库变更。
    """
    if not fields and not config_fields:
        return None
    skill_model = dao_get(db, skill_id)
    if not can_manage_skill(skill_model, user_id, allow_admin):
        return None
    requested_status = fields.get("lifecycle_status")
    is_explicit_status_change = requested_status is not None and requested_status != skill_model.lifecycle_status
    if config_fields and skill_model.lifecycle_status == "published" and not is_explicit_status_change:
        # 已发布的 Skill 改运行配置（system_prompt/tool_names/permissions）就是在动
        # "所有已绑定它的人正在用的东西"，不能悄悄原地生效——强制打回 draft，运行时
        # （get_agent_skills_merged_config）会跟着立刻停止把它喂给非作者的 Agent，
        # 管理员确认没问题后要再手动发布一次，等于强制走一遍"重新审核"。
        # 判断"调用方是不是真的主动决定了状态"不能只看 fields 里有没有
        # lifecycle_status 这个键——前端编辑弹窗每次保存都会带上当前选中的状态
        # （哪怕没改过下拉框），所以只有请求里的值跟数据库现有值不一样，才算是
        # 管理员自己主动做的状态决定，此时才不覆盖（比如同时把它改成"已退役"）。
        fields = {**fields, "lifecycle_status": "draft"}
    snapshot_before_edit(db, skill_id, user_id, allow_admin)
    if fields:
        skill = update_skill(
            db, skill_id, user_id=user_id, commit=False,
            allow_admin=allow_admin, snapshot=False, expected_row_version=expected_row_version, **fields,
        )
        if not skill:
            return None
    if config_fields:
        if not update_skill_config(
            db, skill_id, user_id=user_id, allow_admin=allow_admin,
            snapshot=False, **config_fields,
        ):
            return None
    db.commit()
    skill = get_skill(db, skill_id, user_id=user_id, allow_admin=allow_admin)
    if skill:
        skill["config"] = get_skill_config(db, skill_id, user_id=user_id, allow_admin=allow_admin)
    return skill


def delete_skill(db: Session, skill_id: int, user_id: int, allow_admin: bool = False) -> bool:
    """删除Skill（加权限校验：只有创建者能删除）"""
    skill = dao_get(db, skill_id)
    if not skill:
        return False
    if not can_manage_skill(skill, user_id, allow_admin):
        logger.warning(f"权限拒绝：用户{user_id}尝试删除别人的Skill {skill_id}")
        return False
    config_file = skill.config_file
    delete_versions(db, skill_id)
    success = dao_delete(db, skill_id)
    if success:
        invalidate_skill_config(config_file)
        db.commit()
    return success
