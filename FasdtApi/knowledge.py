import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, status, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field
from models.init_db import get_db, User
from models.async_db import get_async_db
from service.dependencies import get_current_user_async
from service.exceptions import AppError, InvalidInput, NotFound
from service.rag.search_entry import search_scoped_async
from service.knowledge_space.space_service import ensure_default_space_for_agent
from service import knowledge_async_service, knowledge_diagnostics_async_service, knowledge_service
from service.access_control import get_owned_agent, get_owned_knowledge
from service.web_crawler_service import CrawlerError
from service.web_crawler_async_service import async_crawl_url_to_markdown
from service.rag.rag_service import SUPPORTED_FILE_TYPES
from utils.rate_limit import LimitExceeded, concurrency_guard, require_limit
from utils.upload_limits import read_upload, read_uploads

router = APIRouter(prefix="/knowledge", tags=["知识库管理"])

# 迁移边界：列表 / 文档详情 / 片段等纯读接口已全量 AsyncSession（get_async_db + *_async_service）；
# 检索走 search_entry.search_scoped_async（彻底 async 链路，自带 AsyncSessionLocal），处理器不持有任何 Session。
# 上传 / 入库 / 重建索引 / 诊断仍用同步 get_db —— 背后是向量库操作 + 同步 ORM 的 RAG 管线
#（切分 / embedding 落库 / ChromaDB），FastAPI 会把这些环节放线程池。
# 领域异常：本文件的 404/400 已统一为 service.exceptions（500 兜底与 429 限流保留 HTTPException）。
# 收口进度见 docs/sync-async-boundary.md。

# 允许的文件类型——单一事实来源是 rag_service.SUPPORTED_FILE_TYPES（解析器注册表），
# 不要在这里再维护一份独立列表，否则新增类型要改两处、容易漏改。
ALLOWED_TYPES = set(SUPPORTED_FILE_TYPES)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    knowledge_id: Optional[int] = None


class KnowledgeEnabledUpdate(BaseModel):
    is_enabled: int = Field(ge=0, le=1)


class KnowledgeCrawlRequest(BaseModel):
    urls: List[str] = Field(min_length=1, max_length=10)


def _limit_error(exc: LimitExceeded) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=exc.message,
        headers={"Retry-After": str(exc.retry_after)},
    )


def _ensure_agent_owner(db: Session, user_id: int, agent_id: int):
    agent = get_owned_agent(db, user_id, agent_id)
    if not agent:
        raise NotFound("智能体不存在或无权限")
    return agent


def _validate_upload_file_name(file_name: str) -> str:
    file_type = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if file_type not in ALLOWED_TYPES:
        raise InvalidInput(f"不支持的文件类型: {file_type}，支持: {list(ALLOWED_TYPES)}")
    return file_type


