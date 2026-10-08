"""知识库空间路由。

route -> service/knowledge_space/* -> models/knowledge_space*_dao.py
全部按当前登录用户隔离（get_owned_space[_async] / user_space_ids）。
空间 CRUD 走 async；空间内文档的上传/抓取/重建/启停/删除沿用 knowledge_service 同步 + 后台任务。
"""

import asyncio
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models.async_db import get_async_db
from models.init_db import User, get_db
from service.dependencies import get_current_user_async
from service.exceptions import AppError, NotFound
from service.knowledge_space import document_service, health_service, space_async_service
from service.web_crawler_service import CrawlerError
from service.web_crawler_async_service import async_crawl_url_to_markdown
from utils.rate_limit import LimitExceeded, require_limit
from utils.upload_limits import read_upload, read_uploads

router = APIRouter(prefix="/knowledge-spaces", tags=["知识库空间"])


def _limit_error(exc: LimitExceeded) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=exc.message,
        headers={"Retry-After": str(exc.retry_after)},
    )


# ============================ 空间 CRUD ============================

class SpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    purpose: Optional[str] = Field(default=None, max_length=60)
    tags: List[str] = Field(default_factory=list)


class SpaceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    purpose: Optional[str] = Field(default=None, max_length=60)
    tags: Optional[List[str]] = None
    is_enabled: Optional[bool] = None
    status: Optional[str] = Field(default=None, max_length=20)


