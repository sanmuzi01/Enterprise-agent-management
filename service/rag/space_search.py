"""多知识库空间联合检索（同步入口，自带同步 Session）。

阶段3：Agent 绑定 ≥1 个知识库空间时，检索走这里——
  1. 校验每个 space_id 都在 `user_space_ids(user_id)` 里，否则 `PermissionError`
  2. query 只向量化一次
  3. 逐空间查 `space_{id}` collection（`vector_migrated=0` 时双读 legacy `agent_{legacy_agent_id}`）
  4. 合并候选、反查 chunk / knowledge 元数据、过滤禁用文档
  5. 可选 rerank（复用 `rag_service._get_rerank_client`）
  6. 截断 top_k，组装带 `【来源N】` 编号的 context + citations
  7. 拒答：命中为空 / 最高分低于 `RAG_MIN_SCORE`（env，默认 0.2）→ 空 hits

RAG 链路整体同步（见 docs/sync-async-boundary.md）。异步调用方经 `asyncio.to_thread`
调用本模块，不要把同步 Session 泄漏进路由 / runtime。
"""

import os
import re
from typing import Any, Dict, List, Optional

from utils.logger_handler import get_logger

logger = get_logger("space_search")

# 编号/型号/英文专有名词这类 token：字母数字混排、允许中间带 -_./，长度至少 3。
# 向量检索靠语义相似度，恰恰对"精确匹配一串编号"天然不敏感；这类 token 直接
# 拿去 chunk 内容里做关键词补充召回，纠偏那类查询几乎搜不到的问题。
_KEYWORD_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_./]{2,}")


def _extract_keyword_tokens(query: str, limit: int = 5) -> List[str]:
    tokens = [t for t in dict.fromkeys(_KEYWORD_TOKEN_RE.findall(query or ""))]
    return tokens[:limit]  # 一次查太多 token 会拖慢 LIKE 查询，只取前几个


def _min_score() -> float:
    try:
        return float(os.getenv("RAG_MIN_SCORE", "0.2"))
    except (TypeError, ValueError):
        return 0.2


def _candidate_count(top_k: int, rerank: bool) -> int:
    return max(10, top_k * 2) if rerank else max(top_k, 5)


def _should_refuse(hits: List[Dict[str, Any]], threshold: float, refuse_when_empty: bool) -> bool:
    """拒答判定：开了拒答且（无命中 或 最高分低于阈值）。"""
    if not refuse_when_empty:
        return False
    if not hits:
        return True
    return max((h.get("score", 0.0) for h in hits), default=0.0) < threshold