@router.get("/my/list", summary="查看我的全部资料")
async def list_my_documents(
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    return await knowledge_async_service.list_my_documents(async_db, current_user.id)


@router.post("/crawl/check", summary="检测网页地址是否允许抓取")
async def check_crawl_targets(
        data: KnowledgeCrawlRequest,
        current_user: User = Depends(get_current_user_async),
):
    return await knowledge_diagnostics_async_service.async_check_crawl_urls(data.urls)


@router.get("/{agent_id}/diagnostics", summary="查看知识库诊断信息")
async def knowledge_diagnostics(
        agent_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    _ensure_agent_owner(db, current_user.id, agent_id)
    return await knowledge_diagnostics_async_service.async_get_knowledge_diagnostics(db, current_user.id, agent_id)


@router.post("/{agent_id}/upload", summary="上传文档并入库")
async def upload_document(
        agent_id: int,
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        chunk_size: Optional[int] = Form(None),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    _ensure_agent_owner(db, current_user.id, agent_id)
    try:
        require_limit(
            key=f"knowledge_upload:user:{current_user.id}",
            limit_env="KNOWLEDGE_UPLOAD_RATE_LIMIT",
            default_limit=10,
            window_env="KNOWLEDGE_UPLOAD_RATE_WINDOW_SECONDS",
            default_window=3600,
            label="文档上传",
        )
    except LimitExceeded as e:
        raise _limit_error(e)

    # 1. 校验文件类型
    file_name = file.filename
    file_type = _validate_upload_file_name(file_name)

    # 2. 读取文件内容（分块读，超过单文件上限立即 413，不会无上限读进内存）
    content = await read_upload(file)

    # 3. 调用RAG服务上传入库
    try:
        _sid = ensure_default_space_for_agent(db, current_user.id, agent_id)
        item = knowledge_service.create_upload_task(
            db, background_tasks, current_user.id, agent_id, file_name, content, file_type,
            space_id=_sid, chunk_size=chunk_size,
        )
        return {
            "message": "已创建后台入库任务",
            "knowledge_id": item["knowledge_id"],
            "task_id": item["task_id"],
            "status": item["status"],
        }
    except ValueError as e:
        db.rollback()
        raise InvalidInput(str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"上传失败: {str(e)}")


@router.post("/{agent_id}/upload-batch", summary="批量上传文档并入库")
async def upload_documents(
        agent_id: int,
        background_tasks: BackgroundTasks,
        files: List[UploadFile] = File(...),
        chunk_size: Optional[int] = Form(None),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    _ensure_agent_owner(db, current_user.id, agent_id)
    try:
        require_limit(
            key=f"knowledge_upload:user:{current_user.id}",
            limit_env="KNOWLEDGE_UPLOAD_RATE_LIMIT",
            default_limit=10,
            window_env="KNOWLEDGE_UPLOAD_RATE_WINDOW_SECONDS",
            default_window=3600,
            label="文档上传",
        )
    except LimitExceeded as e:
        raise _limit_error(e)
    if not files:
        raise InvalidInput("请选择至少一个文件")
    if len(files) > 20:
        raise InvalidInput("一次最多上传20个文件")

    created = []
    try:
        names_and_types = []
        for file in files:                      # 先把所有文件名都校验完，再开始读内容：有一个不合法就一个字节都不读
            names_and_types.append((file.filename, _validate_upload_file_name(file.filename)))
        contents = await read_uploads(files)    # 每个文件、累计大小、个数三个上限
        prepared_files = [{"file_name": name, "file_type": ftype, "content": content}
                          for (name, ftype), content in zip(names_and_types, contents)]
        _sid = ensure_default_space_for_agent(db, current_user.id, agent_id)
        created = knowledge_service.create_upload_tasks(
            db, background_tasks, current_user.id, agent_id, prepared_files,
            space_id=_sid, chunk_size=chunk_size,
        )
        return {
            "message": f"已创建{len(created)}个后台入库任务",
            "count": len(created),
            "items": created,
        }
    except (HTTPException, AppError):      # 用户输入的问题（文件类型、大小）原样返回，不能被下面的兜底变成 500 / 问题中心里的假故障
        db.rollback()
        raise
    except ValueError as e:
        db.rollback()
        raise InvalidInput(str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"批量上传失败: {str(e)}")


@router.post("/{agent_id}/crawl", summary="抓取网页并入库")
async def crawl_documents(
        agent_id: int,
        data: KnowledgeCrawlRequest,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    _ensure_agent_owner(db, current_user.id, agent_id)
    try:
        require_limit(
            key=f"knowledge_crawl:user:{current_user.id}",
            limit_env="KNOWLEDGE_CRAWL_RATE_LIMIT",
            default_limit=10,
            window_env="KNOWLEDGE_CRAWL_RATE_WINDOW_SECONDS",
            default_window=3600,
            label="网页抓取",
        )
    except LimitExceeded as e:
        raise _limit_error(e)

    unique_urls = []
    for url in data.urls:
        item = (url or "").strip()
        if item and item not in unique_urls:
            unique_urls.append(item)
    if not unique_urls:
        raise InvalidInput("请填写至少一个 URL")

    created = []
    failed = []
    try:
        async def crawl_one(url: str):
            try:
                page = await async_crawl_url_to_markdown(url)
                return {"ok": True, "url": url, "page": page}
            except (CrawlerError, ValueError) as e:
                return {"ok": False, "url": url, "error": str(e)}

        crawl_results = await asyncio.gather(*(crawl_one(url) for url in unique_urls))
        pages = []
        for item in crawl_results:
            if item["ok"]:
                page = item["page"]
                pages.append(page)
            else:
                failed.append({"url": item["url"], "error": item["error"]})

        if not pages:
            db.rollback()
            first_error = failed[0]["error"] if failed else "没有网页被成功抓取"
            raise InvalidInput(first_error)

        _sid = ensure_default_space_for_agent(db, current_user.id, agent_id)
        created = knowledge_service.create_crawl_tasks(
            db, background_tasks, current_user.id, agent_id, pages, space_id=_sid
        )
        return {
            "message": f"已抓取{len(created)}个网页并创建入库任务",
            "count": len(created),
            "items": created,
            "failed_count": len(failed),
            "failed_items": failed,
        }
    except CrawlerError as e:
        db.rollback()
        raise InvalidInput(str(e))
    except ValueError as e:
        db.rollback()
        raise InvalidInput(str(e))
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"网页抓取失败: {str(e)}")


@router.get("/{agent_id}/list", summary="查看知识库文档列表")
async def list_documents(
        agent_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    result = await knowledge_async_service.list_owned_documents(async_db, current_user.id, agent_id)
    if result is None:
        raise NotFound("智能体不存在或无权限")
    return result


@router.post("/{agent_id}/import/{knowledge_id}", summary="把我的已有资料导入当前智能体")
async def import_document_to_agent(
        agent_id: int,
        knowledge_id: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    _ensure_agent_owner(db, current_user.id, agent_id)
    source = get_owned_knowledge(db, current_user.id, knowledge_id)
    if not source:
        raise NotFound("资料不存在或无权限")
    if source.agent_id == agent_id:
        raise InvalidInput("该资料已经属于当前助手")
    try:
        return knowledge_service.import_existing_document(
            db, background_tasks, current_user.id, agent_id, source
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"导入资料失败: {str(e)}")


@router.get("/{agent_id}/{knowledge_id}", summary="查看知识库文档详情")
async def get_document(
        agent_id: int,
        knowledge_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    result = await knowledge_async_service.get_document(async_db, current_user.id, agent_id, knowledge_id)
    if not result:
        raise NotFound("文档不存在或无权限")
    return result


@router.get("/{agent_id}/{knowledge_id}/chunks", summary="查看文档切分片段")
async def list_document_chunks(
        agent_id: int,
        knowledge_id: int,
        async_db=Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
):
    result = await knowledge_async_service.list_document_chunks(
        async_db, current_user.id, agent_id, knowledge_id
    )
    if result is None:
        raise NotFound("文档不存在或无权限")
    return result


@router.post("/{agent_id}/search", summary="检索知识库")
async def search_knowledge(
        agent_id: int,
        data: KnowledgeSearchRequest,
        current_user: User = Depends(get_current_user_async),
):
    # 归属校验 + 文档启用检查 + 检索本体都在 search_entry.search_scoped_async 里（彻底 async RAG 链路：
    # 向量化 async + chunk 反查 async DAO，ChromaDB / rerank 封 to_thread）。本处理器不持有任何 Session。
    query = data.query.strip()
    if not query:
        raise InvalidInput("检索关键词不能为空")
    try:
        require_limit(
            key=f"knowledge_search:user:{current_user.id}",
            limit_env="KNOWLEDGE_SEARCH_RATE_LIMIT",
            default_limit=30,
            window_env="KNOWLEDGE_SEARCH_RATE_WINDOW_SECONDS",
            default_window=60,
            label="知识库检索",
        )
    except LimitExceeded as e:
        raise _limit_error(e)
    try:
        with concurrency_guard(
            key=f"knowledge_search:user:{current_user.id}",
            limit_env="USER_MAX_CONCURRENT_KNOWLEDGE_SEARCHES",
            default_limit=3,
            ttl_env="KNOWLEDGE_SEARCH_CONCURRENCY_TTL_SECONDS",
            default_ttl=120,
            label="知识库检索",
        ):
            results = await search_scoped_async(
                current_user.id, agent_id, query,
                top_k=data.top_k, knowledge_id=data.knowledge_id,
            )
        return {"query": query, "count": len(results), "top_k": data.top_k, "results": results}
    except LimitExceeded as e:
        raise _limit_error(e)
    except PermissionError as e:
        raise NotFound(str(e))
    except ValueError as e:
        raise InvalidInput(str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"检索失败: {str(e)}")


@router.patch("/{agent_id}/{knowledge_id}/enabled", summary="启用或禁用知识库文档")
async def update_document_enabled(
        agent_id: int,
        knowledge_id: int,
        data: KnowledgeEnabledUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    _ensure_agent_owner(db, current_user.id, agent_id)
    doc = get_owned_knowledge(db, current_user.id, knowledge_id, agent_id=agent_id)
    if not doc:
        raise NotFound("文档不存在或无权限")
    return knowledge_service.set_document_enabled(db, doc, data.is_enabled)


class ReindexOptions(BaseModel):
    # 传了（含 null）就更新文档的切块大小再重建；整个 body 省略则沿用现有设置
    chunk_size: Optional[int] = Field(default=None, ge=0, le=4000)


@router.post("/{agent_id}/{knowledge_id}/reindex", summary="重新入库/重建索引")
async def reindex_document(
        agent_id: int,
        knowledge_id: int,
        background_tasks: BackgroundTasks,
        options: Optional[ReindexOptions] = None,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    _ensure_agent_owner(db, current_user.id, agent_id)
    doc = get_owned_knowledge(db, current_user.id, knowledge_id, agent_id=agent_id)
    if not doc:
        raise NotFound("文档不存在或无权限")
    try:
        return knowledge_service.create_reindex_task(
            db,
            background_tasks,
            current_user.id,
            agent_id,
            knowledge_id,
            doc.file_name,
            chunk_size=(options.chunk_size if options is not None else "__keep__"),
        )
    except ValueError as e:
        db.rollback()
        raise InvalidInput(str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"重新入库失败: {str(e)}")


@router.delete("/{agent_id}/{knowledge_id}", summary="删除知识库文档")
async def delete_document(
        agent_id: int,
        knowledge_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user_async),
):
    _ensure_agent_owner(db, current_user.id, agent_id)
    knowledge = get_owned_knowledge(db, current_user.id, knowledge_id, agent_id=agent_id)
    if not knowledge:
        raise NotFound("文档不存在或无权限")

    try:
        return knowledge_service.delete_document_completely(db, agent_id, knowledge_id)
    except Exception as e:
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"删除失败: {str(e)}")
