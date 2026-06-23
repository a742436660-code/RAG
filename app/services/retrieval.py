import time
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.middleware import request_id_var
from app.db.fts import sanitize_fts_query
from app.db.models import DocumentChunk, RetrievalLog
from app.schemas.api import SearchResponse, SearchResult
from app.services.json_utils import dumps_json
from app.services.knowledge_bases import get_knowledge_base_or_404
from app.services.rerankers import LexicalReranker, RerankHit, get_reranker
from app.services.vector_store import get_vector_store


@dataclass
class RankedChunk:
    # 检索链路内部使用的排序结果，保留来源和名次，便于融合和日志记录。
    chunk: DocumentChunk
    score: float
    source: str
    rank: int


def search_knowledge_base(
    db: Session,
    knowledge_base_id: str,
    query: str,
    top_k: int,
    retrieval_mode: str,
    conversation_id: Optional[str] = None,
) -> SearchResponse:
    # search API 是 retrieve 的薄包装：只返回证据，不做答案生成。
    output = retrieve(
        db=db,
        knowledge_base_id=knowledge_base_id,
        query=query,
        top_k=top_k,
        retrieval_mode=retrieval_mode,
        conversation_id=conversation_id,
    )
    return SearchResponse(
        query=query,
        retrieval_mode=retrieval_mode,
        results=[ranked_to_search_result(item) for item in output.results],
        log_id=output.log.id,
        fallback_used=bool(output.log.fallback_used),
        fallback_reason=output.log.fallback_reason,
    )


@dataclass
class RetrievalOutput:
    results: list[RankedChunk]
    log: RetrievalLog


@dataclass
class RerankRun:
    results: list[RankedChunk]
    provider: str
    model: str
    fallback_used: bool = False
    fallback_reason: Optional[str] = None