@router.get("", summary="我的知识库空间列表 + 用途目录")
async def list_spaces_route(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await space_async_service.list_spaces(async_db, current_user.id)


@router.post("", summary="创建知识库空间")
async def create_space_route(
        data: SpaceCreate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await space_async_service.create_space(async_db, current_user.id, data.model_dump())


@router.get("/{space_id:int}", summary="知识库空间详情")
async def get_space_route(
        space_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await space_async_service.get_space(async_db, current_user.id, space_id)


@router.patch("/{space_id:int}", summary="修改知识库空间")
async def update_space_route(
        space_id: int,
        data: SpaceUpdate,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await space_async_service.update_space(
        async_db, current_user.id, space_id, data.model_dump(exclude_unset=True)
    )


@router.delete("/{space_id:int}", summary="删除知识库空间（空间下无文档时）")
async def delete_space_route(
        space_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await space_async_service.delete_space(async_db, current_user.id, space_id)


# ============================ 空间内文档 ============================

class DocMetaUpdate(BaseModel):
    category: Optional[str] = Field(default=None, max_length=60)
    tags: Optional[List[str]] = None
    version: Optional[str] = Field(default=None, max_length=40)
    is_enabled: Optional[bool] = None


class SpaceCrawlRequest(BaseModel):
    urls: List[str] = Field(min_length=1, max_length=10)


@router.get("/{space_id:int}/documents", summary="空间内文档列表（可按分类/标签/状态筛选）")
async def list_space_documents_route(
        space_id: int,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        doc_status: Optional[str] = None,
        enabled: Optional[bool] = None,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await document_service.list_documents(
        async_db, current_user.id, space_id,
        category=category, tag=tag, status=doc_status,
        is_enabled=None if enabled is None else (1 if enabled else 0),
    )


@router.post("/{space_id:int}/documents", summary="上传文档到空间")
async def upload_space_document_route(
        space_id: int,
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        category: Optional[str] = None,
        version: Optional[str] = None,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    _rate_limit_upload(current_user.id)
    content = await read_upload(file)
    try:
        return document_service.upload(
            db, background_tasks, current_user.id, space_id, file.filename or "", content,
            category=category, version=version,
        )
    except (HTTPException, AppError):
        raise
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"上传失败: {e}")


@router.post("/{space_id:int}/documents/batch", summary="批量上传文档到空间")
async def upload_space_documents_batch_route(
        space_id: int,
        background_tasks: BackgroundTasks,
        files: List[UploadFile] = File(...),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    _rate_limit_upload(current_user.id)
    contents = await read_uploads(files)          # 个数、每个文件、累计大小三个上限，超限立即 413
    prepared = [{"file_name": f.filename or "", "content": c} for f, c in zip(files, contents)]
    try:
        items = document_service.upload_batch(db, background_tasks, current_user.id, space_id, prepared)
        return {"message": f"已创建{len(items)}个入库任务", "count": len(items), "items": items}
    except (HTTPException, AppError):
        raise
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"批量上传失败: {e}")


@router.post("/{space_id:int}/documents/crawl", summary="抓取网页入库到空间")
async def crawl_space_documents_route(
        space_id: int,
        data: SpaceCrawlRequest,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    try:
        require_limit(
            key=f"knowledge_crawl:user:{current_user.id}",
            limit_env="KNOWLEDGE_CRAWL_RATE_LIMIT", default_limit=10,
            window_env="KNOWLEDGE_CRAWL_RATE_WINDOW_SECONDS", default_window=3600,
            label="网页抓取",
        )
    except LimitExceeded as e:
        raise _limit_error(e)

    urls = []
    for u in data.urls:
        u = (u or "").strip()
        if u and u not in urls:
            urls.append(u)

    async def _one(url: str):
        try:
            return {"ok": True, "url": url, "page": await async_crawl_url_to_markdown(url)}
        except (CrawlerError, ValueError) as e:
            return {"ok": False, "url": url, "error": str(e)}

    results = await asyncio.gather(*(_one(u) for u in urls))
    pages = [r["page"] for r in results if r["ok"]]
    failed = [{"url": r["url"], "error": r["error"]} for r in results if not r["ok"]]
    if not pages:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=failed[0]["error"] if failed else "没有网页被成功抓取")

    items = document_service.crawl(db, background_tasks, current_user.id, space_id, pages)
    return {
        "message": f"已抓取{len(items)}个网页并创建入库任务",
        "count": len(items), "items": items,
        "failed_count": len(failed), "failed_items": failed,
    }


@router.patch("/{space_id:int}/documents/{knowledge_id:int}", summary="改文档分类/标签/版本/启停")
async def update_space_document_route(
        space_id: int,
        knowledge_id: int,
        data: DocMetaUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    patch = data.model_dump(exclude_unset=True)
    if "is_enabled" in patch and patch["is_enabled"] is not None:
        document_service.set_enabled(db, current_user.id, space_id, knowledge_id, 1 if patch["is_enabled"] else 0)
    meta_keys = {k: patch[k] for k in ("category", "tags", "version") if k in patch}
    if meta_keys:
        return document_service.update_meta(db, current_user.id, space_id, knowledge_id, **meta_keys)
    return {"message": "更新成功", "knowledge_id": knowledge_id}


@router.post("/{space_id:int}/documents/{knowledge_id:int}/reindex", summary="重建文档索引")
async def reindex_space_document_route(
        space_id: int,
        knowledge_id: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    return document_service.reindex(db, background_tasks, current_user.id, space_id, knowledge_id)


@router.delete("/{space_id:int}/documents/{knowledge_id:int}", summary="删除文档")
async def delete_space_document_route(
        space_id: int,
        knowledge_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    return document_service.delete(db, current_user.id, space_id, knowledge_id)


@router.get("/{space_id:int}/health", summary="知识库空间健康分（实时算并回写）")
async def space_health_route(
        space_id: int,
        current_user: User = Depends(get_current_user_async),
):
    try:
        return await asyncio.to_thread(
            health_service.health_snapshot, current_user.id, space_id, persist=True
        )
    except PermissionError:
        raise NotFound("知识库空间不存在或无权限")


# ============================ 成员 / 审计（阶段6） ============================

class MemberSetRequest(BaseModel):
    user_name: str = Field(min_length=1, max_length=255)
    role: str = Field(pattern="^(admin|editor|viewer)$")


@router.get("/{space_id:int}/members", summary="空间成员列表")
async def list_space_members_route(
        space_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await space_async_service.list_members(async_db, current_user.id, space_id)


@router.put("/{space_id:int}/members", summary="添加 / 调整空间成员（owner/admin）")
async def set_space_member_route(
        space_id: int,
        data: MemberSetRequest,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await space_async_service.set_member(
        async_db, current_user.id, space_id, data.user_name, data.role
    )


@router.delete("/{space_id:int}/members/{member_user_id:int}", summary="移除空间成员（owner/admin）")
async def remove_space_member_route(
        space_id: int,
        member_user_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await space_async_service.remove_member(async_db, current_user.id, space_id, member_user_id)


@router.get("/{space_id:int}/audit", summary="空间操作日志（owner/admin）")
async def space_audit_route(
        space_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await space_async_service.list_audit(async_db, current_user.id, space_id)


def _rate_limit_upload(user_id: int) -> None:
    try:
        require_limit(
            key=f"knowledge_upload:user:{user_id}",
            limit_env="KNOWLEDGE_UPLOAD_RATE_LIMIT", default_limit=10,
            window_env="KNOWLEDGE_UPLOAD_RATE_WINDOW_SECONDS", default_window=3600,
            label="文档上传",
        )
    except LimitExceeded as e:
        raise _limit_error(e)
