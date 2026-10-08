from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from service.exceptions import InvalidInput
from pydantic import BaseModel,Field
from service import auth_async_service, session_cookie
from service.auth import ACCESS_TOKEN_EXPIRE_MINUTES
from service.password_policy import MAX_LENGTH as PW_MAX_LENGTH, MIN_LENGTH as PW_MIN_LENGTH
from service.phone_verification_service import normalize_phone
from service.phone_verification_async_service import async_send_register_code, async_send_verification_code
from models.async_db import get_async_db
from models.user_async_dao import get_user_by_phone_async
from service.dependencies import get_current_user_async
from models.init_db import User
from service.admin_service import current_user_payload
from utils.rate_limit import LimitExceeded, require_limit
from service.user_dashboard_async_service import get_user_dashboard as get_user_dashboard_async
from service.quota_service import get_quota_status_async, list_monthly_runs_async
from service.user_workspace_async_service import (
    apply_workspace_command,
    get_user_workspace as get_user_workspace_async,
    save_user_workspace as save_user_workspace_async,
)
from service.user_profile_async_service import (
    get_user_profile_payload as get_user_profile_payload_async,
    save_user_profile as save_user_profile_async,
)
router = APIRouter(prefix="/user", tags=["用户功能"])

class LoginUser(BaseModel):
    name: str = Field(min_length=3, max_length=20)
    password:str= Field(min_length=6)
class RegisterUser(BaseModel):
    name: str = Field(min_length=3, max_length=20)
    # 长度只是形状检查，能不能用（不等于用户名/手机号、不是常见弱密码）由 service.password_policy 判断
    password: str = Field(min_length=PW_MIN_LENGTH, max_length=PW_MAX_LENGTH)
    age: int = Field(ge=0, le=150)
    phone: str = Field(min_length=11, max_length=20)
    sms_code: str = Field(min_length=6, max_length=6)
    accepted_terms: bool = Field(default=False)

class SendRegisterCodeRequest(BaseModel):
    phone: str = Field(min_length=11, max_length=20)

class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=PW_MIN_LENGTH, max_length=PW_MAX_LENGTH)


class ResetPasswordRequest(BaseModel):
    phone: str = Field(min_length=11, max_length=20)
    sms_code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=PW_MIN_LENGTH, max_length=PW_MAX_LENGTH)


def _limit_error(exc: LimitExceeded) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=exc.message,
        headers={"Retry-After": str(exc.retry_after)},
    )


class UserProfileRequest(BaseModel):
    occupation: str = Field(default="", max_length=100)
    skills: str = Field(default="", max_length=1000)
    preferences: str = Field(default="", max_length=1000)
    communication_style: str = Field(default="balanced", max_length=50)
    persona: str = Field(default="professional", max_length=50)
    extra_info: str = Field(default="", max_length=1000)


class WorkspaceWidgetRequest(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=80)
    title: str = Field(default="自定义小窗口", max_length=80)
    enabled: bool = True
    size: str = Field(default="wide", max_length=20)
    settings: Dict[str, Any] = Field(default_factory=dict)


class WorkspaceRequest(BaseModel):
    modules: List[str] = Field(default_factory=list)
    widgets: List[WorkspaceWidgetRequest] = Field(default_factory=list)
    layout: Dict[str, Any] = Field(default_factory=dict)


class WorkspaceCommandRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
#登录
@router.post("/login",summary="用户登录")
async def login(
        user: LoginUser,
        request: Request,
        response: Response,
        async_db=Depends(get_async_db),
):
    client_ip = request.client.host if request.client else "unknown"
    try:
        # 按 IP 限：防止单一来源脚本化撞库；按用户名限：防止分布式撞同一个账号。
        require_limit(
            key=f"login:ip:{client_ip}",
            limit_env="LOGIN_IP_RATE_LIMIT",
            default_limit=20,
            window_env="LOGIN_RATE_WINDOW_SECONDS",
            default_window=300,
            label="登录",
        )
        require_limit(
            key=f"login:user:{user.name.strip().lower()}",
            limit_env="LOGIN_USER_RATE_LIMIT",
            default_limit=8,
            window_env="LOGIN_RATE_WINDOW_SECONDS",
            default_window=300,
            label="登录",
        )
    except LimitExceeded as e:
        raise _limit_error(e)
    result = await auth_async_service.login(async_db, user.name, user.password)
    # 浏览器前端靠这个 HttpOnly Cookie 保持登录（JavaScript 读不到令牌）；响应体里仍带 access_token，给脚本 / 集成用
    session_cookie.issue(response, result["access_token"], ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    return result


@router.post("/logout", summary="退出登录（清除浏览器里的登录 Cookie）")
async def logout(response: Response):
    """不需要登录态：只是让浏览器丢掉两个 Cookie。已经签发的令牌要让它立即失效，用 /user/logout-all。"""
    session_cookie.clear(response)
    return {"message": "已退出登录"}
#注册
@router.post("/register",summary="用户注册")
async def register(
        user: RegisterUser,
        request: Request,
        async_db=Depends(get_async_db),
):
    client_ip = request.client.host if request.client else "unknown"
    try:
        require_limit(
            key=f"register:ip:{client_ip}",
            limit_env="REGISTER_RATE_LIMIT",
            default_limit=10,
            window_env="REGISTER_RATE_WINDOW_SECONDS",
            default_window=3600,
            label="注册",
        )
    except LimitExceeded as e:
        raise _limit_error(e)
    return await auth_async_service.register(
        async_db,
        user.name,
        user.password,
        user.age,
        user.phone,
        user.sms_code,
        user.accepted_terms,
    )

@router.post("/register/sms-code",summary="发送注册手机验证码")
async def send_register_sms_code(
        data: SendRegisterCodeRequest,
        request: Request,
        async_db=Depends(get_async_db),
):
    phone = normalize_phone(data.phone)
    client_ip = request.client.host if request.client else ""
    # 查库之前先限流：否则对"已注册"的号码可以无限次探测，一次短信都不会发也就不会被发送环节的限制拦住。
    try:
        require_limit(
            key=f"sms:register-probe:ip:{client_ip or 'unknown'}",
            limit_env="SMS_CODE_IP_LIMIT",
            default_limit=20,
            window_env="SMS_CODE_IP_WINDOW_SECONDS",
            default_window=3600,
            label="验证码发送",
        )
    except LimitExceeded as e:
        raise _limit_error(e)
    existing = await get_user_by_phone_async(async_db, phone)
    if existing:
        raise InvalidInput("手机号已经注册")
    return await async_send_register_code(phone, client_ip)


@router.post("/reset-password/sms-code", summary="发送找回密码手机验证码")
async def send_reset_password_sms_code(
        data: SendRegisterCodeRequest,
        request: Request,
        async_db=Depends(get_async_db),
):
    phone = normalize_phone(data.phone)
    client_ip = request.client.host if request.client else "unknown"
    try:
        require_limit(
            key=f"sms:reset-probe:ip:{client_ip}",
            limit_env="SMS_CODE_IP_LIMIT",
            default_limit=20,
            window_env="SMS_CODE_IP_WINDOW_SECONDS",
            default_window=3600,
            label="验证码发送",
        )
    except LimitExceeded as e:
        raise _limit_error(e)
    # 无论手机号是否已注册都返回同样的成功响应，避免把"是否存在该账号"暴露给调用方；
    # 未注册手机号不会真的发送验证码，只是走同一套返回结构。
    existing = await get_user_by_phone_async(async_db, phone)
    if not existing:
        return {
            "message": "验证码已发送",
            "phone": phone,
            "expires_in": 300,
            "retry_after": 60,
            "provider": "console",
            "dev_code": None,
        }
    return await async_send_verification_code(phone, client_ip, scene="reset")


@router.post("/reset-password", summary="通过手机验证码重置密码")
async def reset_password(
        data: ResetPasswordRequest,
        request: Request,
        async_db=Depends(get_async_db),
):
    client_ip = request.client.host if request.client else "unknown"
    try:
        require_limit(
            key=f"reset-password:ip:{client_ip}",
            limit_env="RESET_PASSWORD_RATE_LIMIT",
            default_limit=10,
            window_env="RESET_PASSWORD_RATE_WINDOW_SECONDS",
            default_window=3600,
            label="重置密码",
        )
    except LimitExceeded as e:
        raise _limit_error(e)
    return await auth_async_service.reset_password_with_phone(
        async_db, data.phone, data.sms_code, data.new_password,
    )
@router.get("/me", summary="查询当前登录用户信息")
async def get_me(current_user: User = Depends(get_current_user_async)):
    return current_user_payload(current_user)


@router.get("/dashboard", summary="查询当前用户工作台概览")
async def get_dashboard(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await get_user_dashboard_async(async_db, current_user.id)


@router.get("/quota", summary="查询当前用户的套餐与本月配额用量")
async def get_quota(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await get_quota_status_async(async_db, current_user.id)


@router.get("/usage/export", summary="导出本月用量报表（CSV）")
async def export_usage(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    from utils.csv_export import csv_response, rows_to_csv
    from utils.timeutil import utcnow

    quota = await get_quota_status_async(async_db, current_user.id)
    runs = await list_monthly_runs_async(async_db, current_user.id)
    lines = [
        f"本月用量报表，导出于 {utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC",
        "",
        "套餐概况",
        rows_to_csv(
            ["套餐", "本月已用Token", "月度配额", "是否不限量"],
            [[quota["plan_display_name"], quota["used_tokens"],
              quota["monthly_token_limit"] if not quota["unlimited"] else "-",
              "是" if quota["unlimited"] else "否"]],
        ),
        "",
        "本月运行明细",
        rows_to_csv(
            ["时间", "助手", "状态", "步数", "Token消耗"],
            [[r["started_at"], r["agent_name"], r["status"], r["total_steps"], r["total_tokens"]] for r in runs],
        ),
    ]
    filename = f"my_usage_report_{utcnow().strftime('%Y%m%d')}.csv"
    return csv_response(filename, "\n".join(lines))


@router.get("/workspace", summary="读取当前用户工作台配置")
async def get_workspace(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await get_user_workspace_async(async_db, current_user.id)


@router.put("/workspace", summary="保存当前用户工作台配置")
async def update_workspace(
        data: WorkspaceRequest,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await save_user_workspace_async(async_db, current_user.id, data.model_dump())


@router.post("/workspace/command", summary="通过对话调整当前用户工作台")
async def command_workspace(
        data: WorkspaceCommandRequest,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await apply_workspace_command(async_db, current_user.id, data.prompt)


@router.get("/profile", summary="查询当前用户画像")
async def get_profile(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await get_user_profile_payload_async(async_db, current_user.id)


@router.put("/profile", summary="保存当前用户画像")
async def update_profile(
        data: UserProfileRequest,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await save_user_profile_async(async_db, current_user.id, data.model_dump())

@router.post("/change-password", summary="修改当前用户密码")
async def change_password(
        data: ChangePasswordRequest,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    result = await auth_async_service.change_password(
        async_db,
        current_user,
        data.old_password,
        data.new_password,
    )
    if result["message"] != "修改成功":
        raise InvalidInput(result["message"])
    return result


@router.post("/logout-all", summary="退出所有设备")
async def logout_all_devices(
        response: Response,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    """让当前用户已签发的所有 token（含这一台设备正在用的这个）立即失效，下次访问都要重新登录。

    普通"退出登录"不用这个接口，前端直接丢弃本地 token 就够了；这个接口是给"我怀疑账号在别的
    设备上也登录着"这种场景用的。
    """
    await auth_async_service.logout_all_devices(async_db, current_user.id)
    session_cookie.clear(response)
    return {"message": "已退出所有设备，请重新登录"}
