"""Search API endpoints - Hub Search compatible."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.api.app import _get_state
from src.config_weights import weights

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/search", tags=["Search"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class KBFilter(BaseModel):
    """Nested KB filter from dashboard requests."""

    kb_ids: list[str] | None = None


class HubSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    kb_ids: list[str] | None = None
    kb_filter: KBFilter | None = None
    group_id: str | None = None      # 검색 그룹 ID (BU/팀 스코프)
    group_name: str | None = None    # 검색 그룹 이름 (편의용)
    top_k: int = Field(default=5, ge=1, le=50)
    include_answer: bool = True
    stream: bool = False
    mode: str | None = None


class QueryPreprocessInfo(BaseModel):
    """Query preprocessing metadata for the response."""

    corrected_query: str
    original_query: str
    corrections: list[dict[str, Any]] = []


class HubSearchResponse(BaseModel):
    query: str
    answer: str | None = None
    chunks: list[dict[str, Any]] = []
    searched_kbs: list[str] = []
    total_chunks: int = 0
    search_time_ms: float = 0
    query_type: str = ""
    confidence: str = ""
    metadata: dict[str, Any] = {}
    query_preprocess: QueryPreprocessInfo | None = None
    corrected_query: str | None = None
    expanded_terms: list[str] = []
    confidence_level: str = ""
    rerank_applied: bool = False


# ---------------------------------------------------------------------------
# Hub Search endpoint
# ---------------------------------------------------------------------------


@router.post("/hub", response_model=HubSearchResponse)
async def hub_search(request: HubSearchRequest):
    """Hub Search - unified knowledge search with full pipeline."""
    state = _get_state()
    search_engine = state.get("qdrant_search")
    if not search_engine:
        raise HTTPException(status_code=503, detail="Search engine not initialized")

    start = time.time()
    query = request.query.strip()

    # 1. Parse request - handle BOTH kb_ids (flat) and kb_filter.kb_ids (nested)
    # Resolve search scope: group_id/group_name > kb_ids > kb_filter > default
    collections = request.kb_ids or []
    if not collections and request.kb_filter and request.kb_filter.kb_ids:
        collections = request.kb_filter.kb_ids

    # KB Search Group 지원: 그룹으로 스코프 검색
    if not collections and (request.group_id or request.group_name):
        group_repo = state.get("search_group_repo")
        if group_repo:
            collections = await group_repo.resolve_kb_ids(
                group_id=request.group_id,
                group_name=request.group_name,
            )

    if not collections:
        collections = ["knowledge"]

    # 2. QueryPreprocessor - correct typos BEFORE embedding
    corrected_query = query
    preprocess_info: QueryPreprocessInfo | None = None
    preprocessor = state.get("query_preprocessor")
    if preprocessor:
        pp_result = preprocessor.preprocess(query)
        corrected_query = pp_result.corrected_query
        preprocess_info = QueryPreprocessInfo(
            corrected_query=pp_result.corrected_query,
            original_query=pp_result.original_query,
            corrections=[
                {
                    "original": c.original,
                    "corrected": c.corrected,
                    "reason": c.reason,
                }
                for c in pp_result.corrections
            ],
        )

    # 3. Embed the preprocessed query
    embedder = state.get("embedder")
    if not embedder:
        raise HTTPException(status_code=503, detail="Embedding provider not initialized")

    encoded = await asyncio.to_thread(
        lambda: embedder.encode([corrected_query], return_dense=True, return_sparse=True),
    )
    dense_vector = encoded["dense_vecs"][0]
    sparse_weights = encoded["lexical_weights"][0] if encoded.get("lexical_weights") else {}
    sparse_vector = {int(k): float(v) for k, v in sparse_weights.items()} if sparse_weights else None

    # 4. Search across collections in parallel
    all_chunks: list[dict[str, Any]] = []
    searched_kbs: list[str] = []

    async def _search_collection(collection: str) -> tuple[str, list[dict[str, Any]]]:
        results = await search_engine.search(
            kb_id=collection,
            dense_vector=dense_vector,
            sparse_vector=sparse_vector,
            top_k=request.top_k,
        )
        chunks = []
        for r in results:
            chunk_meta = r.metadata or {}
            chunks.append({
                "chunk_id": r.point_id,
                "content": r.content,
                "score": r.score,
                "kb_id": collection,
                "document_name": chunk_meta.get("document_name", ""),
                "source_uri": chunk_meta.get("source_uri", ""),
                "updated_at": chunk_meta.get("last_modified") or chunk_meta.get("updated_at"),
                "is_stale": bool(chunk_meta.get("is_stale", False)),
                "metadata": chunk_meta,
            })
        return collection, chunks

    search_tasks = []
    for collection in collections:
        search_tasks.append(_search_collection(collection))

    search_results_list = await asyncio.gather(*search_tasks, return_exceptions=True)
    for result in search_results_list:
        if isinstance(result, Exception):
            logger.warning("Search in collection failed: %s", result)
            continue
        col_name, chunks = result
        all_chunks.extend(chunks)
        searched_kbs.append(col_name)

    # Sort by score; keep top_k * 3 candidates for reranking, then trim to top_k after
    all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
    all_chunks = all_chunks[: request.top_k * weights.search.rerank_pool_multiplier]

    # 5. CompositeReranker - rerank results with weighted fusion
    rerank_applied = False
    composite_reranker = state.get("composite_reranker")
    if composite_reranker and all_chunks:
        from src.domain.models import SearchChunk

        search_chunks = [
            SearchChunk(
                chunk_id=c.get("chunk_id", ""),
                content=c.get("content", ""),
                score=c.get("score", 0.0),
                kb_id=c.get("kb_id", ""),
                document_name=c.get("document_name", ""),
                metadata=c.get("metadata", {}),
            )
            for c in all_chunks
        ]

        reranked = composite_reranker.rerank(
            query=corrected_query,
            chunks=search_chunks,
            top_k=request.top_k,
        )

        if reranked:
            all_chunks = [
                {
                    "chunk_id": rc.chunk_id,
                    "content": rc.content,
                    "score": rc.score,
                    "rerank_score": rc.score,
                    "kb_id": rc.kb_id,
                    "document_name": rc.document_name,
                    "source_uri": (rc.metadata or {}).get("source_uri", ""),
                    "updated_at": (rc.metadata or {}).get("last_modified")
                    or (rc.metadata or {}).get("updated_at"),
                    "is_stale": bool((rc.metadata or {}).get("is_stale", False)),
                    "metadata": rc.metadata or {},
                }
                for rc in reranked
            ]
            rerank_applied = True

    # Trim to final top_k after reranking
    all_chunks = all_chunks[: request.top_k]

    # 6. Generate answer
    answer = None
    query_type = ""
    confidence = ""
    expanded_terms: list[str] = []

    if request.include_answer and all_chunks:
        # Try TieredResponseGenerator first, fall back to AnswerService
        tiered_gen = state.get("tiered_response_generator")
        llm = state.get("llm")

        if tiered_gen and llm:
            try:
                from src.search.query_classifier import QueryClassifier, QueryType as ClassifierQueryType
                from src.search.tiered_response import RAGContext

                classifier = QueryClassifier()
                classification = classifier.classify(corrected_query)

                rag_context = RAGContext(
                    query=corrected_query,
                    retrieved_chunks=[c.get("content", "") for c in all_chunks],
                    chunk_sources=[
                        {
                            "document_name": c.get("document_name", ""),
                            "source_uri": c.get("source_uri", ""),
                            "score": c.get("score", 0),
                            "metadata": c.get("metadata", {}),
                        }
                        for c in all_chunks
                    ],
                    relevance_scores=[c.get("score", 0.0) for c in all_chunks],
                )

                tiered_result = await tiered_gen.generate(
                    query_type=classification.query_type,
                    context=rag_context,
                )
                answer = tiered_result.content
                query_type = tiered_result.query_type.value
                confidence = (
                    "높음" if tiered_result.confidence >= 0.8
                    else "보통" if tiered_result.confidence >= 0.5
                    else "낮음"
                )
            except Exception as e:
                logger.warning("TieredResponseGenerator failed, falling back to AnswerService: %s", e)
                tiered_gen = None  # fall through to AnswerService

        if answer is None:
            # Fallback to AnswerService
            answer_service = state.get("answer_service")
            if llm:
                from src.search.answer_service import AnswerService

                if not answer_service:
                    answer_service = AnswerService(llm_client=llm)
                    state["answer_service"] = answer_service

                result = await answer_service.enrich(corrected_query, all_chunks)
                answer = result.answer
                query_type = result.query_type
                confidence = result.confidence_indicator

    elapsed = (time.time() - start) * 1000

    return HubSearchResponse(
        query=query,
        answer=answer,
        chunks=all_chunks,
        searched_kbs=searched_kbs,
        total_chunks=len(all_chunks),
        search_time_ms=round(elapsed, 1),
        query_type=query_type,
        confidence=confidence,
        corrected_query=corrected_query if corrected_query != query else None,
        expanded_terms=expanded_terms,
        confidence_level=confidence,
        rerank_applied=rerank_applied,
        query_preprocess=preprocess_info,
        metadata={
            "corrected_query": corrected_query,
            "rerank_applied": rerank_applied,
            "search_time_ms": round(elapsed, 1),
        },
    )


@router.get("/hub/kbs")
async def list_searchable_kbs():
    """List searchable knowledge bases."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    if not collections:
        return {"kbs": []}

    try:
        names = await collections.get_existing_collection_names()
        return {"kbs": [{"kb_id": n, "name": n} for n in names]}
    except Exception as e:
        logger.warning("KB list failed: %s", e)
        return {"kbs": []}
