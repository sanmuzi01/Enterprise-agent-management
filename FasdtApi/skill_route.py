from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models.init_db import User, get_db
from models.async_db import get_async_db
from service.admin_service import is_admin_user
from service.dependencies import (
    get_current_admin_user,
    get_current_admin_user_async,
    get_current_user,
    get_current_user_async,
)
from service.exceptions import InvalidInput, NotFound
from service import skill_async_service
from service.skills_core.github_import import import_from_github
from service.skills_core.crud import reanalyze_all_scripts
from service.skills_core.translate import TranslateError, translate_skill
from service.skills_core.versioning import VersionError, list_skill_versions, restore_skill_version
from service.skills_core.package_import import MAX_UPLOAD_BYTES, SkillImportError, import_skill_bundle
from service.skill_service import (
    bind_skill,
    create_template,
    delete_template,
    export_skill_package,
    create_skill,
    delete_skill,
    get_template_config,
    install_public_skill,
    list_available_tools,
    list_templates,
    unbind_skill,
    update_agent_skills,
    update_skill_with_config,
    update_template,
)

router = APIRouter(prefix="/skill", tags=["Skill管理"])

# 迁移边界：读接口（我的/公开/单个 Skill、校验、Agent 绑定列表）已全量 AsyncSession，
# 不再保留「未装 asyncmy 回退同步」的死分支（asyncmy 已是硬依赖）。
# 创建 / 绑定 / 导入导出 / 安装公共 Skill 仍用同步 get_db —— skill_service / skills_core
# 里混了文件系统操作（写 SKILL.md、打包 zip）和同步 ORM，FastAPI 会把 def 端点放线程池。
# 待 skills_core 迁到 AsyncSession 后再统一收口，见 docs/sync-async-boundary.md。


class SkillCreate(BaseModel):
    name: str
    description: str = ""
    template_filename: str = ""
    is_public: int = 0
    system_prompt: str = ""
    tool_names: List[str] = Field(default_factory=list)
    permissions: Dict[str, Any] = Field(default_factory=dict)


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    template_filename: Optional[str] = None
    is_public: Optional[int] = None
    system_prompt: Optional[str] = None
    tool_names: Optional[List[str]] = None
    permissions: Optional[Dict[str, Any]] = None
    # 发布生命周期：draft/reviewing/published/retired（service/lifecycle.py）。
    lifecycle_status: Optional[str] = None
    # 乐观锁：这是唯一一个给"人在编辑表单里读了旧值再保存"这种场景用的入口
    # （管理员编辑弹窗），必须传，不接受"不比对版本"这个退路——不然两个管理员
    # 前后脚保存同一个 Skill，后保存的会悄悄覆盖前一个的改动，乐观锁形同虚设。
    # `service/skills_core/crud.py::update_skill`/`models/skill_dao.py::update_skill`
    # 的 `expected_row_version` 参数本身仍然是可选的——那是给 import/translate/
    # reanalyze 这类不走这个 HTTP 路由的系统内部单字段更新用的，不受这里收紧。
    expected_row_version: int


class SkillTemplateSave(BaseModel):
    name: str
    description: str = ""
    system_prompt: str
    tool_names: List[str] = Field(default_factory=list)


@router.post("/", summary="（管理员）创建Skill")
def api_create_skill(
    data: SkillCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    skill = create_skill(
        db=db,
        user_id=current_user.id,
        name=data.name,
        description=data.description,
        template_filename=data.template_filename,
        is_public=data.is_public,
        system_prompt=data.system_prompt,
        tool_names=data.tool_names,
        permissions=data.permissions,
    )
    if not skill:
        raise InvalidInput("创建Skill失败，请检查模板文件名是否正确")
    return {"code": 200, "msg": "创建成功", "data": skill}


@router.get("/", summary="（管理员）查询平台全部Skill")
async def api_list_my_skills(
    async_db=Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user_async),
):
    skills = await skill_async_service.list_all_skills(async_db)
    return {"code": 200, "msg": "查询成功", "data": skills}


@router.get("/public", summary="查询所有公开Skill")
async def api_list_public_skills(
    async_db=Depends(get_async_db),
    current_user: User = Depends(get_current_user_async),
):
    skills = await skill_async_service.list_public_skills(async_db)
    return {"code": 200, "msg": "查询成功", "data": skills}


@router.get("/templates", summary="（管理员）列出可用YML模板")
def api_list_templates(
    current_user: User = Depends(get_current_admin_user),
):
    templates = list_templates(current_user.id)
    return {"code": 200, "msg": "查询成功", "data": templates}


@router.post("/templates", summary="（管理员）创建Skill模板")
def api_create_template(
    data: SkillTemplateSave,
    current_user: User = Depends(get_current_admin_user),
):
    template = create_template(
        user_id=current_user.id,
        name=data.name,
        description=data.description,
        system_prompt=data.system_prompt,
        tool_names=data.tool_names,
    )
    if not template:
        raise InvalidInput("创建模板失败，请检查工具和指令")
    return {"code": 200, "msg": "创建成功", "data": template}


