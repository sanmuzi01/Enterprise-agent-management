"""知识库空间内的文档管理编排。

读（列表 / facet）走 async；写（上传 / 抓取 / 重建 / 启停 / 删除 / 改元数据）沿用
knowledge_service 的同步 + 后台任务链路。归属：get_owned_space[_async]。
"""

import json
from typing import Any, Dict, List, Optional

from service.exceptions import InvalidInput, NotFound, PermissionDenied
from service.rag.rag_service import SUPPORTED_FILE_TYPES

# 单一事实来源是 rag_service.SUPPORTED_FILE_TYPES（解析器注册表），不要在这里
# 再维护一份独立列表，否则新增类型要改两处、容易漏改。
ALLOWED_TYPES = set(SUPPORTED_FILE_TYPES)
_STATUS_LABEL = {"pending": "待处理", "processing": "解析中", "done": "已入库", "failed": "失败"}


def _doc_dict(k) -> Dict[str, Any]:
    try:
        tags = json.loads(k.tags_json) if k.tags_json else []
    except (TypeError, ValueError):
        tags = []
    return {
        "id": k.id,
        "file_name": k.file_name,
        "file_type": k.file_type,
        "file_size": k.file_size,
        "chunk_count": k.chunk_count,
        "status": k.status,
        "status_label": _STATUS_LABEL.get(k.status, k.status),
        "is_enabled": (k.is_enabled if k.is_enabled is not None else 1),
        "error_msg": k.error_msg,
        "category": k.category,
        "tags": tags,
        "version": k.version,
        "source_type": k.source_type or "upload",
        "source_url": k.source_url,
        "created_at": k.created_at.strftime("%Y-%m-%d %H:%M:%S") if k.created_at else None,
        "updated_at": k.updated_at.strftime("%Y-%m-%d %H:%M:%S") if k.updated_at else None,
    }


def _tags_json(raw) -> Optional[str]:
    if not isinstance(raw, list):
        return None
    tags = [str(t).strip()[:40] for t in raw if str(t).strip()][:20]
    return json.dumps(tags, ensure_ascii=False) if tags else None


# ---------------- 读 ----------------

async def list_documents(
        async_db, user_id: int, space_id: int, *,
        category: str = None, tag: str = None, status: str = None, is_enabled: int = None,
) -> Dict[str, Any]:
    from models import knowledge_async_dao as kdao
    from service.access_control import get_owned_space_async

    if await get_owned_space_async(async_db, user_id, space_id) is None:
        raise NotFound("知识库空间不存在或无权限")

    all_docs = await kdao.list_knowledge_by_space_async(async_db, space_id)
    filtered = await kdao.list_knowledge_by_space_async(
        async_db, space_id, category=category, tag=tag, status=status, is_enabled=is_enabled,
    )
    categories = sorted({d.category for d in all_docs if d.category})
    tags_set = set()
    for d in all_docs:
        try:
            tags_set.update(json.loads(d.tags_json) if d.tags_json else [])
        except (TypeError, ValueError):
            pass
    return {
        "items": [_doc_dict(d) for d in filtered],
        "total": len(all_docs),
        "facets": {
            "categories": categories,
            "tags": sorted(tags_set),
            "statuses": [{"key": k, "label": v} for k, v in _STATUS_LABEL.items()],
        },
    }


# ---------------- 写（sync + BackgroundTasks） ----------------

def _require_write(db, user_id: int, space_id: int):
    """空间可访问 + 当前用户对该空间有写文档权限（owner/admin/editor）。返回 (space, role)。"""
    from service.access_control import get_owned_space, get_space_role
    from service.knowledge_space.membership import can_write_doc

    space = get_owned_space(db, user_id, space_id)
    if space is None:
        raise NotFound("知识库空间不存在或无权限")
    role = get_space_role(db, user_id, space_id)
    if not can_write_doc(role):
        raise PermissionDenied("你在该知识库空间只有只读权限")
    return space, role


def _audit(db, user_id: int, action: str, space_id: int, knowledge_id: int = None, detail=None):
    try:
        from models import kb_audit_dao
        kb_audit_dao.record(db, user_id, action, space_id=space_id, target_type="document",
                            target_id=knowledge_id, detail=detail, commit=True)
    except Exception:  # noqa: BLE001 —— 审计失败不影响主流程
        pass


def _doc_in_space_or_404(db, space_id: int, knowledge_id: int):
    from models.knowledge_dao import get_knowledge_in_space

    doc = get_knowledge_in_space(db, space_id, knowledge_id)
    if not doc:
        raise NotFound("文档不存在或无权限")
    return doc


