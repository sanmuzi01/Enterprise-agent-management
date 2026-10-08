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
from service import agent_admin_service, department_agent_service
from service import organization_admin_service as svc
from service.dependencies import get_current_admin_user_async
from service.exceptions import InvalidInput

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


class AgentConfig(BaseModel):
    """运行参数（都可以不填，不填 = 沿用默认或不修改）。"""
    temperature: Optional[int] = Field(default=None, ge=0, le=100)
    memory_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    rag_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    kb_top_k: Optional[int] = Field(default=None, ge=1, le=20)
    kb_rerank_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    kb_force_citation: Optional[int] = Field(default=None, ge=0, le=1)
    kb_refuse_when_empty: Optional[int] = Field(default=None, ge=0, le=1)


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
    config: Optional[AgentConfig] = None
    space_ids: Optional[list[int]] = Field(default=None, description="绑定的知识库空间；不填 = 不绑定")
    skill_ids: Optional[list[int]] = Field(default=None, description="添加的技能；不填 = 不添加")
    description: Optional[str] = Field(default=None, max_length=500)
    maintainer: Optional[str] = Field(default=None, max_length=100)


class ExternalAgentRegister(BaseModel):
    """接入工程师已经开发好的智能体服务。"""
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=500, description="它能做什么")
    maintainer: Optional[str] = Field(default=None, max_length=100, description="谁维护它（出了问题找谁）")
    agent_type: str = Field(default="department", description="central 全企业可用 / department 业务智能体（之后再划分部门）")
    department_code: Optional[str] = None
    url: str = Field(min_length=1, max_length=1000)
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=300)
    send_knowledge: Optional[bool] = None
    headers: Optional[dict] = None


class AgentAssignment(BaseModel):
    target: str = Field(description="department 划分给某个部门 / enterprise 全企业可用 / unassigned 收回成未划分")
    team_id: Optional[int] = Field(default=None, description="target=department 时必填")
    department_code: Optional[str] = Field(default=None, description="业务方向；不填则沿用部门已设置的业务类型")
    expected_row_version: Optional[int] = None


class AgentRuntimeUpdate(BaseModel):
    runtime_type: str = Field(description="builtin = 平台自带运行方式；external = 转发给企业自己的 Agent 服务")
    url: Optional[str] = Field(default=None, max_length=1000, description="外部 Agent 服务地址")
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=300)
    send_knowledge: Optional[bool] = Field(default=None, description="是否把检索到的资料片段一并发给对方（受密级策略约束）")
    headers: Optional[dict] = Field(default=None, description="附加请求头（加密保存，只写不读）；传 {} 清空")
    rotate_secret: bool = Field(default=False, description="重新生成签名密钥（旧密钥立即失效）")


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
    config: Optional[AgentConfig] = None
    space_ids: Optional[list[int]] = Field(default=None, description="绑定的知识库空间（整体替换）；不填 = 不修改")
    skill_ids: Optional[list[int]] = Field(default=None, description="添加的技能（整体替换）；不填 = 不修改")
    description: Optional[str] = Field(default=None, max_length=500)
    maintainer: Optional[str] = Field(default=None, max_length=100)