def search_spaces(
    user_id: int,
    space_ids: List[int],
    query: str,
    top_k: int = 5,
    rerank: Optional[bool] = None,
    refuse_when_empty: bool = True,
    min_score: Optional[float] = None,
) -> Dict[str, Any]:
    """跨多个知识库空间检索，返回 hits / context / citations（结构见模块 docstring 与方案 5 节）。"""
    from models.init_db import SessionLocal
    from models.knowledge_chunk_dao import get_chunks_by_vector_ids, search_chunks_by_keyword
    from models.knowledge_dao import get_knowledge_by_id
    from models.knowledge_space_dao import list_spaces_by_ids
    from service.access_control import user_space_ids
    from service.rag import rag_service
    from service.rag.embedding_service import embed_query
    from service.rag.vector_store_service import (
        legacy_agent_key,
        search_similar,
        space_collection_key,
    )

    if not query or not query.strip():
        raise ValueError("检索关键词不能为空")

    want_ids = [int(s) for s in dict.fromkeys(space_ids or [])]
    if not want_ids:
        return _empty_result(query, want_ids, top_k, bool(rerank))

    top_k = max(1, min(int(top_k or 5), 20))
    use_rerank = rag_service.RAG_RERANK_ENABLED if rerank is None else bool(rerank)
    threshold = _min_score() if min_score is None else float(min_score)

    db = SessionLocal()
    try:
        allowed = user_space_ids(db, user_id)
        bad = [s for s in want_ids if s not in allowed]
        if bad:
            raise PermissionError(f"包含无权访问的知识库空间：{bad}")

        spaces = {s.id: s for s in list_spaces_by_ids(db, want_ids)}
        missing = [s for s in want_ids if s not in spaces]
        if missing:
            raise PermissionError(f"知识库空间不存在：{missing}")

        query_vector = embed_query(db, user_id, query)
        if not query_vector:
            return _empty_result(query, want_ids, top_k, use_rerank)

        per_space = _candidate_count(top_k, use_rerank)
        raw: List[Dict[str, Any]] = []
        seen_vids = set()
        for sid in want_ids:
            space = spaces[sid]
            keys = [space_collection_key(sid)]
            if not space.vector_migrated and space.legacy_agent_id:
                keys.append(legacy_agent_key(space.legacy_agent_id))
            for key in keys:
                try:
                    hits = search_similar(key, query_vector, top_k=per_space)
                except Exception as e:  # 单集合失败不影响其它空间
                    logger.warning(f"空间 {sid} 集合 {key} 检索失败，跳过: {e}")
                    continue
                for h in hits:
                    vid = h.get("id")
                    if not vid or vid in seen_vids:
                        continue
                    seen_vids.add(vid)
                    h["_space_id"] = sid
                    raw.append(h)

        if not raw:
            return _empty_result(query, want_ids, top_k, use_rerank)

        raw.sort(key=lambda r: r.get("distance", 1.0))

        chunks = get_chunks_by_vector_ids(db, [r["id"] for r in raw])
        chunk_map = {c.vector_id: c for c in chunks}
        knowledge_map: Dict[int, Any] = {}

        merged: List[Dict[str, Any]] = []
        for r in raw:
            chunk = chunk_map.get(r["id"])
            if not chunk:
                continue
            kid = chunk.knowledge_id
            if kid not in knowledge_map:
                knowledge_map[kid] = get_knowledge_by_id(db, kid)
            knowledge = knowledge_map[kid]
            if not knowledge or knowledge.is_enabled == 0:
                continue
            space = spaces.get(r["_space_id"])
            merged.append({
                "chunk_id": chunk.id,
                "content": chunk.content,
                "score": max(0.0, 1.0 - float(r.get("distance", 1.0))),
                "rerank_score": None,
                "distance": r.get("distance"),
                "knowledge_id": kid,
                "chunk_index": chunk.chunk_index,
                "source": {
                    "space_id": r["_space_id"],
                    "space_name": space.name if space else "",
                    "file_name": knowledge.file_name,
                    "file_type": knowledge.file_type,
                    "category": knowledge.category,
                    "version": knowledge.version,
                    "source_url": knowledge.source_url,
                },
            })

        # ---- 关键词补充召回：向量检索对编号/型号这类精确匹配天然弱，
        # 直接在 chunk 内容里找一遍，命中的一起交给下面的排序/rerank ----
        keyword_tokens = _extract_keyword_tokens(query)
        if keyword_tokens:
            existing_ids = {m["chunk_id"] for m in merged}
            extra_chunks = search_chunks_by_keyword(db, want_ids, keyword_tokens, existing_ids, limit=5)
            for chunk in extra_chunks:
                kid = chunk.knowledge_id
                if kid not in knowledge_map:
                    knowledge_map[kid] = get_knowledge_by_id(db, kid)
                knowledge = knowledge_map[kid]
                if not knowledge or knowledge.is_enabled == 0:
                    continue
                space = spaces.get(knowledge.space_id)
                merged.append({
                    "chunk_id": chunk.id,
                    "content": chunk.content,
                    "score": 0.5,  # 关键词命中的基线分；开了 rerank 会被重新打分
                    "rerank_score": None,
                    "distance": None,
                    "knowledge_id": kid,
                    "chunk_index": chunk.chunk_index,
                    "keyword_hit": True,
                    "source": {
                        "space_id": knowledge.space_id,
                        "space_name": space.name if space else "",
                        "file_name": knowledge.file_name,
                        "file_type": knowledge.file_type,
                        "category": knowledge.category,
                        "version": knowledge.version,
                        "source_url": knowledge.source_url,
                    },
                })
            if extra_chunks:
                logger.info(f"关键词补充召回 {len(extra_chunks)} 条: tokens={keyword_tokens}")

        if not merged:
            return _empty_result(query, want_ids, top_k, use_rerank)

        merged = _maybe_rerank(query, merged, top_k, use_rerank, rag_service)
        merged = merged[:top_k]

        if _should_refuse(merged, threshold, refuse_when_empty):
            best = max((h["score"] for h in merged), default=0.0)
            logger.info(f"多空间检索最高分 {best:.4f} < 阈值 {threshold}，按拒答处理")
            return _empty_result(query, want_ids, top_k, use_rerank, refused=True)

        context, citations = _assemble(merged)
        return {
            "query": query,
            "space_ids": want_ids,
            "top_k": top_k,
            "rerank": use_rerank,
            "refused": False,
            "hits": merged,
            "context": context,
            "citations": citations,
        }
    finally:
        db.close()


