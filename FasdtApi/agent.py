from fastapi import APIRouter, Depends
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from typing import Optional, List
from models.init_db import get_db, User
from models.async_db import get_async_db
from service.dependencies import get_current_user, get_current_user_async
from service.exceptions import InvalidInput, NotFound, PermissionDenied
from service import agent_service
from service import agent_async_service
from service.agent_templates import create_user_template, delete_user_template, list_templates_for_user
router = APIRouter(prefix="/agent", tags=["agent管理"])

# 迁移边界说明：
# 读接口（list/get/selected）已走 AsyncSession + agent_async_service。
# 写接口（create/update/delete/clone/select）以及 debug/dry-run 仍是同步 def +
# get_db：它们背后的 agent_service 把 db/user 当作同一会话内的 ORM 对象直接改写并
# 自行 commit（见 update_selected_agent 等），不是简单换 AsyncSession 就行。
# FastAPI 会把这些 def 端点放线程池执行，不阻塞事件循环。待 agent_service 写逻辑
# 整体迁移到 AsyncSession 后再统一收口。
class AgentResponse(BaseModel):
    id: int
    name: str
    prompt: dict | None
    model_name: str
    rag_enabled: int
    memory_enabled: int
    temperature: int
    skills: List[dict] = []
    space_ids: List[int] = []
    kb_top_k: int = 5
    kb_rerank_enabled: int = 0
    kb_force_citation: int = 1
    kb_refuse_when_empty: int = 1
    class Config:
        from_attributes = True
class AgentWithSelectedResponse(AgentResponse):
    is_selected: bool
#创建智能体用
class AgentCreate(BaseModel):
    name:str = Field(min_length=1,max_length=255)
    model_name:str=Field(default="glm-4")
    role: Optional[str] = Field(default=None)
    task: Optional[str] = Field(default=None)
    constraints: Optional[str] = Field(default=None)
    output: Optional[str] = Field(default=None)
    rag_enabled: int =  Field(default=0, ge=0, le=1)
    memory_enabled: int = Field(default=1, ge=0, le=1)
    temperature:int = Field(default=70,ge=0,le=100)
    skill_ids: List[int] = Field(default=[])
    # 知识库空间绑定 + 检索行为（阶段3）
    space_ids: List[int] = Field(default=[])
    kb_top_k: int = Field(default=5, ge=1, le=20)
    kb_rerank_enabled: int = Field(default=0, ge=0, le=1)
    kb_force_citation: int = Field(default=1, ge=0, le=1)
    kb_refuse_when_empty: int = Field(default=1, ge=0, le=1)
    # 注意：这里故意不暴露 agent_type/department_code——中央/部门 Agent 只能由企业管理员
    # 通过组织管理后台创建（后端同时设置 organization_id/team_id/scope_type，三者必须
    # 一起算，不能只给 department_code），见 FasdtApi/organization_admin.py。普通用户
    # 这个创建接口曾经直接接受这两个字段，任何登录用户都能把自己的 Agent 声明成
    # "central"/"department"，即使 get_usable_agent 的可见性判断目前把危害限制在
    # 本人范围内，语义上也不该允许——修复见 docs/enterprise-rbac-plan.md 相关记录。

# 更新用
class AgentUpdate(BaseModel):
    name:Optional[str] = Field(default=None,min_length=1,max_length=255)
    role: Optional[str] = Field(default=None)
    task: Optional[str] = Field(default=None)
    constraints: Optional[str] = Field(default=None)
    output: Optional[str] = Field(default=None)
    model_name:Optional[str]=Field(default=None)
    rag_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    memory_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    temperature:Optional[int] = Field(default=None,ge=0,le=100)
    skill_ids: Optional[List[int]] = Field(default=None)
    # 知识库空间绑定 + 检索行为（阶段3）。space_ids=None 不改绑定，[] 清空
    space_ids: Optional[List[int]] = Field(default=None)
    kb_top_k: Optional[int] = Field(default=None, ge=1, le=20)
    kb_rerank_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    kb_force_citation: Optional[int] = Field(default=None, ge=0, le=1)
    kb_refuse_when_empty: Optional[int] = Field(default=None, ge=0, le=1)


class AgentDryRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=5000)
    conversation_id: Optional[int] = Field(default=None, ge=1)

class AgentCloneRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)

class AgentTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=500)
    model_name: str = Field(default="glm-4", max_length=100)
    role: Optional[str] = Field(default="")
    task: Optional[str] = Field(default="")
    constraints: Optional[str] = Field(default="")
    output: Optional[str] = Field(default="")
    rag_enabled: int = Field(default=0, ge=0, le=1)
    memory_enabled: int = Field(default=1, ge=0, le=1)
    temperature: int = Field(default=70, ge=0, le=100)
    skill_names: List[str] = Field(default=[])

@router.get("/templates", summary="查询内置Agent模板")
def list_templates(
        current_user: User = Depends(get_current_user)):
    """返回内置和用户自定义Agent模板，前端用于一键预填创建表单。"""
    return list_templates_for_user(current_user.id)

@router.post("/templates", summary="保存自定义Agent模板")
def create_template(
        data: AgentTemplateCreate,
        current_user: User = Depends(get_current_user)):
    return create_user_template(current_user.id, data.model_dump())

@router.delete("/templates/{template_id}", summary="删除自定义Agent模板")
def delete_template(
        template_id: str,
        current_user: User = Depends(get_current_user)):
    if not template_id.startswith("custom_"):
        raise InvalidInput("内置模板不能删除")
    if not delete_user_template(current_user.id, template_id):
        raise NotFound("模板不存在或无权限")
    return {"message": "删除成功", "template_id": template_id}

@router.get("/list",summary="查询用户的智能体列表",response_model=List[AgentWithSelectedResponse])
async def list_agents(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)):
    """查询用户的智能体列表，每个智能体附带 is_selected 标记"""
    return await agent_async_service.list_agent(async_db, current_user)
@router.get("/selected/me",summary="获取选中智能体")
async def get_selected(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    return await agent_async_service.get_selected(async_db, current_user)
@router.get("/{agent_id:int}",summary="查询单个智能体信息",response_model=AgentWithSelectedResponse)
async def get_agent(
        agent_id:int,
        async_db=Depends(get_async_db),
        current_user: User =Depends(get_current_user_async) ):
    result = await agent_async_service.get_agent(async_db, current_user, agent_id)
    if result is None:
        raise NotFound("智能体不存在或无权限")
    return result

@router.get("/{agent_id:int}/debug", summary="查看Agent运行调试信息")
def get_agent_debug(
        agent_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)):
    result = agent_service.get_agent_debug(db, current_user, agent_id)
    if result is None:
        raise NotFound("智能体不存在或无权限")
    return result


@router.post("/{agent_id:int}/dry-run", summary="Agent Dry Run调试")
def dry_run_agent(
        agent_id: int,
        data: AgentDryRunRequest,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)):
    try:
        result = agent_service.dry_run_agent(
            db=db,
            user=current_user,
            agent_id=agent_id,
            user_message=data.message,
            conversation_id=data.conversation_id,
        )
    except ValueError as e:
        raise InvalidInput(str(e))
    if result is None:
        raise NotFound("智能体不存在或无权限")
    return result

@router.post("/{agent_id:int}/clone", summary="复制Agent")
def clone_agent(
        agent_id: int,
        data: AgentCloneRequest = AgentCloneRequest(),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)):
    result = agent_service.clone(db, current_user, agent_id, name=data.name)
    if result is None:
        raise NotFound("智能体不存在或无权限复制")
    if "agent_id" not in result:
        raise InvalidInput(result.get("message", "复制失败"))
    return result

@router.post("",summary="创建智能体")
def create_agent(
        agent:AgentCreate,
        db:Session = Depends(get_db),
        current_user:User = Depends(get_current_user)):
    result = agent_service.create(
        db=db,
        user=current_user,
        name=agent.name,
        role=agent.role,
        task=agent.task,
        constraints=agent.constraints,
        output=agent.output,
        model_name=agent.model_name,
        rag_enabled=agent.rag_enabled,
        memory_enabled=agent.memory_enabled,
        temperature=agent.temperature,
        skill_ids=agent.skill_ids,
        space_ids=agent.space_ids,
        kb_top_k=agent.kb_top_k,
        kb_rerank_enabled=agent.kb_rerank_enabled,
        kb_force_citation=agent.kb_force_citation,
        kb_refuse_when_empty=agent.kb_refuse_when_empty,
    )
    if "agent_id" not in result:
        raise InvalidInput(result.get("message", "创建失败"))
    return result
