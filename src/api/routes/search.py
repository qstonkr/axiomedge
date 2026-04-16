"""Search API endpoints - Hub Search compatible."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.api.app import _get_state
from src.api.routes.metrics import inc as metrics_inc
from src.config_weights import weights
from src.search.answer_guard import AnswerGuard
from src.search.passage_cleaner import clean_chunks
from src.search.cross_encoder_reranker import async_rerank_with_cross_encoder

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/search", tags=["Search"])




# Module-level singletons (avoid per-request allocation)
_answer_guard = AnswerGuard()


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
    document_filter: list[str] | None = None  # 문서명 필터 (특정 문서만 검색)
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
    # Typed top-level fields (promoted from metadata for type safety)
    crag_action: str | None = None
    crag_confidence: float | None = None
    conflicts: list[dict[str, Any]] | None = None
    follow_up_questions: list[str] | None = None
    transparency: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Hub Search endpoint
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------

# Step functions → _search_steps.py
from src.api.routes._search_steps import (  # noqa: E402
    _step_cache_check,
    _step_resolve_collections,
    _step_preprocess,
    _step_expand_query,
    _step_classify_query,
    _step_embed,
    _step_search_collections,
    _step_keyword_fallback,
    _step_composite_rerank,
    _step_week_match_guarantee,
    _step_generate_answer,
    _step_detect_conflicts,
    _step_follow_ups,
    _step_build_transparency,
    _step_cache_store,
    _step_search_enrichment,
    _step_tree_expand,
    _step_apply_trust_and_freshness,
    _step_graph_expand,
    _step_crag_evaluate,
    _step_log_usage,
)



# ---------------------------------------------------------------------------
# Hub Search endpoint
# ---------------------------------------------------------------------------


@router.post("/hub", response_model=HubSearchResponse, responses={503: {"description": "Search engine or embedding provider not initialized"}})
async def hub_search(request: HubSearchRequest):
    """Hub Search - unified knowledge search with full pipeline."""
    state = _get_state()
    search_engine = state.get("qdrant_search")
    if not search_engine:
        raise HTTPException(status_code=503, detail="Search engine not initialized")

    start = time.time()
    query = request.query.strip()
    metrics_inc("search_requests")

    # 0. Cache check
    cache_collections = request.kb_ids or []
    if not cache_collections and request.kb_filter and request.kb_filter.kb_ids:
        cache_collections = request.kb_filter.kb_ids
    if not cache_collections:
        cache_collections = ["knowledge"]

    cached = await _step_cache_check(query, state, cache_collections, request.top_k, start)
    if cached:
        return cached
    metrics_inc("search_cache_misses")

    # 1. Resolve collections
    collections = await _step_resolve_collections(request, state)

    # 2. Preprocess query
    corrected_query, preprocess_info = _step_preprocess(query, state)

    # 2b. Query expansion
    search_query, display_query, expanded_terms = await _step_expand_query(
        corrected_query, collections, state,
    )

    # 2.5. Query classification
    effective_top_k, _dense_w, _sparse_w = _step_classify_query(
        display_query, request.top_k, state,
    )

    # 3. Embed query
    dense_vector, sparse_vector, colbert_vectors = await _step_embed(search_query, state)

    # 4. Search collections
    _qdrant_top_k = effective_top_k * weights.search.rerank_pool_multiplier
    all_chunks, searched_kbs = await _step_search_collections(
        search_engine, collections, dense_vector, sparse_vector,
        colbert_vectors, _qdrant_top_k, request.document_filter,
    )

    # 4.3. Keyword fallback
    from src.config import get_settings as _get_settings
    _qdrant_url = state.get("qdrant_url") or _get_settings().qdrant.url
    all_chunks = await _step_keyword_fallback(display_query, all_chunks, collections, _qdrant_url)

    # 4.35-4.46. Search enrichment pipeline
    all_chunks = await _step_search_enrichment(
        display_query, all_chunks, collections, _qdrant_url, effective_top_k,
    )

    # 4.5-4.6. Passage cleaning + cross-encoder reranking
    all_chunks = clean_chunks(all_chunks)
    try:
        all_chunks = await async_rerank_with_cross_encoder(
            query=search_query, chunks=all_chunks,
            top_k=effective_top_k * weights.search.rerank_pool_multiplier,
        )
    except Exception as ce_err:  # noqa: BLE001
        logger.warning("Cross-encoder reranking skipped: %s", ce_err)

    # 5. Composite reranking + week-match guarantee
    all_chunks, rerank_applied, search_chunks = _step_composite_rerank(
        search_query, all_chunks, effective_top_k, state,
    )
    all_chunks = all_chunks[:effective_top_k]
    all_chunks = _step_week_match_guarantee(all_chunks, rerank_applied, search_chunks)

    # 5.5 Tree context expansion (형제 청크 확장 + 섹션 제목 검색)
    all_chunks = await _step_tree_expand(
        display_query, all_chunks, collections, state,
    )

    # 5.6 Trust score + freshness (feature-gated)
    all_chunks = _step_apply_trust_and_freshness(all_chunks, state)

    # 6. Graph expansion
    all_chunks = await _step_graph_expand(
        display_query, all_chunks, collections, state, _qdrant_url,
    )

    # 7-9. CRAG + answer + guard + conflicts + follow-ups + transparency
    crag_evaluation = await _step_crag_evaluate(display_query, all_chunks, start, state)
    answer, query_type, confidence = await _step_generate_answer(
        display_query, all_chunks, crag_evaluation, request.include_answer, state,
    )
    if answer:
        answer = _answer_guard.guard(answer, all_chunks, display_query)
    conflicts = _step_detect_conflicts(all_chunks, searched_kbs)
    follow_ups = await _step_follow_ups(
        display_query, answer, all_chunks, request.include_answer, state,
    )
    transparency = _step_build_transparency(answer, query_type, confidence)

    elapsed = (time.time() - start) * 1000
    await _step_log_usage(
        query, display_query, all_chunks, elapsed, collections,
        request, answer, follow_ups, rerank_applied, state,
        crag_evaluation=crag_evaluation,
    )

    response = HubSearchResponse(
        query=query, answer=answer, chunks=all_chunks,
        searched_kbs=searched_kbs, total_chunks=len(all_chunks),
        search_time_ms=round(elapsed, 1), query_type=query_type,
        confidence=confidence,
        display_query=display_query if display_query != query else None,
        expanded_terms=expanded_terms, confidence_level=confidence,
        rerank_applied=rerank_applied, query_preprocess=preprocess_info,
        crag_action=crag_evaluation.action.value if crag_evaluation else None,
        crag_confidence=crag_evaluation.confidence_score if crag_evaluation else None,
        conflicts=conflicts or None,
        follow_up_questions=follow_ups or None,
        transparency=transparency,
        metadata={
            "display_query": corrected_query,
            "rerank_applied": rerank_applied,
            "search_time_ms": round(elapsed, 1),
            **({"crag_action": crag_evaluation.action.value,
                "crag_confidence": crag_evaluation.confidence_score,
                "crag_recommendation": crag_evaluation.recommendation,
                } if crag_evaluation else {}),
            **({"conflicts": conflicts} if conflicts else {}),
            **({"follow_up_questions": follow_ups} if follow_ups else {}),
        },
    )

    await _step_cache_store(query, response, collections, effective_top_k, state)
    return response


@router.get("/hub/kbs")
async def list_searchable_kbs():
    """List searchable knowledge bases."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    if not collections:
        return {"kbs": []}

    try:
        names = await collections.get_existing_collection_names()
        # Strip "kb_" prefix from collection names to get logical kb_id
        kbs = []
        for n in names:
            kb_id = n[3:].replace("_", "-") if n.startswith("kb_") else n
            kbs.append({"kb_id": kb_id, "name": kb_id, "collection": n})
        return {"kbs": kbs}
    except Exception as e:  # noqa: BLE001
        logger.warning("KB list failed: %s", e)
        return {"kbs": []}