async def search_spaces_async(
    user_id: int,
    space_ids: List[int],
    query: str,
    top_k: int = 5,
    rerank: Optional[bool] = None,
    refuse_when_empty: bool = True,
    min_score: Optional[float] = None,
    db=None,
) -> Dict[str, Any]:
    """`search_spaces` 的彻底 async 版。

    `db` 为 AsyncSession；不传则自开一个 `AsyncSessionLocal()`（RAG 检索是叶子子系统，
    自带事务，和同步版一致）。归属校验 / 空间元数据 / chunk 反查走 async DAO，
    向量化走 async 客户端，ChromaDB 检索与 rerank 封 `asyncio.to_thread`。
    """
    import asyncio

    from models.knowledge_async_dao import (
        get_chunks_by_vector_ids_async, get_knowledge_by_ids_async, search_chunks_by_keyword_async,
    )
    from models.knowledge_space_async_dao import list_spaces_by_ids_async
    from service.access_control import user_space_ids_async
    from service.rag import rag_service
    from service.rag.embedding_service import aembed_query_async
    from service.rag.vector_store_service import (
        legacy_agent_key, search_similar, space_collection_key,
    )

    if not query or not query.strip():
        raise ValueError("检索关键词不能为空")

    want_ids = [int(s) for s in dict.fromkeys(space_ids or [])]
    if not want_ids:
        return _empty_result(query, want_ids, top_k, bool(rerank))

    top_k = max(1, min(int(top_k or 5), 20))
    use_rerank = rag_service.RAG_RERANK_ENABLED if rerank is None else bool(rerank)
    threshold = _min_score() if min_score is None else float(min_score)

    async def _run(session):
        allowed = await user_space_ids_async(session, user_id)
        bad = [s for s in want_ids if s not in allowed]
        if bad:
            raise PermissionError(f"包含无权访问的知识库空间：{bad}")

        spaces = {s.id: s for s in await list_spaces_by_ids_async(session, want_ids)}
        missing = [s for s in want_ids if s not in spaces]
        if missing:
            raise PermissionError(f"知识库空间不存在：{missing}")

        query_vector = await aembed_query_async(session, user_id, query)
        if not query_vector:
            return _empty_result(query, want_ids, top_k, use_rerank)

        per_space = _candidate_count(top_k, use_rerank)
        raw: List[Dict[str, Any]] = []
        seen_vids = set()
        for sid in want_ids:
            space = spaces[sid]
            keys = [space_collection_key(sid)]
            if not space.vector_migrated and space.legacy_agent_id:
                keys.append(legacy_agent_key(space.legacy_agent_id))
            for key in keys:
                try:
                    hits = await asyncio.to_thread(search_similar, key, query_vector, per_space)
                except Exception as e:  # 单集合失败不影响其它空间
                    logger.warning(f"空间 {sid} 集合 {key} 检索失败，跳过: {e}")
                    continue
                for h in hits:
                    vid = h.get("id")
                    if not vid or vid in seen_vids:
                        continue
                    seen_vids.add(vid)
                    h["_space_id"] = sid
                    raw.append(h)

        if not raw:
            return _empty_result(query, want_ids, top_k, use_rerank)

        raw.sort(key=lambda r: r.get("distance", 1.0))

        chunks = await get_chunks_by_vector_ids_async(session, [r["id"] for r in raw])
        chunk_map = {c.vector_id: c for c in chunks}
        # 一条 WHERE id IN (...) 批量取，而不是命中几个文档就发几条 SELECT
        # （检索是每次聊天都会走的热路径，之前这里是循环里逐个查）。
        knowledge_map: Dict[int, Any] = await get_knowledge_by_ids_async(
            session, {c.knowledge_id for c in chunks},
        )

        merged: List[Dict[str, Any]] = []
        for r in raw:
            chunk = chunk_map.get(r["id"])
            if not chunk:
                continue
            kid = chunk.knowledge_id
            knowledge = knowledge_map.get(kid)
            if not knowledge or knowledge.is_enabled == 0:
                continue
            space = spaces.get(r["_space_id"])
            merged.append({
                "chunk_id": chunk.id,
                "content": chunk.content,
                "score": max(0.0, 1.0 - float(r.get("distance", 1.0))),
                "rerank_score": None,
                "distance": r.get("distance"),
                "knowledge_id": kid,
                "chunk_index": chunk.chunk_index,
                "source": {
                    "space_id": r["_space_id"],
                    "space_name": space.name if space else "",
                    "file_name": knowledge.file_name,
                    "file_type": knowledge.file_type,
                    "category": knowledge.category,
                    "version": knowledge.version,
                    "source_url": knowledge.source_url,
                },
            })

        keyword_tokens = _extract_keyword_tokens(query)
        if keyword_tokens:
            existing_ids = {m["chunk_id"] for m in merged}
            extra_chunks = await search_chunks_by_keyword_async(
                session, want_ids, keyword_tokens, existing_ids, limit=5,
            )
            missing_ids = {c.knowledge_id for c in extra_chunks if c.knowledge_id not in knowledge_map}
            if missing_ids:
                knowledge_map.update(await get_knowledge_by_ids_async(session, missing_ids))
            for chunk in extra_chunks:
                kid = chunk.knowledge_id
                knowledge = knowledge_map.get(kid)
                if not knowledge or knowledge.is_enabled == 0:
                    continue
                space = spaces.get(knowledge.space_id)
                merged.append({
                    "chunk_id": chunk.id,
                    "content": chunk.content,
                    "score": 0.5,
                    "rerank_score": None,
                    "distance": None,
                    "knowledge_id": kid,
                    "chunk_index": chunk.chunk_index,
                    "keyword_hit": True,
                    "source": {
                        "space_id": knowledge.space_id,
                        "space_name": space.name if space else "",
                        "file_name": knowledge.file_name,
                        "file_type": knowledge.file_type,
                        "category": knowledge.category,
                        "version": knowledge.version,
                        "source_url": knowledge.source_url,
                    },
                })
            if extra_chunks:
                logger.info(f"关键词补充召回(async) {len(extra_chunks)} 条: tokens={keyword_tokens}")

        if not merged:
            return _empty_result(query, want_ids, top_k, use_rerank)

        merged = await _maybe_rerank_async(query, merged, top_k, use_rerank, rag_service)
        merged = merged[:top_k]

        if _should_refuse(merged, threshold, refuse_when_empty):
            best = max((h["score"] for h in merged), default=0.0)
            logger.info(f"多空间检索最高分 {best:.4f} < 阈值 {threshold}，按拒答处理")
            return _empty_result(query, want_ids, top_k, use_rerank, refused=True)

        context, citations = _assemble(merged)
        return {
            "query": query,
            "space_ids": want_ids,
            "top_k": top_k,
            "rerank": use_rerank,
            "refused": False,
            "hits": merged,
            "context": context,
            "citations": citations,
        }

    if db is not None:
        return await _run(db)
    from models.async_db import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        return await _run(session)