def _validate_file(file_name: str) -> str:
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in (file_name or "") else ""
    if ext not in ALLOWED_TYPES:
        raise InvalidInput(f"不支持的文件类型: {ext}，支持: {sorted(ALLOWED_TYPES)}")
    return ext


def precheck_upload(db, user_id: int, space_id: int, file_names: List[str]) -> None:
    """路由在接收文件内容（写临时文件）之前先调用：没有写权限、文件类型不支持的请求，一个字节都不用收。"""
    _require_write(db, user_id, space_id)
    for name in file_names:
        _validate_file(name)


def upload(db, background_tasks, user_id: int, space_id: int, file_name: str, content: bytes,
          *, category: str = None, tags: List[str] = None, version: str = None) -> Dict[str, Any]:
    _require_write(db, user_id, space_id)
    file_type = _validate_file(file_name)
    if not content:
        raise InvalidInput("上传文件为空")
    from service import knowledge_service
    out = knowledge_service.create_upload_task(
        db, background_tasks, user_id, None, file_name, content, file_type,
        space_id=space_id, category=category, tags_json=_tags_json(tags), version=version,
    )
    _audit(db, user_id, "doc.upload", space_id, out.get("knowledge_id"), {"file_name": file_name})
    return out


def upload_batch(db, background_tasks, user_id: int, space_id: int, files: List[Dict]) -> List[Dict]:
    _require_write(db, user_id, space_id)
    if not files:
        raise InvalidInput("请选择至少一个文件")
    if len(files) > 20:
        raise InvalidInput("一次最多上传 20 个文件")
    prepared = []
    for f in files:
        _validate_file(f["file_name"])
        if not f.get("content"):
            raise InvalidInput(f"文件为空：{f['file_name']}")
        prepared.append({"file_name": f["file_name"], "content": f["content"],
                         "file_type": f["file_name"].rsplit(".", 1)[-1].lower()})
    from service import knowledge_service
    out = knowledge_service.create_upload_tasks(db, background_tasks, user_id, None, prepared, space_id=space_id)
    _audit(db, user_id, "doc.upload_batch", space_id, None, {"count": len(prepared)})
    return out


def crawl(db, background_tasks, user_id: int, space_id: int, pages: List[Dict]) -> List[Dict]:
    _require_write(db, user_id, space_id)
    from service import knowledge_service
    out = knowledge_service.create_crawl_tasks(db, background_tasks, user_id, None, pages, space_id=space_id)
    _audit(db, user_id, "doc.crawl", space_id, None, {"count": len(pages or [])})
    return out


def set_enabled(db, user_id: int, space_id: int, knowledge_id: int, is_enabled: int) -> Dict:
    _require_write(db, user_id, space_id)
    from service import knowledge_service

    doc = _doc_in_space_or_404(db, space_id, knowledge_id)
    out = knowledge_service.set_document_enabled(db, doc, is_enabled)
    _audit(db, user_id, "doc.set_enabled", space_id, knowledge_id, {"is_enabled": int(bool(is_enabled))})
    return out


def update_meta(db, user_id: int, space_id: int, knowledge_id: int, *,
                category=None, tags=None, version=None) -> Dict:
    _require_write(db, user_id, space_id)
    from models.knowledge_dao import update_knowledge_meta

    doc = _doc_in_space_or_404(db, space_id, knowledge_id)
    update_knowledge_meta(
        db, doc,
        category=(category if category is not None else None),
        tags_json=(_tags_json(tags) or "" if tags is not None else None),
        version=(version if version is not None else None),
    )
    db.commit()
    _audit(db, user_id, "doc.update_meta", space_id, knowledge_id)
    return _doc_dict(doc)


def reindex(db, background_tasks, user_id: int, space_id: int, knowledge_id: int) -> Dict:
    _require_write(db, user_id, space_id)
    from service import knowledge_service

    doc = _doc_in_space_or_404(db, space_id, knowledge_id)
    out = knowledge_service.create_reindex_task(
        db, background_tasks, user_id, doc.agent_id, knowledge_id, doc.file_name
    )
    _audit(db, user_id, "doc.reindex", space_id, knowledge_id, {"file_name": doc.file_name})
    return out


def delete(db, user_id: int, space_id: int, knowledge_id: int) -> Dict:
    _require_write(db, user_id, space_id)
    from service import knowledge_service
    from service.knowledge_space.space_service import recount_space

    doc = _doc_in_space_or_404(db, space_id, knowledge_id)
    file_name = doc.file_name
    out = knowledge_service.delete_document_completely(db, doc.agent_id, knowledge_id)
    recount_space(db, space_id)
    _audit(db, user_id, "doc.delete", space_id, knowledge_id, {"file_name": file_name})
    return out
