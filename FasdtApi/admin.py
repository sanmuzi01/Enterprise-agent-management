from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from service.exceptions import NotFound
from pydantic import BaseModel, Field

from models.init_db import User
from models.async_db import get_async_db
from service import admin_async_service, admin_service
from service.dependencies import get_current_admin_user_async
from service.password_policy import MAX_LENGTH as PW_MAX_LENGTH, MIN_LENGTH as PW_MIN_LENGTH
from service import operation_log_async_service
from utils.csv_export import csv_response, rows_to_csv
from utils.timeutil import utcnow

router = APIRouter(prefix="/admin", tags=["管理员后台"])


class UserRolesUpdate(BaseModel):
    roles: List[str] = Field(default_factory=list)


class UserStatusUpdate(BaseModel):
    disabled: bool


class UserPasswordUpdate(BaseModel):
    # 长度只是形状检查，能不能用由 service.password_policy 判断
    new_password: str = Field(min_length=PW_MIN_LENGTH, max_length=PW_MAX_LENGTH)


@router.get("/me", summary="查询当前管理员信息")
async def admin_me(current_user: User = Depends(get_current_admin_user_async)):
    return admin_service.current_user_payload(current_user)


@router.get("/overview", summary="后台总览统计")
async def admin_overview(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await admin_async_service.overview(async_db)


@router.get("/users", summary="用户管控列表")
async def admin_users(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        search: str = Query(default=None, description="按用户名/手机号模糊搜索"),
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await admin_async_service.list_users(async_db, limit=limit, offset=offset, search=search)


@router.get("/users/{user_id}", summary="查询用户详情")
async def admin_user_detail(
        user_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    result = await admin_async_service.get_user_detail(async_db, user_id)
    if not result:
        raise NotFound("用户不存在")
    return result


@router.put("/users/{user_id}/roles", summary="更新用户角色")
async def admin_update_user_roles(
        user_id: int,
        data: UserRolesUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    result = await admin_async_service.set_user_roles(async_db, user_id, data.roles, current_user.id)
    if not result:
        raise NotFound("用户不存在")
    return result


@router.patch("/users/{user_id}/status", summary="启用或禁用用户")
async def admin_update_user_status(
        user_id: int,
        data: UserStatusUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    result = await admin_async_service.set_user_disabled(async_db, user_id, data.disabled, current_user.id)
    if not result:
        raise NotFound("用户不存在")
    return result


@router.put("/users/{user_id}/password", summary="重置用户密码")
async def admin_reset_user_password(
        user_id: int,
        data: UserPasswordUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    result = await admin_async_service.reset_user_password(async_db, user_id, data.new_password)
    if not result:
        raise NotFound("用户不存在")
    return result


@router.post("/users/{user_id}/revoke-sessions", summary="强制下线：让该用户已签发的所有 token 立即失效")
async def admin_revoke_user_sessions(
        user_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    """不改密码，只让该用户当前所有已登录的设备立即失效，用户下次请求会收到 401 要求重新登录。

    典型场景：怀疑某个账号的 token 泄露、员工离职当天要立刻收权限——不用等 token 自然过期。
    """
    ok = await admin_async_service.force_logout_user(async_db, user_id)
    if not ok:
        raise NotFound("用户不存在")
    return {"message": "已强制下线，该用户所有已登录设备下次请求都需要重新登录"}


@router.delete("/users/{user_id}", summary="删除用户")
async def admin_delete_user(
        user_id: int,
        current_user: User = Depends(get_current_admin_user_async),
):
    result = await admin_async_service.delete_user(user_id, current_user.id)
    if not result:
        raise NotFound("用户不存在")
    return result


@router.get("/tasks", summary="查询全局后台任务")
async def admin_tasks(
        limit: int = Query(default=50, ge=1, le=200),
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await admin_async_service.list_recent_tasks(async_db, limit=limit)


@router.get("/usage", summary="查询系统使用情况")
async def admin_usage(
        days: int = Query(default=14, ge=1, le=90),
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await admin_async_service.usage_stats(async_db, days=days)


@router.get("/usage/export", summary="导出使用情况报表（CSV）")
async def admin_export_usage(
        days: int = Query(default=14, ge=1, le=90),
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    data = await admin_async_service.usage_stats(async_db, days=days)
    lines = [
        f"用量报表，最近 {days} 天，导出于 {utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC",
        "",
        "汇总",
        rows_to_csv(
            ["总运行数", "成功数", "失败数", "成功率(%)", "总Token", "总消息数"],
            [[data["summary"]["total_runs"], data["summary"]["finished_runs"],
              data["summary"]["failed_runs"], data["summary"]["success_rate"],
              data["summary"]["total_tokens"], data["summary"]["total_messages"]]],
        ),
        "",
        "每日明细",
        rows_to_csv(
            ["日期", "运行数", "Token消耗", "消息数"],
            [[d["date"], d["runs"], d["tokens"], d["messages"]] for d in data["daily"]],
        ),
        "",
        "Top用户",
        rows_to_csv(
            ["用户ID", "用户名", "运行数", "Token消耗"],
            [[u["user_id"], u["name"], u["run_count"], u["tokens"]] for u in data["top_users"]],
        ),
    ]
    filename = f"usage_report_{utcnow().strftime('%Y%m%d')}.csv"
    return csv_response(filename, "\n".join(lines))


@router.get("/logs/export", summary="导出操作日志（CSV，最多导出当前筛选条件下最近 500 条）")
async def admin_export_logs(
        days: int = Query(default=7, ge=1, le=90),
        keyword: str = Query(default=None),
        method: str = Query(default=None),
        status_group: str = Query(default=None),
        user_id: int = Query(default=None),
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    data = await operation_log_async_service.list_operation_logs(
        async_db, limit=500, offset=0, keyword=keyword, method=method,
        status_group=status_group, user_id=user_id, days=days,
    )
    content = rows_to_csv(
        ["ID", "用户", "方法", "路径", "状态码", "耗时(ms)", "客户端IP", "错误信息", "时间"],
        [[
            item["id"], item["username"] or item["user_id"] or "", item["method"], item["path"],
            item["status_code"], item["latency_ms"], item["client_ip"] or "",
            (item["error_msg"] or "")[:200], item["created_at"],
        ] for item in data["items"]],
    )
    filename = f"operation_logs_{utcnow().strftime('%Y%m%d')}.csv"
    return csv_response(filename, content)


@router.get("/knowledge-spaces", summary="企业知识库空间总览（可按部门筛选）")
async def admin_knowledge_spaces(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        scope: str | None = Query(default=None, description="all / unassigned / enterprise / team:<部门编号>"),
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await admin_async_service.list_knowledge_spaces(async_db, limit=limit, offset=offset, scope=scope)


class AdminSpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    purpose: str | None = Field(default=None, max_length=60)
    scope: str = Field(default="unassigned", description="unassigned 先不划分 / departments 划分给指定部门 / enterprise 全企业")
    team_ids: list[int] = Field(default_factory=list, description="scope=departments 时的部门编号（可多个）")
    sensitivity: str | None = Field(default=None, max_length=20)


@router.post("/knowledge-spaces", summary="管理员统一创建知识库空间（可顺便划分给部门 / 全企业）")
async def admin_create_knowledge_space(
        data: AdminSpaceCreate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await admin_async_service.admin_create_space(async_db, current_user.id, data.model_dump())


class AdminSpaceStatusUpdate(BaseModel):
    is_enabled: bool | None = None
    status: str | None = None
    scope: str | None = Field(default=None, description="划分方式：unassigned / departments / enterprise")
    team_ids: list[int] = Field(default_factory=list, description="scope=departments 时的部门编号（可多个）")
    sensitivity: str | None = Field(default=None, max_length=20)


@router.patch("/knowledge-spaces/{space_id}", summary="管理员直接改一个空间的启停/归档状态")
async def admin_update_knowledge_space(
        space_id: int,
        data: AdminSpaceStatusUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await admin_async_service.admin_update_space(
        async_db, current_user.id, space_id, data.model_dump(exclude_unset=True),
    )


class PlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    display_name: str = Field(min_length=1, max_length=100)
    monthly_token_limit: int = Field(default=0, ge=0)
    price_desc: Optional[str] = None
    is_default: bool = False


class PlanUpdate(BaseModel):
    display_name: Optional[str] = None
    monthly_token_limit: Optional[int] = Field(default=None, ge=0)
    price_desc: Optional[str] = None
    is_default: Optional[bool] = None
    is_enabled: Optional[bool] = None


class UserPlanAssign(BaseModel):
    plan_id: int


@router.get("/plans", summary="查询套餐列表")
async def admin_list_plans(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await admin_async_service.list_plans(async_db)


@router.post("/plans", summary="新建套餐")
async def admin_create_plan(
        data: PlanCreate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await admin_async_service.create_plan(async_db, current_user.id, data.model_dump())


@router.patch("/plans/{plan_id}", summary="更新套餐")
async def admin_update_plan(
        plan_id: int,
        data: PlanUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await admin_async_service.update_plan(async_db, current_user.id, plan_id, data.model_dump(exclude_unset=True))


@router.delete("/plans/{plan_id}", summary="删除套餐")
async def admin_delete_plan(
        plan_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await admin_async_service.delete_plan(async_db, current_user.id, plan_id)


@router.patch("/users/{user_id}/plan", summary="给用户分配套餐")
async def admin_assign_user_plan(
        user_id: int,
        data: UserPlanAssign,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await admin_async_service.assign_user_plan(async_db, current_user.id, user_id, data.plan_id)


@router.get("/logs", summary="查询操作日志")
async def admin_logs(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        days: int = Query(default=7, ge=1, le=90),
        keyword: str = Query(default=None),
        method: str = Query(default=None),
        status_group: str = Query(default=None),
        user_id: int = Query(default=None),
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_admin_user_async),
):
    return await operation_log_async_service.list_operation_logs(
        async_db,
        limit=limit,
        offset=offset,
        days=days,
        keyword=keyword,
        method=method,
        status_group=status_group,
        user_id=user_id,
    )