def retrieve(
    db: Session,
    knowledge_base_id: str,
    query: str,
    top_k: int,
    retrieval_mode: str,
    conversation_id: Optional[str] = None,
) -> RetrievalOutput:
    # RAG 检索主入口：按 retrieval_mode 选择 sparse/dense，再融合、重排、截断和写日志。
    get_knowledge_base_or_404(db, knowledge_base_id)
    started = time.perf_counter()
    sparse_ranked: list[RankedChunk] = []
    dense_ranked: list[RankedChunk] = []

    if retrieval_mode in {"sparse", "hybrid", "hybrid_rerank"}:
        # sparse 侧多取一些候选，给后续融合/重排留空间。
        sparse_ranked = sparse_search(db, knowledge_base_id, query, limit=max(top_k * 2, top_k))
    if retrieval_mode in {"dense", "hybrid", "hybrid_rerank"}:
        # dense 侧同样多取候选，避免融合时候选池太窄。
        dense_ranked = dense_search(db, knowledge_base_id, query, limit=max(top_k * 2, top_k))

    if retrieval_mode == "sparse":
        fused = sparse_ranked
    elif retrieval_mode == "dense":
        fused = dense_ranked
    else:
        fused = rrf_fuse(sparse_ranked, dense_ranked, rrf_k=get_settings().default_rrf_k)

    rerank_run: Optional[RerankRun] = None
    if retrieval_mode == "hybrid_rerank":
        rerank_run = run_rerank(query, fused)
        reranked = rerank_run.results
    else:
        reranked = fused
    final = [
        RankedChunk(chunk=item.chunk, score=item.score, source=item.source, rank=index + 1)
        for index, item in enumerate(reranked[:top_k])
    ]

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    vector_store = get_vector_store()
    fallback_reasons: list[str] = []
    if (
        retrieval_mode in {"dense", "hybrid", "hybrid_rerank"}
        and vector_store.backend_name == "local"
    ):
        fallback_reasons.append("chromadb_unavailable_using_local_dense_scan")
    if rerank_run is not None and rerank_run.fallback_reason:
        fallback_reasons.append(rerank_run.fallback_reason)
    fallback_used = int(bool(fallback_reasons))
    fallback_reason = ";".join(fallback_reasons) if fallback_reasons else None
    rerank_log_extra = None
    if rerank_run is not None:
        rerank_log_extra = {
            "rerank_provider": rerank_run.provider,
            "rerank_model": rerank_run.model,
            "rerank_fallback": rerank_run.fallback_used,
        }
    log = RetrievalLog(
        request_id=request_id_var.get(None),
        knowledge_base_id=knowledge_base_id,
        conversation_id=conversation_id,
        query=query,
        retrieval_mode=retrieval_mode,
        dense_results_json=dumps_json([ranked_to_log_item(item) for item in dense_ranked]),
        sparse_results_json=dumps_json([ranked_to_log_item(item) for item in sparse_ranked]),
        fusion_results_json=dumps_json([ranked_to_log_item(item) for item in fused]),
        rerank_results_json=dumps_json(
            [ranked_to_log_item(item, rerank_log_extra) for item in reranked]
        ),
        final_evidence_json=dumps_json([ranked_to_log_item(item) for item in final]),
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        retrieval_latency_ms=elapsed_ms,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return RetrievalOutput(results=final, log=log)


def sparse_search(
    db: Session,
    knowledge_base_id: str,
    query: str,
    limit: int,
) -> list[RankedChunk]:
    # SQLite FTS5 关键词检索。bm25 分数越小越相关，因此代码中取负数转为“越大越好”。
    fts_query = sanitize_fts_query(query)
    rows: list[Any] = []
    try:
        rows = list(
            db.execute(
                text(
                    """
                    SELECT chunk_id, bm25(document_chunks_fts) AS bm25_score
                    FROM document_chunks_fts
                    WHERE document_chunks_fts MATCH :query
                      AND knowledge_base_id = :knowledge_base_id
                    ORDER BY bm25_score
                    LIMIT :limit
                    """
                ),
                {"query": fts_query, "knowledge_base_id": knowledge_base_id, "limit": limit},
            ).all()
        )
    except Exception:
        # FTS 查询语法或 tokenizer 异常时不让整个搜索失败，而是退回 LIKE。
        rows = []

    if not rows:
        # 没有 FTS 命中时使用 LIKE 做最后兜底，牺牲效果换取可用性。
        return like_search(db, knowledge_base_id, query, limit)

    chunks = load_chunks_by_ids(db, [row.chunk_id for row in rows])
    results = []
    for rank, row in enumerate(rows, start=1):
        chunk = chunks.get(row.chunk_id)
        if chunk is None:
            continue
        results.append(
            RankedChunk(
                chunk=chunk,
                score=max(0.0, -float(row.bm25_score)),
                source="sparse",
                rank=rank,
            )
        )
    return results


def like_search(
    db: Session,
    knowledge_base_id: str,
    query: str,
    limit: int,
) -> list[RankedChunk]:
    # 简单子串匹配兜底，只适合小数据量和精确短语。
    pattern = f"%{query.strip()}%"
    chunks = list(
        db.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.knowledge_base_id == knowledge_base_id,
                DocumentChunk.content.like(pattern),
            )
            .limit(limit)
        ).all()
    )
    return [
        RankedChunk(chunk=chunk, score=1.0 / rank, source="sparse", rank=rank)
        for rank, chunk in enumerate(chunks, start=1)
    ]


def dense_search(
    db: Session,
    knowledge_base_id: str,
    query: str,
    limit: int,
) -> list[RankedChunk]:
    # 向量检索先拿 chunk_id，再回数据库加载完整 chunk 内容和元数据。
    hits = get_vector_store().query(db, knowledge_base_id, query, top_k=limit)
    chunks = load_chunks_by_ids(db, [hit.chunk_id for hit in hits])
    results = []
    for rank, hit in enumerate(hits, start=1):
        chunk = chunks.get(hit.chunk_id)
        if chunk is None:
            continue
        results.append(RankedChunk(chunk=chunk, score=hit.score, source="dense", rank=rank))
    return results


def load_chunks_by_ids(db: Session, chunk_ids: list[str]) -> dict[str, DocumentChunk]:
    # 统一回表加载 chunk，返回 dict 便于保持向量库/FTS 返回顺序。
    if not chunk_ids:
        return {}
    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids))).all()
    return {chunk.id: chunk for chunk in chunks}


