"""带归属校验的 RAG 检索入口。

- 同步版（`search_scoped` / `search_for_widget` / `search_for_agent`）：自带同步 `Session`，
  剩余同步调用方（`agent_service` 预览、`debug_service`）用；异步调用方经 `asyncio.to_thread` 调。
- 异步版（`*_async`）：彻底 async —— 向量化走 async 客户端 + async 配置查询，归属校验 / chunk
  反查走 async DAO，ChromaDB 检索与 rerank 封 `asyncio.to_thread`。自带 `AsyncSessionLocal()`
  （不传 `db`）或复用传入的 `AsyncSession`。聊天链路（`agent_runtime`）用这条。

单独成文件（不改 `rag_service.py`）是为了避免那个文件的大范围行尾变更。
"""

from typing import Any, Dict, List, Optional


def search_scoped(
    user_id: int,
    agent_id: int,
    query: str,
    top_k: int = 5,
    knowledge_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """校验 agent 归属（及可选的单文档归属 / 启用状态）后做一次检索。

    - 归属不符 -> PermissionError
    - query 为空 / 指定文档已禁用 -> ValueError
    """
    from models.init_db import SessionLocal
    from models.agent_dao import get_agent_by_id
    from models.knowledge_dao import get_knowledge_by_id
    from service.rag import rag_service

    if not query or not query.strip():
        raise ValueError("检索关键词不能为空")

    db = SessionLocal()
    try:
        agent = get_agent_by_id(db, agent_id)
        if not agent or agent.user_id != user_id:
            raise PermissionError("智能体不存在或无权限")
        if knowledge_id is not None:
            doc = get_knowledge_by_id(db, knowledge_id)
            if not doc or doc.agent_id != agent_id:
                raise PermissionError("文档不存在或无权限")
            if doc.is_enabled == 0:
                raise ValueError("该文档已禁用，不参与检索")
        return rag_service.search(db, user_id, agent_id, query, top_k=top_k, knowledge_id=knowledge_id)
    finally:
        db.close()


def search_for_widget(user_id: int, agent_id: int, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """工作台组件用的检索入口（无 knowledge_id 维度）。"""
    hits = search_scoped(user_id, agent_id, query, top_k=top_k)
    return _widget_shape(hits)


def search_for_agent(
    user_id: int,
    agent_id: int,
    query: str,
    top_k: int = 5,
    rerank: Optional[bool] = None,
    refuse_when_empty: bool = True,
) -> Dict[str, Any]:
    """Agent 对话时的统一检索入口（同步，自带 Session）。

    - Agent 绑定了 ≥1 个知识库空间 -> 走 `space_search.search_spaces`，返回带 `【来源N】`
      编号的 context 和 citations。
    - 未绑定任何空间 -> 回退旧的「Agent 私有库」检索（`search_scoped`），context 为
      `[知识片段N]` 形式，citations 为空。

    统一返回：
      {"mode": "spaces"|"agent", "hits": [...], "context": str,
       "citations": [...], "refused": bool}
    异步调用方经 `asyncio.to_thread` 调用本函数。
    """
    from models.init_db import SessionLocal
    from service.access_control import get_usable_agent
    from models.agent_knowledge_space_dao import list_space_ids_by_agent

    db = SessionLocal()
    try:
        # Phase 3D 阶段2：部门/企业共享的 Agent 也能检索，不只是 owner（见
        # docs/enterprise-rbac-plan.md 9.5）。下面 `space_ids` 为空时会回退到
        # `search_scoped`——那是遗留的"Agent 私有库"检索，内部仍然是严格 owner 校验
        # （它同时也被 widget 检索复用，不能跟着放宽）。新建的部门共享 Agent 应该绑知识库
        # 空间而不是用私有库，这条回退分支目前不支持非 owner，是个已知的窄口径限制。
        agent = get_usable_agent(db, user_id, agent_id)
        if not agent:
            raise PermissionError("智能体不存在或无权限")
        space_ids = list_space_ids_by_agent(db, agent_id)
    finally:
        db.close()

    if space_ids:
        from service.rag.space_search import search_spaces

        res = search_spaces(
            user_id, space_ids, query,
            top_k=top_k, rerank=rerank, refuse_when_empty=refuse_when_empty,
        )
        return {
            "mode": "spaces",
            "hits": res["hits"],
            "context": res["context"],
            "citations": res["citations"],
            "refused": res["refused"],
        }

    hits = search_scoped(user_id, agent_id, query, top_k=top_k)
    context, citations = _assemble_agent_context(hits)
    return {"mode": "agent", "hits": hits, "context": context, "citations": citations, "refused": False}


# ============================================================================
# 彻底 async 版
# ============================================================================

def _widget_shape(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "content": h.get("content", ""),
            "score": h.get("score"),
            "file_name": h.get("file_name", ""),
            "knowledge_id": h.get("knowledge_id"),
        }
        for h in hits
    ]


from service.prompt_guard import neutralize  # noqa: E402


def _assemble_agent_context(hits):
    """agent 私有库：按文档去重编号，产出 `【来源N】` context + citations（带 snippet）。

    和空间检索的 `【来源N】` 口径统一——之前私有库用的是 `[知识片段N]` 且没有 citations，
    导致 prompt 让模型标「【来源N】」但上下文里根本没有这个编号，且前端没有来源芯片。
    """
    order = []
    for h in hits:
        kid = h.get("knowledge_id")
        if kid is not None and kid not in order:
            order.append(kid)
    idx_of = {kid: i + 1 for i, kid in enumerate(order)}

    blocks = []
    for h in hits:
        kid = h.get("knowledge_id")
        if kid is None:
            continue
        i = idx_of[kid]
        fn = h.get("file_name") or f"文档{kid}"
        blocks.append(f"【来源{i}】{neutralize(fn)}\n{neutralize(h.get('content', ''))}")
        h["citation_index"] = i

    citations = []
    for kid in order:
        sample = next(h for h in hits if h.get("knowledge_id") == kid)
        citations.append({
            "index": idx_of[kid],
            "knowledge_id": kid,
            "file_name": sample.get("file_name") or f"文档{kid}",
            "space_id": None,
            "space_name": "",
            "snippet": (sample.get("content") or "")[:300],
        })
    return "\n\n".join(blocks), citations


async def _scoped_hits_async(session, user_id, agent_id, query, top_k, knowledge_id):
    """归属校验 + rag_service.search_async，复用传入的 AsyncSession。"""
    from models.agent_async_dao import get_agent_by_id_async
    from models.knowledge_async_dao import get_knowledge_by_id_async
    from service.rag import rag_service

    if not query or not query.strip():
        raise ValueError("检索关键词不能为空")

    agent = await get_agent_by_id_async(session, agent_id)
    if not agent or agent.user_id != user_id:
        raise PermissionError("智能体不存在或无权限")
    if knowledge_id is not None:
        doc = await get_knowledge_by_id_async(session, knowledge_id)
        if not doc or doc.agent_id != agent_id:
            raise PermissionError("文档不存在或无权限")
        if doc.is_enabled == 0:
            raise ValueError("该文档已禁用，不参与检索")
    return await rag_service.search_async(
        session, user_id, agent_id, query, top_k=top_k, knowledge_id=knowledge_id,
    )


async def search_scoped_async(
    user_id: int,
    agent_id: int,
    query: str,
    top_k: int = 5,
    knowledge_id: Optional[int] = None,
    db=None,
) -> List[Dict[str, Any]]:
    """`search_scoped` 的彻底 async 版。"""
    if db is not None:
        return await _scoped_hits_async(db, user_id, agent_id, query, top_k, knowledge_id)
    from models.async_db import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        return await _scoped_hits_async(session, user_id, agent_id, query, top_k, knowledge_id)


async def search_for_widget_async(
    user_id: int, agent_id: int, query: str, top_k: int = 5, db=None,
) -> List[Dict[str, Any]]:
    """`search_for_widget` 的彻底 async 版。"""
    hits = await search_scoped_async(user_id, agent_id, query, top_k=top_k, db=db)
    return _widget_shape(hits)


async def search_for_agent_async(
    user_id: int,
    agent_id: int,
    query: str,
    top_k: int = 5,
    rerank: Optional[bool] = None,
    refuse_when_empty: bool = True,
    db=None,
) -> Dict[str, Any]:
    """`search_for_agent` 的彻底 async 版。聊天链路（`agent_runtime`）直接 await，无 to_thread。

    比同步版多返回一个 `stats`（RAG 上下文压缩 / Token 节省），给调试台和聊天引用面板展示。
    """
    from service.access_control import get_usable_agent_async
    from models.agent_knowledge_space_dao import list_space_ids_by_agent_async
    from models.knowledge_async_dao import sum_chunk_chars_by_knowledge_ids_async
    from service.rag.rag_stats import build_savings, hit_knowledge_ids

    async def _run(session):
        # 同步版同一条注释：部门/企业共享 Agent 也能检索，回退到私有库分支仍是严格 owner。
        agent = await get_usable_agent_async(session, user_id, agent_id)
        if not agent:
            raise PermissionError("智能体不存在或无权限")
        space_ids = await list_space_ids_by_agent_async(session, agent_id)

        if space_ids:
            from service.rag.space_search import search_spaces_async

            res = await search_spaces_async(
                user_id, space_ids, query,
                top_k=top_k, rerank=rerank, refuse_when_empty=refuse_when_empty,
                db=session,
            )
            out = {
                "mode": "spaces",
                "hits": res["hits"],
                "context": res["context"],
                "citations": res["citations"],
                "refused": res["refused"],
            }
        else:
            hits = await _scoped_hits_async(session, user_id, agent_id, query, top_k, None)
            context, citations = _assemble_agent_context(hits)
            out = {"mode": "agent", "hits": hits, "context": context,
                   "citations": citations, "refused": False}

        src_chars = await sum_chunk_chars_by_knowledge_ids_async(
            session, hit_knowledge_ids(out["hits"])
        )
        out["stats"] = build_savings(src_chars, out["context"], len(out["hits"]))
        return out

    if db is not None:
        return await _run(db)
    from models.async_db import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        return await _run(session)
