"""企业组织管理后台路由：部门增删/成员分配/部门负责人/企业角色/权限关系查看/
中央与部门 Agent 管理。

见 service/organization_admin_service.py、service/agent_admin_service.py 顶部
注释——网关跟 FasdtApi/admin.py 的其它端点一样走平台超级管理员
（`get_current_admin_user_async`），不是企业内部角色。
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from models.async_db import get_async_db
from models.init_db import User
from service import agent_admin_service
from service import organization_admin_service as svc
from service.dependencies import get_current_admin_user_async

router = APIRouter(prefix="/admin/org", tags=["企业组织管理"])


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    department_code: Optional[str] = Field(default=None, description="部门业务类型：hr/procurement/sales/finance/it")


class TeamUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    status: Optional[str] = Field(default=None, description="active/disabled")
    department_code: Optional[str] = Field(default=None, description="部门业务类型，不传则不改；传空字符串表示清空")


class TeamMemberCreate(BaseModel):
    user_id: int
    role_code: str = Field(default="member", description="部门角色代码：admin(负责人)/editor/member")


class TeamMemberRoleUpdate(BaseModel):
    role_code: str


class OrgMemberCreate(BaseModel):
    user_id: int
    role_code: str = Field(default="member", description="企业角色代码：owner/admin/auditor/member")


class OrgMemberUpdate(BaseModel):
    role_code: Optional[str] = None
    status: Optional[str] = Field(default=None, description="active/disabled")


class ManagedAgentCreate(BaseModel):
    template_id: Optional[str] = None
    name: str = Field(min_length=1, max_length=255)
    agent_type: str = Field(description="central/department")
    department_code: Optional[str] = Field(default=None, description="agent_type=department 时必填")
    team_id: Optional[int] = Field(default=None, description="agent_type=department 时必填")
    model_name: str = Field(default="glm-4")
    role: Optional[str] = None
    task: Optional[str] = None
    constraints: Optional[str] = None
    output: Optional[str] = None


class ManagedAgentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    department_code: Optional[str] = None
    team_id: Optional[int] = None
    model_name: Optional[str] = None
    lifecycle_status: Optional[str] = Field(default=None, description="draft/reviewing/published/retired")
    # 必须传：这是给管理员编辑表单用的入口（读旧值→改→保存），不接受"不比对版本"
    # 这个退路——见 FasdtApi/skill_route.py::SkillUpdate.expected_row_version 的
    # 同一条理由。
    expected_row_version: int = Field(description="乐观锁：必须跟数据库当前 row_version 一致才允许更新")
    role: Optional[str] = None
    task: Optional[str] = None
    constraints: Optional[str] = None
    output: Optional[str] = None


@router.get("/roles", summary="企业角色目录（组织/部门两个 scope，供角色选择器用）")
async def get_enterprise_roles(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.list_enterprise_roles(async_db)


@router.get("/teams", summary="部门列表")
async def get_teams(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.list_teams(async_db)


@router.post("/teams", summary="新建部门")
async def create_team(
        data: TeamCreate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.create_team(async_db, data.name, current_user.id, department_code=data.department_code)


@router.patch("/teams/{team_id}", summary="重命名/启用/停用/设置业务类型")
async def update_team(
        team_id: int,
        data: TeamUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    # department_code 没传就不改（跟 name/status 当前都传了才改不是一回事——
    # department_code 的合法取值本身包含 None，"没传"和"传了 null 清空"要能区分，
    # 用 model_fields_set 判断请求体里有没有这个 key，不能只看值是不是 None。
    kwargs = {}
    if "department_code" in data.model_fields_set:
        kwargs["department_code"] = data.department_code
    return await svc.update_team(async_db, team_id, current_user.id, name=data.name, status=data.status, **kwargs)


@router.get("/teams/{team_id}/permissions", summary="部门权限关系总览：成员+角色/绑定知识库空间/绑定部门Agent")
async def get_team_permissions(
        team_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.team_permissions(async_db, team_id)


@router.get("/teams/{team_id}/members", summary="部门成员列表")
async def get_team_members(
        team_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.list_team_members(async_db, team_id)


@router.post("/teams/{team_id}/members", summary="给部门分配成员（role_code=admin 即设为部门负责人）")
async def add_team_member(
        team_id: int,
        data: TeamMemberCreate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.add_team_member(async_db, team_id, current_user.id, data.user_id, data.role_code)


@router.patch("/teams/{team_id}/members/{user_id}", summary="调整部门成员角色（含设置/取消部门负责人）")
async def update_team_member(
        team_id: int,
        user_id: int,
        data: TeamMemberRoleUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.update_team_member_role(async_db, team_id, current_user.id, user_id, data.role_code)


@router.delete("/teams/{team_id}/members/{user_id}", summary="将成员移出部门")
async def remove_team_member(
        team_id: int,
        user_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.remove_team_member(async_db, team_id, current_user.id, user_id)


@router.get("/members", summary="企业成员列表（组织维度，独立于具体部门）")
async def get_org_members(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.list_org_members(async_db)


@router.post("/members", summary="添加企业成员并设置企业角色")
async def add_org_member(
        data: OrgMemberCreate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.add_org_member(async_db, current_user.id, data.user_id, data.role_code)


@router.patch("/members/{user_id}", summary="调整企业角色或启停企业成员身份")
async def update_org_member(
        user_id: int,
        data: OrgMemberUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.update_org_member(async_db, current_user.id, user_id, role_code=data.role_code, status=data.status)


@router.delete("/members/{user_id}", summary="移出企业（连带清除该用户在所有部门里的身份）")
async def remove_org_member(
        user_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.remove_org_member(async_db, current_user.id, user_id)


@router.get("/agents", summary="中央/部门 Agent 列表")
async def get_managed_agents(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await agent_admin_service.list_managed_agents(async_db)


@router.get("/agent-templates", summary="企业专业 Agent 模板")
async def get_managed_agent_templates(current_user: User = Depends(get_current_admin_user_async)):
    from service.enterprise_agent_templates import list_templates
    return list_templates()


@router.post("/agents", summary="创建中央/部门 Agent（默认 draft，需要单独发布才会被路由使用）")
async def create_managed_agent(
        data: ManagedAgentCreate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await agent_admin_service.create_managed_agent(
        async_db, current_user.id, data.name, data.agent_type,
        department_code=data.department_code, team_id=data.team_id, model_name=data.model_name,
        role=data.role, task=data.task, constraints=data.constraints, output=data.output,
        template_id=data.template_id,
    )


@router.patch("/agents/{agent_id}", summary="更新中央/部门 Agent（含改绑部门、发布/退役）")
async def update_managed_agent(
        agent_id: int,
        data: ManagedAgentUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await agent_admin_service.update_managed_agent(
        async_db, agent_id, current_user.id, name=data.name, department_code=data.department_code,
        team_id=data.team_id, model_name=data.model_name, lifecycle_status=data.lifecycle_status,
        expected_row_version=data.expected_row_version,
        role=data.role, task=data.task, constraints=data.constraints, output=data.output,
    )