@router.get("/roles", summary="企业角色目录（组织/部门两个 scope，供角色选择器用）")
async def get_enterprise_roles(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await svc.list_enterprise_roles(async_db)


class EnterpriseUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/enterprise", summary="本企业的基本信息")
async def get_enterprise(async_db=Depends(get_async_db), current_user: User = Depends(get_current_admin_user_async)):
    return await svc.get_enterprise(async_db)


@router.patch("/enterprise", summary="修改企业名称（平台只服务一个企业）")
async def update_enterprise(data: EnterpriseUpdate, async_db=Depends(get_async_db),
                            current_user: User = Depends(get_current_admin_user_async)):
    return await svc.rename_enterprise(async_db, current_user.id, data.name)


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
    team = await svc.create_team(async_db, data.name, current_user.id, department_code=data.department_code)
    team["agent_status"] = await department_agent_service.on_team_saved(
        async_db, team["id"], current_user.id, created=True)
    return team


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
    team = await svc.update_team(async_db, team_id, current_user.id, name=data.name, status=data.status, **kwargs)
    team["agent_status"] = await department_agent_service.on_team_saved(
        async_db, team_id, current_user.id,
        department_code_changed="department_code" in kwargs, status=data.status)
    return team


@router.get("/teams/{team_id}/agent-status", summary="部门专业 Agent 的配置状态与缺失原因")
async def get_team_agent_status(
        team_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await department_agent_service.agent_status(async_db, team_id)


@router.post("/teams/{team_id}/agent-repair", summary="一键修复：按业务类型补齐部门 Agent、绑定专业技能、退役冲突 Agent")
async def repair_team_agent(
        team_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await department_agent_service.repair(async_db, team_id, current_user.id)


@router.post("/teams/{team_id}/agent-publish", summary="一键发布部门主 Agent（配置完整时）")
async def publish_team_agent(
        team_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await department_agent_service.publish(async_db, team_id, current_user.id)


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


@router.get("/handoffs", summary="中央 Agent 转交记录（转给了谁、为什么、输入摘要、备选部门）")
async def get_handoffs(
        limit: int = 100, department_code: Optional[str] = None, reason: Optional[str] = None,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    from service import handoff_service
    return await handoff_service.list_handoffs(async_db, limit, department_code, reason)


@router.get("/agent-templates", summary="企业专业 Agent 模板")
async def get_managed_agent_templates(current_user: User = Depends(get_current_admin_user_async)):
    from service.enterprise_agent_templates import list_templates
    return list_templates()


@router.post("/agents/external", summary="接入已开发好的智能体服务（登记档案 + 配好地址 + 生成签名密钥，草稿）")
async def register_external_agent(
        data: ExternalAgentRegister,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await agent_admin_service.register_external_agent(
        async_db, current_user.id, data.name, data.agent_type, data.url, description=data.description,
        maintainer=data.maintainer, department_code=data.department_code, timeout_seconds=data.timeout_seconds,
        send_knowledge=data.send_knowledge, headers=data.headers,
    )


@router.post("/agents", summary="启用内置智能体（必须选一个内置模板；自己开发的智能体请走“接入”）")
async def create_managed_agent(
        data: ManagedAgentCreate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    if not data.template_id:
        raise InvalidInput("企业智能体不能凭空填几段提示词创建：请选一个内置模板启用，或者把工程师开发好的智能体服务接入进来")
    return await agent_admin_service.create_managed_agent(
        async_db, current_user.id, data.name, data.agent_type,
        department_code=data.department_code, team_id=data.team_id, model_name=data.model_name,
        role=data.role, task=data.task, constraints=data.constraints, output=data.output,
        template_id=data.template_id,
        config=data.config.model_dump(exclude_none=True) if data.config else None,
        space_ids=data.space_ids, skill_ids=data.skill_ids,
        description=data.description, maintainer=data.maintainer,
    )


@router.get("/agent-options", summary="创建 / 编辑智能体用的可选项：模型、技能、知识库、部门")
async def get_agent_options(
        agent_id: Optional[int] = None,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await agent_admin_service.agent_options(async_db, current_user.id, agent_id)


@router.get("/agents/{agent_id}", summary="智能体的完整配置与发布前检查")
async def get_managed_agent_detail(
        agent_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await agent_admin_service.get_managed_agent_detail(async_db, agent_id)


@router.put("/agents/{agent_id}/assignment", summary="划分智能体：部门 / 全企业 / 未划分（已发布的需先停用）")
async def assign_agent(
        agent_id: int,
        data: AgentAssignment,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await agent_admin_service.assign_managed_agent(
        async_db, agent_id, current_user.id, data.target, team_id=data.team_id,
        department_code=data.department_code, expected_row_version=data.expected_row_version,
    )


@router.get("/agents/{agent_id}/runtime", summary="智能体的运行方式与外部服务接入配置")
async def get_agent_runtime(
        agent_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    from service import external_agent_admin_service
    return await external_agent_admin_service.get_runtime(async_db, agent_id)


@router.put("/agents/{agent_id}/runtime", summary="设置运行方式：平台自带 / 接入企业自己的 Agent 服务")
async def set_agent_runtime(
        agent_id: int,
        data: AgentRuntimeUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    from service import external_agent_admin_service
    return await external_agent_admin_service.configure_runtime(
        async_db, agent_id, current_user.id, data.runtime_type, url=data.url, timeout_seconds=data.timeout_seconds,
        send_knowledge=data.send_knowledge, headers=data.headers, rotate_secret=data.rotate_secret,
    )


@router.post("/agents/{agent_id}/runtime/test", summary="测试外部 Agent 服务连接（只发 ping，不带对话内容）")
async def test_agent_runtime(
        agent_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    from service import external_agent_admin_service
    return await external_agent_admin_service.test_runtime(async_db, agent_id, current_user.id)


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
        config=data.config.model_dump(exclude_none=True) if data.config else None,
        space_ids=data.space_ids, skill_ids=data.skill_ids,
        description=data.description, maintainer=data.maintainer,
    )