@router.get("/templates/{template_filename:path}", summary="（管理员）查询Skill模板详情")
def api_get_template(
    template_filename: str,
    current_user: User = Depends(get_current_admin_user),
):
    template = get_template_config(template_filename, user_id=current_user.id)
    if not template:
        raise NotFound("模板不存在")
    return {"code": 200, "msg": "查询成功", "data": template}


@router.put("/templates/{template_filename:path}", summary="（管理员）更新Skill模板")
def api_update_template(
    template_filename: str,
    data: SkillTemplateSave,
    current_user: User = Depends(get_current_admin_user),
):
    template = update_template(
        user_id=current_user.id,
        template_filename=template_filename,
        name=data.name,
        description=data.description,
        system_prompt=data.system_prompt,
        tool_names=data.tool_names,
    )
    if not template:
        raise InvalidInput("更新模板失败，只能修改自己的模板")
    return {"code": 200, "msg": "更新成功", "data": template}


@router.delete("/templates/{template_filename:path}", summary="（管理员）删除Skill模板")
def api_delete_template(
    template_filename: str,
    current_user: User = Depends(get_current_admin_user),
):
    success = delete_template(current_user.id, template_filename)
    if not success:
        raise InvalidInput("删除模板失败，只能删除自己的模板")
    return {"code": 200, "msg": "删除成功"}


@router.get("/tools", summary="（管理员）列出可用于Skill的工具")
def api_list_tools(
    current_user: User = Depends(get_current_admin_user),
):
    tools = list_available_tools()
    return {"code": 200, "msg": "查询成功", "data": tools}


@router.get("/{skill_id}/validate", summary="校验Skill是否可用")
async def api_validate_skill(
    skill_id: int,
    async_db=Depends(get_async_db),
    current_user: User = Depends(get_current_user_async),
):
    result = await skill_async_service.validate_skill(
        async_db, skill_id, user_id=current_user.id,
        allow_admin=is_admin_user(current_user),
    )
    if not result:
        raise NotFound("Skill不存在")
    return {"code": 200, "msg": "校验完成", "data": result}