@router.put("/{agent_id}",summary="更新智能体信息")
def update_agent(
        agent_id:int,
        agent_update:AgentUpdate,
        db:Session = Depends(get_db),
        current_user:User = Depends(get_current_user)):
    result = agent_service.get_agent(db, current_user, agent_id)
    if result is None:
        raise NotFound("智能体不存在或无权限更新")
    update_result = agent_service.update(
        db=db,
        user=current_user,
        agent_id=agent_id,
        name=agent_update.name,
        role=agent_update.role,
        task=agent_update.task,
        constraints=agent_update.constraints,
        output=agent_update.output,
        model_name=agent_update.model_name,
        rag_enabled=agent_update.rag_enabled,
        memory_enabled=agent_update.memory_enabled,
        temperature=agent_update.temperature,
        skill_ids=agent_update.skill_ids,
        space_ids=agent_update.space_ids,
        kb_top_k=agent_update.kb_top_k,
        kb_rerank_enabled=agent_update.kb_rerank_enabled,
        kb_force_citation=agent_update.kb_force_citation,
        kb_refuse_when_empty=agent_update.kb_refuse_when_empty,
    )
    if update_result.get("message") != "更新成功":
        raise InvalidInput(update_result.get("message", "更新失败"))
    return update_result
@router.get("/{agent_id}/delete_preview", summary="删除智能体预检（返回将被删除的数据量）")
def delete_preview_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """前端展示删除确认弹窗时先调这个，拿到影响范围再让用户确认"""
    result = agent_service.delete_preview(db, current_user, agent_id)
    if not result:
        raise NotFound("智能体不存在或无权限")
    return result
@router.delete("/{agent_id}",summary="删除智能体")
def delete_agent(
        agent_id:int,
        db:Session = Depends(get_db),
        current_user:User = Depends(get_current_user)
):
    result = agent_service.get_agent(db, current_user, agent_id)
    if result is None:
        raise NotFound("智能体不存在或无权限删除")
    return agent_service.delete(db, current_user, agent_id)
@router.post("/{agent_id}/select",summary="选中智能体")
def select_agent(
        agent_id :int,
        db:Session = Depends(get_db),
        current_user:User = Depends(get_current_user)
):
    result = agent_service.get_agent(db, current_user, agent_id)
    if result is None:
        raise NotFound("智能体不存在或无权限选中")
    return agent_service.select(
        db,current_user,agent_id
    )


# ============================================================================
# 企业接口连接器：给这个 Agent 配一个真实的企业 HTTP 接口，运行时当工具用。
# URL/认证/请求方式用户在这里配好，LLM 运行时只填参数——不能碰 URL 和认证信息。
# Step 0 安全收口：创建新连接器默认只留给管理员——单企业部署下"能不能接入外部系统"
# 应该是审核后统一配置的能力，不是员工自助接的；FEATURE_USER_API_CONNECTORS=true
# 时放开给普通用户自助配置。
# ============================================================================
from service import admin_service, feature_flags
from service.tools import http_connector_service


class ApiConnectorCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=1000)
    method: str = Field(default="GET")
    headers: Optional[dict] = None
    param_schema: Optional[dict] = None
    static_query: Optional[dict] = None


@router.post("/{agent_id}/api-connectors", summary="给 Agent 配一个企业接口工具（默认仅管理员）")
def create_api_connector(
        agent_id: int,
        data: ApiConnectorCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    if not admin_service.is_admin_user(current_user) and not feature_flags.user_api_connectors_enabled():
        raise PermissionDenied("创建企业接口连接器需要管理员权限，请联系管理员配置")
    if agent_service.get_agent(db, current_user, agent_id) is None:
        raise NotFound("智能体不存在或无权限")
    return http_connector_service.create_connector(
        db, current_user.id, agent_id,
        name=data.name, description=data.description, url=data.url,
        method=data.method, headers=data.headers,
        param_schema=data.param_schema, static_query=data.static_query,
    )


@router.get("/{agent_id}/api-connectors", summary="列出 Agent 配的企业接口工具")
def list_api_connectors(
        agent_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    if agent_service.get_agent(db, current_user, agent_id) is None:
        raise NotFound("智能体不存在或无权限")
    return http_connector_service.list_connectors(db, current_user.id, agent_id)


class ApiConnectorEnabledUpdate(BaseModel):
    is_enabled: bool


@router.patch("/api-connectors/{connector_id}", summary="启用/停用一个企业接口工具")
def update_api_connector_enabled(
        connector_id: int,
        data: ApiConnectorEnabledUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    return http_connector_service.set_connector_enabled(db, current_user.id, connector_id, data.is_enabled)


@router.delete("/api-connectors/{connector_id}", summary="删除一个企业接口工具")
def delete_api_connector(
        connector_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    http_connector_service.delete_connector(db, current_user.id, connector_id)
    return {"message": "删除成功"}