def rrf_fuse(
    sparse_results: list[RankedChunk],
    dense_results: list[RankedChunk],
    rrf_k: int,
) -> list[RankedChunk]:
    # Reciprocal Rank Fusion：用排名而不是原始分数融合 sparse/dense。
    # 这样 BM25 分数和向量相似度不需要强行归一到同一尺度。
    by_id: dict[str, RankedChunk] = {}
    scores: dict[str, float] = {}
    sources: dict[str, set[str]] = {}

    for source_results in (sparse_results, dense_results):
        for rank, item in enumerate(source_results, start=1):
            chunk_id = item.chunk.id
            by_id[chunk_id] = item
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            sources.setdefault(chunk_id, set()).add(item.source)

    fused = []
    for chunk_id, item in by_id.items():
        source_set = sources.get(chunk_id, set())
        source = "both" if {"sparse", "dense"}.issubset(source_set) else next(iter(source_set))
        fused.append(
            RankedChunk(
                chunk=item.chunk,
                score=scores[chunk_id],
                source=source,
                rank=0,
            )
        )
    fused.sort(key=lambda item: item.score, reverse=True)
    return [
        RankedChunk(chunk=item.chunk, score=item.score, source=item.source, rank=index + 1)
        for index, item in enumerate(fused)
    ]


def rerank(query: str, results: list[RankedChunk]) -> list[RankedChunk]:
    return run_rerank(query, results).results


def run_rerank(query: str, results: list[RankedChunk]) -> RerankRun:
    documents = [item.chunk.content for item in results]
    base_scores = [item.score for item in results]
    try:
        reranker = get_reranker()
        hits = reranker.rerank(query, documents, base_scores)
        return RerankRun(
            results=_apply_rerank_hits(results, hits),
            provider=reranker.provider,
            model=reranker.model,
        )
    except AppError as exc:
        fallback = LexicalReranker()
        hits = fallback.rerank(query, documents, base_scores)
        return RerankRun(
            results=_apply_rerank_hits(results, hits),
            provider=fallback.provider,
            model=fallback.model,
            fallback_used=True,
            fallback_reason=f"rerank_failed:{exc.code}",
        )


def _apply_rerank_hits(results: list[RankedChunk], hits: list[RerankHit]) -> list[RankedChunk]:
    ranked = []
    seen = set()
    for hit in hits:
        if hit.index in seen or hit.index < 0 or hit.index >= len(results):
            continue
        item = results[hit.index]
        seen.add(hit.index)
        ranked.append(
            RankedChunk(
                chunk=item.chunk,
                score=hit.score,
                source=item.source,
                rank=len(ranked) + 1,
            )
        )
    for index, item in enumerate(results):
        if index in seen:
            continue
        ranked.append(
            RankedChunk(
                chunk=item.chunk,
                score=item.score,
                source=item.source,
                rank=len(ranked) + 1,
            )
        )
    return ranked


def make_quote(query: str, content: str, max_chars: int = 240) -> str:
    # 为搜索结果和 citation 生成短摘录；优先截取 query 词附近的片段。
    content = content.strip()
    if len(content) <= max_chars:
        return content
    terms = [term for term in query.split() if term.strip()]
    start = 0
    lowered = content.lower()
    for term in terms:
        found = lowered.find(term.lower())
        if found >= 0:
            start = max(0, found - 60)
            break
    end = min(len(content), start + max_chars)
    return content[start:end].strip()


def ranked_to_search_result(item: RankedChunk) -> SearchResult:
    # 将内部 RankedChunk 转成 API schema，隐藏 ORM 对象细节。
    return SearchResult(
        chunk_id=item.chunk.id,
        document_id=item.chunk.document_id,
        source_filename=item.chunk.source_filename,
        page_number=item.chunk.page_number,
        section_title=item.chunk.section_title,
        content=item.chunk.content,
        quote=make_quote("", item.chunk.content),
        score=float(item.score),
        source=item.source,  # type: ignore[arg-type]
        rank=item.rank,
    )


def ranked_to_log_item(
    item: RankedChunk, extra: Optional[dict[str, object]] = None
) -> dict[str, object]:
    # Log lightweight evidence data without duplicating full chunk content.
    payload = {
        "chunk_id": item.chunk.id,
        "document_id": item.chunk.document_id,
        "source_filename": item.chunk.source_filename,
        "page_number": item.chunk.page_number,
        "section_title": item.chunk.section_title,
        "score": float(item.score),
        "source": item.source,
        "rank": item.rank,
        "quote": make_quote("", item.chunk.content),
    }
    if extra:
        payload.update(extra)
    return payload
