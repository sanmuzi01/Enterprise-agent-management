from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from models.async_db import get_async_db
from models.init_db import User
from service.dependencies import get_current_user_async
from service.user_workspace_async_service import apply_workspace_command, get_user_workspace, save_user_workspace

router = APIRouter(prefix="/user/workspace", tags=["用户工作台"])


class WorkspaceWidget(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=80)
    title: str = Field(default="自定义小窗口", max_length=80)
    enabled: bool = True
    size: str = Field(default="wide", max_length=20)
    settings: Dict[str, Any] = Field(default_factory=dict)


class WorkspaceRequest(BaseModel):
    modules: List[str] = Field(default_factory=list)
    widgets: List[WorkspaceWidget] = Field(default_factory=list)
    layout: Dict[str, Any] = Field(default_factory=dict)


class WorkspaceCommandRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)


@router.get("", summary="读取当前用户工作台配置")
async def get_workspace(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await get_user_workspace(async_db, current_user.id)


@router.put("", summary="保存当前用户工作台配置")
async def update_workspace(
        data: WorkspaceRequest,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await save_user_workspace(async_db, current_user.id, data.model_dump())


@router.post("/command", summary="通过对话调整当前用户工作台")
async def command_workspace(
        data: WorkspaceCommandRequest,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await apply_workspace_command(async_db, current_user.id, data.prompt)