async def _maybe_rerank_async(query, merged, top_k, use_rerank, rag_service):
    import asyncio
    if not use_rerank or len(merged) <= 1:
        return merged
    reranker = rag_service._get_rerank_client()
    if not reranker:
        return merged
    try:
        reranked = await asyncio.to_thread(
            reranker.rerank, query, [m["content"] for m in merged], len(merged),
        )
    except Exception as e:
        logger.warning(f"rerank 失败，用向量分数: {e}")
        return merged
    out = []
    for original_idx, rerank_score in reranked:
        if original_idx < len(merged):
            item = dict(merged[original_idx])
            item["rerank_score"] = rerank_score
            item["score"] = rerank_score
            out.append(item)
    return out or merged


def _maybe_rerank(query, merged, top_k, use_rerank, rag_service):
    if not use_rerank or len(merged) <= 1:
        return merged
    reranker = rag_service._get_rerank_client()
    if not reranker:
        return merged
    try:
        reranked = reranker.rerank(query, [m["content"] for m in merged], top_n=len(merged))
    except Exception as e:
        logger.warning(f"rerank 失败，用向量分数: {e}")
        return merged
    out = []
    for original_idx, rerank_score in reranked:
        if original_idx < len(merged):
            item = dict(merged[original_idx])
            item["rerank_score"] = rerank_score
            item["score"] = rerank_score
            out.append(item)
    return out or merged


from service.prompt_guard import neutralize  # noqa: E402


def _assemble(hits: List[Dict[str, Any]]):
    """按去重后的文档编号组装 context（【来源N】）+ citations。"""
    order: List[int] = []
    for h in hits:
        kid = h["knowledge_id"]
        if kid not in order:
            order.append(kid)
    index_of = {kid: i + 1 for i, kid in enumerate(order)}

    blocks = []
    for h in hits:
        idx = index_of[h["knowledge_id"]]
        src = h["source"]
        head = f"【来源{idx}】{neutralize(src['space_name'])} / {neutralize(src['file_name'])}"
        if src.get("version"):
            head += f"（{neutralize(src['version'])}）"
        blocks.append(f"{head}\n{neutralize(h['content'])}")
        h["citation_index"] = idx

    citations = []
    for kid in order:
        sample = next(h for h in hits if h["knowledge_id"] == kid)
        src = sample["source"]
        citations.append({
            "index": index_of[kid],
            "knowledge_id": kid,
            "file_name": src["file_name"],
            "space_id": src["space_id"],
            "space_name": src["space_name"],
            "snippet": (sample.get("content") or "")[:300],
        })
    return "\n\n".join(blocks), citations


def _empty_result(query, space_ids, top_k, rerank, refused=False):
    return {
        "query": query,
        "space_ids": space_ids,
        "top_k": top_k,
        "rerank": rerank,
        "refused": refused,
        "hits": [],
        "context": "",
        "citations": [],
    }