@router.get("/{skill_id}/export", summary="（管理员）导出Skill包")
def api_export_skill(
    skill_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    try:
        package = export_skill_package(db, skill_id, user_id=current_user.id, allow_admin=True)
    except ValueError as e:
        raise InvalidInput(str(e))
    if not package:
        raise NotFound("Skill不存在")
    return FileResponse(
        package["path"],
        filename=package["filename"],
        media_type="application/zip",
    )


@router.post("/{skill_id}/install", summary="（管理员）复制公开Skill到管理库")
def api_install_public_skill(
    skill_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    skill = install_public_skill(db, current_user.id, skill_id)
    if not skill:
        raise InvalidInput("安装失败：Skill不存在或不是公开能力")
    return {"code": 200, "msg": "安装成功", "data": skill}


class GithubImport(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    is_public: int = 0


def _import_response(result: Dict[str, Any]) -> Dict[str, Any]:
    return {"code": 200, "msg": f"已导入 {len(result['imported'])} 个能力", "data": result}


def _import_policy(user: User, is_public: int) -> Dict[str, Any]:
    """脚本和"公开到能力商店"只给管理员。

    导入接口本身已经要求管理员（get_current_admin_user）；这里再按角色判断一次是纵深防御：
    以后如果放宽路由（比如让公司管理员也能导入），脚本和上架也不会跟着被悄悄放开。
    """
    admin = is_admin_user(user)
    return {"allow_scripts": admin, "is_public": 1 if (admin and is_public == 1) else 0}


@router.post("/import", summary="导入外部Skill包（官方 Skill / GitHub 仓库 zip / 本平台能力包 / yml）")
def api_import_skill(
    file: UploadFile = File(...),
    is_public: int = Form(0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    # 同步 def：解压、写文件、同步 ORM 都是阻塞操作，交给线程池，别堵事件循环
    try:
        result = import_skill_bundle(
            db=db,
            user_id=current_user.id,
            filename=file.filename or "skill.zip",
            content=file.file.read(MAX_UPLOAD_BYTES + 1),
            **_import_policy(current_user, is_public),
        )
    except SkillImportError as e:
        db.rollback()
        raise InvalidInput(str(e))
    return _import_response(result)


@router.post("/import/github", summary="通过 GitHub 链接导入 Skill（仓库或文件夹，仅公开仓库）")
def api_import_skill_from_github(
    data: GithubImport,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    try:
        result = import_from_github(db, current_user.id, data.url, **_import_policy(current_user, data.is_public))
    except SkillImportError as e:
        db.rollback()
        raise InvalidInput(str(e))
    return _import_response(result)


@router.post("/admin/reanalyze", summary="（管理员）重新检查所有 Skill 的脚本兼容性")
def api_reanalyze_skill_scripts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """沙箱依赖变化后调用，刷新每个带脚本的 Skill 的"可运行"标签，不用重新导入。"""
    result = reanalyze_all_scripts(db)
    return {"code": 200, "msg": f"已检查 {result['checked']} 个，{result['changed']} 个有变化", "data": result}


@router.get("/{skill_id}/versions", summary="（管理员）查看技能的历史版本")
def api_list_skill_versions(
    skill_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    versions = list_skill_versions(db, skill_id, current_user.id, allow_admin=True)
    if versions is None:
        raise NotFound("Skill不存在，或不是你创建的")
    return {"code": 200, "msg": "查询成功", "data": versions}


@router.post("/{skill_id}/versions/{version_id}/restore", summary="（管理员）把技能恢复到某个历史版本")
def api_restore_skill_version(
    skill_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    try:
        result = restore_skill_version(db, skill_id, version_id, current_user.id, allow_admin=True)
    except VersionError as e:
        db.rollback()
        raise InvalidInput(str(e))
    if result is None:
        raise NotFound("Skill不存在，或不是你创建的")
    return {"code": 200, "msg": f"已恢复到 v{result['restored_to']}", "data": result}


@router.post("/{skill_id}/translate", summary="（管理员）把 Skill 的名称和说明翻译成中文")
def api_translate_skill(
    skill_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    try:
        skill = translate_skill(db, skill_id, current_user.id, allow_admin=True)
    except TranslateError as e:
        db.rollback()
        raise InvalidInput(str(e))
    if not skill:
        raise NotFound("Skill不存在，或不是你创建的")
    return {"code": 200, "msg": "已翻译", "data": skill}


@router.get("/{skill_id}", summary="查询单个Skill详情")
async def api_get_skill(
    skill_id: int,
    async_db=Depends(get_async_db),
    current_user: User = Depends(get_current_user_async),
):
    skill = await skill_async_service.get_skill_with_config(
        async_db, skill_id, user_id=current_user.id,
        allow_admin=is_admin_user(current_user),
    )
    if not skill:
        raise NotFound("Skill不存在")
    return {"code": 200, "msg": "查询成功", "data": skill}


@router.put("/{skill_id}", summary="（管理员）更新Skill")
def api_update_skill(
    skill_id: int,
    data: SkillUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    payload = data.model_dump(exclude_none=True)
    expected_row_version = payload.pop("expected_row_version", None)
    config_payload = {}
    for key in ("system_prompt", "tool_names", "permissions"):
        if key in payload:
            config_payload[key] = payload.pop(key)
    skill = update_skill_with_config(
        db,
        skill_id,
        user_id=current_user.id,
        fields=payload,
        config_fields=config_payload,
        allow_admin=True,
        expected_row_version=expected_row_version,
    )
    if not skill:
        raise InvalidInput("更新失败，Skill不存在、模板文件名错误或配置不可用")
    return {"code": 200, "msg": "更新成功", "data": skill}


@router.delete("/{skill_id}", summary="（管理员）删除Skill")
def api_delete_skill(
    skill_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    success = delete_skill(db, skill_id, user_id=current_user.id, allow_admin=True)
    if not success:
        raise NotFound("Skill不存在")
    return {"code": 200, "msg": "删除成功"}


@router.post("/agent/{agent_id}/bind/{skill_id}", summary="绑定Skill到Agent")
def api_bind_skill(
    agent_id: int,
    skill_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    success = bind_skill(db, agent_id, skill_id, user_id=current_user.id)
    if not success:
        raise InvalidInput("绑定失败，请检查Agent和Skill是否存在且有权限")
    return {"code": 200, "msg": "绑定成功"}


@router.delete("/agent/{agent_id}/unbind/{skill_id}", summary="解绑Skill")
def api_unbind_skill(
    agent_id: int,
    skill_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    success = unbind_skill(db, agent_id, skill_id, user_id=current_user.id)
    if not success:
        raise InvalidInput("解绑失败")
    return {"code": 200, "msg": "解绑成功"}


@router.get("/agent/{agent_id}", summary="查询Agent绑定的所有Skill")
async def api_list_agent_skills(
    agent_id: int,
    async_db=Depends(get_async_db),
    current_user: User = Depends(get_current_user_async),
):
    skills = await skill_async_service.list_agent_skills(async_db, agent_id, user_id=current_user.id)
    return {"code": 200, "msg": "查询成功", "data": skills}


@router.put("/agent/{agent_id}", summary="批量更新Agent绑定的Skill")
def api_update_agent_skills(
    agent_id: int,
    skill_ids: List[int],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    success = update_agent_skills(db, agent_id, skill_ids, user_id=current_user.id)
    if not success:
        raise InvalidInput("更新失败")
    return {"code": 200, "msg": "更新成功"}
