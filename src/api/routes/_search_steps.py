"""Search pipeline step functions — extracted from search.py.

Each function implements one step of the hub_search pipeline.
Imported by search.py for hub_search orchestration.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from src.api.routes.metrics import inc as metrics_inc
from src.config.weights import weights as _w  # alias for timeout refs
from src.config.weights import weights
from src.domain.models import SearchChunk
from src.search.crag_evaluator import RetrievalAction
from src.search.transparency_formatter import SourceType, TransparencyFormatter
from src.search.trust_score_service import SOURCE_CREDIBILITY

# Lazy imports to avoid circular dependency with search.py
TYPE_CHECKING = False
if TYPE_CHECKING:
    from src.api.routes.search import HubSearchRequest, HubSearchResponse

logger = logging.getLogger(__name__)


# ── KiwiPy morpheme-based keyword extraction (singleton) ──
_kiwi_instance = None
_NOUN_TAGS = frozenset({"NNG", "NNP", "SL", "SH"})


def _extract_query_keywords(query: str) -> list[str]:
    """Extract meaningful keywords using KiwiPy morphological analysis."""
    global _kiwi_instance
    if _kiwi_instance is None:
        try:
            from kiwipiepy import Kiwi
            _kiwi_instance = Kiwi()
        except ImportError:
            return [t.strip() for t in query.lower().split() if len(t.strip()) >= 2]
    try:
        tokens = _kiwi_instance.tokenize(query)
        keywords = [tok.form for tok in tokens if tok.tag in _NOUN_TAGS and len(tok.form) >= 2]
        return keywords if keywords else [t.strip() for t in query.lower().split() if len(t.strip()) >= 2]
    except Exception:  # noqa: BLE001
        return [t.strip() for t in query.lower().split() if len(t.strip()) >= 2]


async def _step_cache_check(
    query: str,
    state: dict[str, Any],
    cache_collections: list[str],
    top_k: int,
    start: float,
) -> HubSearchResponse | None:
    """Step 0: Check multi-layer and legacy caches."""
    expected_version = weights.cache.cache_version

    multi_cache = state.get("multi_layer_cache")
    if multi_cache:
        result = await _check_multi_layer_cache(
            multi_cache, query, cache_collections, top_k, expected_version, start,
        )
        if result:
            return result

    search_cache = state.get("search_cache")
    if search_cache:
        result = await _check_legacy_cache(
            search_cache, query, cache_collections, top_k, expected_version, start,
        )
        if result:
            return result

    return None


async def _resolve_collections_from_qdrant(state: dict[str, Any]) -> list[str]:
    """Fallback: list all non-test collections from Qdrant."""
    qdrant_collections = state.get("qdrant_collections")
    if not qdrant_collections:
        return ["knowledge"]
    try:
        all_names = await qdrant_collections.get_existing_collection_names()
        collections = [
            n[3:].replace("_", "-") if n.startswith("kb_") else n
            for n in all_names
            if not n.startswith("kb_test")
        ]
        return collections if collections else ["knowledge"]
    except Exception:  # noqa: BLE001
        return ["knowledge"]


async def _filter_by_kb_registry(
    collections: list[str], state: dict[str, Any],
) -> list[str]:
    """Filter collections by KB registry active status (cached)."""
    from src.api.routes.search_helpers import get_active_kb_ids
    kb_registry = state.get("kb_registry")
    if not kb_registry or collections == ["knowledge"]:
        return collections
    try:
        active_kb_ids = await get_active_kb_ids(kb_registry)
        filtered = [c for c in collections if c in active_kb_ids]
        return filtered if filtered else collections
    except Exception as e:  # noqa: BLE001
        logger.warning("KB registry filter failed, using unfiltered collections: %s", e)
        return collections


async def _step_resolve_collections(
    request: HubSearchRequest,
    state: dict[str, Any],
) -> list[str]:
    """Step 1: Resolve KB collections from request params."""
    collections = request.kb_ids or []
    if not collections and request.kb_filter and request.kb_filter.kb_ids:
        collections = request.kb_filter.kb_ids

    if not collections and (request.group_id or request.group_name):
        group_repo = state.get("search_group_repo")
        if group_repo:
            collections = await group_repo.resolve_kb_ids(
                group_id=request.group_id,
                group_name=request.group_name,
            )

    if not collections:
        collections = await _resolve_collections_from_qdrant(state)

    collections = await _filter_by_kb_registry(collections, state)
    return collections


def _step_preprocess(
    query: str,
    state: dict[str, Any],
) -> tuple[str, Any]:
    """Step 2: Preprocess query (typo correction)."""
    preprocessor = state.get("query_preprocessor")
    if not preprocessor:
        return query, None

    from src.api.routes.search import QueryPreprocessInfo as _QPI
    pp_result = preprocessor.preprocess(query)
    preprocess_info = _QPI(
        corrected_query=pp_result.corrected_query,
        original_query=pp_result.original_query,
        corrections=[
            {"original": c.original, "corrected": c.corrected, "reason": c.reason}
            for c in pp_result.corrections
        ],
    )
    return pp_result.corrected_query, preprocess_info


async def _step_expand_query(
    corrected_query: str,
    collections: list[str],
    state: dict[str, Any],
) -> tuple[str, str, list[str]]:
    """Step 2b: Query expansion. Returns (search_query, display_query, expanded_terms)."""
    expanded_terms: list[str] = []
    search_query = corrected_query
    display_query = corrected_query

    query_expander = state.get("query_expander")
    if query_expander:
        try:
            first_kb = collections[0] if collections else "knowledge"
            expansion_result = await query_expander.expand_query(first_kb, corrected_query)
            expanded_terms = getattr(expansion_result, "expanded_terms", [])
            expanded_q = getattr(expansion_result, "expanded_query", None)
            if expanded_q and expanded_q != corrected_query:
                search_query = expanded_q
        except Exception as e:  # noqa: BLE001
            logger.warning("Query expansion failed: %s", e)

    return search_query, display_query, expanded_terms


def _step_classify_query(
    display_query: str,
    top_k: int,
    state: dict[str, Any],
) -> tuple[int, float, float]:
    """Step 2.5: Classify query type and adjust weights. Returns (effective_top_k, dense_w, sparse_w)."""
    import re as _re_dq

    effective_top_k = top_k
    _dense_w = weights.hybrid_search.dense_weight
    _sparse_w = weights.hybrid_search.sparse_weight

    classifier = state.get("query_classifier")
    if classifier:
        try:
            query_classification = classifier.classify(display_query)
            qtype = query_classification.query_type.value
            if qtype == "owner_query":
                effective_top_k = max(effective_top_k, 10)
            elif qtype == "concept":
                effective_top_k = max(effective_top_k, 8)
                _dense_w = weights.hybrid_search.concept_dense_weight
                _sparse_w = weights.hybrid_search.concept_sparse_weight
            elif qtype in ("procedure", "troubleshoot"):
                _dense_w = weights.hybrid_search.procedure_dense_weight
                _sparse_w = weights.hybrid_search.procedure_sparse_weight
        except Exception as e:  # noqa: BLE001
            logger.debug("Query type weight adjustment failed: %s", e)

    if _re_dq.search(r"20\d{2}년\s*\d{1,2}월|20\d{2}[_\-]\d{2}|\d{1,2}월\s*\d주차", display_query):
        _dense_w = weights.hybrid_search.date_query_dense_weight
        _sparse_w = weights.hybrid_search.date_query_sparse_weight

    return effective_top_k, _dense_w, _sparse_w


async def _step_embed(
    search_query: str,
    state: dict[str, Any],
) -> tuple[list[float], dict[int, float] | None, list | None]:
    """Step 3: Embed query. Returns (dense_vector, sparse_vector, colbert_vectors)."""
    embedder = state.get("embedder")
    if not embedder:
        raise HTTPException(status_code=503, detail="Embedding provider not initialized")

    colbert_enabled = weights.hybrid_search.enable_colbert_reranking
    encoded = await asyncio.to_thread(
        lambda: embedder.encode(
            [search_query],
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=colbert_enabled,
        ),
    )
    dense_vector = encoded["dense_vecs"][0]
    sparse_weights_raw = encoded["lexical_weights"][0] if encoded.get("lexical_weights") else {}
    sparse_vector = (
        {int(k): float(v) for k, v in sparse_weights_raw.items()}
        if sparse_weights_raw else None
    )
    colbert_vectors = (
        encoded["colbert_vecs"][0]
        if colbert_enabled and encoded.get("colbert_vecs")
        else None
    )
    return dense_vector, sparse_vector, colbert_vectors


def _build_chunks_from_results(
    results: list[Any],
    collection: str,
    document_filter: list[str] | None,
) -> list[dict[str, Any]]:
    """Convert search results to chunk dicts, applying optional document filter."""
    chunks = []
    for r in results:
        chunk_meta = r.metadata or {}
        doc_name = chunk_meta.get("document_name", "")
        if document_filter and not any(
            f.lower() in doc_name.lower() for f in document_filter
        ):
            continue
        chunks.append({
            "chunk_id": r.point_id, "content": r.content,
            "score": r.score, "kb_id": collection,
            "document_name": doc_name,
            "source_uri": chunk_meta.get("source_uri", ""),
            "updated_at": chunk_meta.get("last_modified") or chunk_meta.get("updated_at"),
            "is_stale": bool(chunk_meta.get("is_stale", False)),
            "metadata": chunk_meta,
        })
    return chunks


async def _step_search_collections(
    search_engine: Any,
    collections: list[str],
    dense_vector: list[float],
    sparse_vector: dict[int, float] | None,
    colbert_vectors: list | None,
    qdrant_top_k: int,
    document_filter: list[str] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Step 4: Search across collections in parallel."""
    colbert_enabled = weights.hybrid_search.enable_colbert_reranking

    async def _search_one(collection: str) -> tuple[str, list[dict[str, Any]]]:
        if colbert_enabled and colbert_vectors:
            results = await search_engine.search_with_colbert_rerank(
                kb_id=collection, dense_vector=dense_vector,
                sparse_vector=sparse_vector, colbert_vectors=colbert_vectors,
                top_k=qdrant_top_k,
            )
        else:
            results = await search_engine.search(
                kb_id=collection, dense_vector=dense_vector,
                sparse_vector=sparse_vector, top_k=qdrant_top_k,
            )
        chunks = _build_chunks_from_results(results, collection, document_filter)
        return collection, chunks

    tasks = [_search_one(c) for c in collections]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    all_chunks: list[dict[str, Any]] = []
    searched_kbs: list[str] = []
    for result in results_list:
        if isinstance(result, Exception):
            logger.warning("Search in collection failed: %s", result)
            continue
        col_name, chunks = result
        all_chunks.extend(chunks)
        searched_kbs.append(col_name)
    return all_chunks, searched_kbs


async def _step_keyword_fallback(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    collections: list[str],
    qdrant_url: str,
) -> list[dict[str, Any]]:
    """Step 4.3: Keyword fallback if main keywords missing from results."""
    kw_tokens = _extract_query_keywords(display_query)
    if not kw_tokens or not all_chunks:
        return all_chunks

    has_match = any(
        any(t in c.get("content", "").lower() for t in kw_tokens)
        for c in all_chunks[:20]
    )
    if has_match:
        return all_chunks

    import httpx
    kw_primary = kw_tokens[0]

    async def _scroll_one(client: httpx.AsyncClient, coll: str) -> list[dict[str, Any]]:
        """Scroll one collection for the keyword — isolated so gather can parallelize."""
        coll_name = f"kb_{coll.replace('-', '_')}"
        try:
            resp = await client.post(
                f"{qdrant_url}/collections/{coll_name}/points/scroll",
                json={
                    "limit": 5, "with_payload": True, "with_vector": False,
                    "filter": {"should": [
                        {"key": "morphemes", "match": {"text": kw_primary}},
                        {"key": "content", "match": {"text": kw_primary}},
                    ]},
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Keyword fallback scroll failed (coll=%s): %s", coll, e)
            return []
        if resp.status_code != 200:
            return []
        chunks: list[dict[str, Any]] = []
        for pt in resp.json().get("result", {}).get("points", []):
            pay = pt.get("payload", {})
            chunks.append({
                "chunk_id": str(pt["id"]),
                "content": pay.get("content", ""),
                "score": 0.5,
                "kb_id": coll,
                "document_name": pay.get("document_name", ""),
                "source_uri": pay.get("source_uri", ""),
                "metadata": pay,
                "_keyword_fallback": True,
            })
        return chunks

    try:
        async with httpx.AsyncClient(timeout=_w.timeouts.httpx_search_scroll) as client:
            # 컬렉션들을 병렬로 scroll — 이전엔 직렬 loop 였고, collection 수 × 개별
            # 타임아웃만큼 latency 가 쌓였음. asyncio.gather 로 max(요청시간)으로 축소.
            per_collection_chunks = await asyncio.gather(
                *(_scroll_one(client, coll) for coll in collections),
                return_exceptions=False,
            )
        for chunks in per_collection_chunks:
            all_chunks.extend(chunks)
        logger.info(
            "Keyword fallback: injected %d chunks across %d collections for '%s'",
            sum(len(c) for c in per_collection_chunks), len(collections), kw_primary,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("Keyword fallback search failed: %s", e)

    return all_chunks


def _step_composite_rerank(
    search_query: str,
    all_chunks: list[dict[str, Any]],
    effective_top_k: int,
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, list[SearchChunk]]:
    """Step 5: Composite reranking. Returns (reranked_chunks, rerank_applied, search_chunks)."""
    composite_reranker = state.get("composite_reranker")
    if not composite_reranker or not all_chunks:
        return all_chunks, False, []

    for c in all_chunks:
        if c.get("_week_matched"):
            meta = c.get("metadata") or {}
            meta["_week_matched"] = True
            c["metadata"] = meta

    search_chunks = [
        SearchChunk(
            chunk_id=c.get("chunk_id", ""), content=c.get("content", ""),
            score=c.get("score", 0.0), kb_id=c.get("kb_id", ""),
            document_name=c.get("document_name", ""), metadata=c.get("metadata", {}),
        )
        for c in all_chunks
    ]

    reranked = composite_reranker.rerank(
        query=search_query, chunks=search_chunks, top_k=effective_top_k,
    )
    if not reranked:
        return all_chunks, False, search_chunks

    result_chunks = [
        {
            "chunk_id": rc.chunk_id, "content": rc.content,
            "score": rc.score, "rerank_score": rc.score,
            "kb_id": rc.kb_id, "document_name": rc.document_name,
            "source_uri": (rc.metadata or {}).get("source_uri", ""),
            "updated_at": (rc.metadata or {}).get("last_modified")
            or (rc.metadata or {}).get("updated_at"),
            "is_stale": bool((rc.metadata or {}).get("is_stale", False)),
            "metadata": rc.metadata or {},
        }
        for rc in reranked
    ]
    return result_chunks, True, search_chunks


def _step_week_match_guarantee(
    all_chunks: list[dict[str, Any]],
    rerank_applied: bool,
    search_chunks: list[SearchChunk],
) -> list[dict[str, Any]]:
    """Step 5.1: Ensure at least 1 week-matched chunk survives reranking."""
    has_week = any(
        c.get("_week_matched") or (c.get("metadata") or {}).get("_week_matched")
        for c in all_chunks
    )
    if has_week or not rerank_applied or not search_chunks:
        return all_chunks

    week_candidates = [
        sc for sc in search_chunks if (sc.metadata or {}).get("_week_matched")
    ]
    if not week_candidates:
        return all_chunks

    best_wk = max(week_candidates, key=lambda sc: sc.score)
    all_chunks[-1] = {
        "chunk_id": best_wk.chunk_id, "content": best_wk.content,
        "score": best_wk.score, "rerank_score": best_wk.score,
        "kb_id": best_wk.kb_id, "document_name": best_wk.document_name,
        "source_uri": (best_wk.metadata or {}).get("source_uri", ""),
        "updated_at": (best_wk.metadata or {}).get("last_modified")
        or (best_wk.metadata or {}).get("updated_at"),
        "is_stale": bool((best_wk.metadata or {}).get("is_stale", False)),
        "metadata": best_wk.metadata or {},
        "_week_matched": True,
    }
    logger.info("Week-match guarantee: pinned doc '%s' into final top-k", best_wk.document_name)
    return all_chunks


async def _step_generate_answer(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    crag_evaluation: Any,
    include_answer: bool,
    state: dict[str, Any],
) -> tuple[str | None, str, str]:
    """Step 8: Generate answer. Returns (answer, query_type, confidence)."""
    if crag_evaluation and crag_evaluation.action == RetrievalAction.INCORRECT:
        return crag_evaluation.recommendation, "", crag_evaluation.confidence_level.value

    if crag_evaluation and crag_evaluation.confidence_score < weights.search.crag_block_threshold:
        return (
            "검색 결과의 신뢰도가 낮아 정확한 답변을 제공하기 어렵습니다. 질문을 더 구체적으로 해주세요.",
            "", "낮음",
        )

    if not include_answer or not all_chunks:
        return None, "", ""

    answer = await _try_tiered_generation(display_query, all_chunks, state)
    if answer is not None:
        return answer

    # Fallback to AnswerService
    answer_service = state.get("answer_service")
    if answer_service:
        result = await answer_service.enrich(display_query, all_chunks)
        return result.answer, result.query_type, result.confidence_indicator

    return None, "", ""


async def _try_tiered_generation(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    state: dict[str, Any],
) -> tuple[str, str, str] | None:
    """Try TieredResponseGenerator. Returns (answer, query_type, confidence) or None."""
    tiered_gen = state.get("tiered_response_generator")
    llm = state.get("llm")
    if not tiered_gen or not llm:
        return None

    try:
        from src.search.query_classifier import QueryClassifier
        from src.search.tiered_response import RAGContext

        classifier = state.get("query_classifier") or QueryClassifier()
        classification = classifier.classify(display_query)

        rag_context = RAGContext(
            query=display_query,
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
            query_type=classification.query_type, context=rag_context,
        )

        if tiered_result.confidence >= weights.search.confidence_display_high:
            confidence = "높음"
        elif tiered_result.confidence >= weights.search.confidence_display_medium:
            confidence = "보통"
        else:
            confidence = "낮음"
        return tiered_result.content, tiered_result.query_type.value, confidence
    except Exception as e:  # noqa: BLE001
        logger.warning("TieredResponseGenerator failed, falling back to AnswerService: %s", e)
        return None


def _check_kb_pair_conflict(
    kb_a: str, kb_b: str,
    kb_answers: dict[str, list[str]],
    threshold: float,
) -> dict[str, Any] | None:
    """Check if two KBs have conflicting answers based on word overlap."""
    words_a = set(" ".join(kb_answers[kb_a][:3]).split())
    words_b = set(" ".join(kb_answers[kb_b][:3]).split())
    if not words_a or not words_b:
        return None
    overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
    if overlap >= threshold:
        return None
    return {
        "kb_a": kb_a, "kb_b": kb_b,
        "overlap_ratio": round(overlap, 3),
        "warning": f"KB '{kb_a}'와 '{kb_b}'의 답변이 상이할 수 있습니다.",
    }


def _step_detect_conflicts(
    all_chunks: list[dict[str, Any]],
    searched_kbs: list[str],
) -> list[dict[str, Any]]:
    """Step 9.5: Detect contradictory answers from different KBs."""
    if len(searched_kbs) <= 1 or not all_chunks:
        return []

    kb_answers: dict[str, list[str]] = {}
    for c in all_chunks[:10]:
        kb = c.get("kb_id", "")
        content = c.get("content", "")[:200]
        if kb and content:
            kb_answers.setdefault(kb, []).append(content)

    if len(kb_answers) <= 1:
        return []

    conflicts: list[dict[str, Any]] = []
    kb_list = list(kb_answers.keys())
    threshold = weights.search.conflict_overlap_threshold
    for i in range(len(kb_list)):
        for j in range(i + 1, len(kb_list)):
            conflict = _check_kb_pair_conflict(
                kb_list[i], kb_list[j], kb_answers, threshold,
            )
            if conflict:
                conflicts.append(conflict)
    return conflicts


async def _step_follow_ups(
    display_query: str,
    answer: str | None,
    all_chunks: list[dict[str, Any]],
    include_answer: bool,
    state: dict[str, Any],
) -> list[str]:
    """Step 9.6: Generate follow-up questions."""
    if not include_answer or not answer or not all_chunks:
        return []
    try:
        llm = state.get("llm")
        if not llm:
            return []
        prompt = (
            f"다음 질문과 답변을 바탕으로, 사용자가 추가로 궁금해할 수 있는 후속 질문 3개를 생성하세요.\n"
            f"각 질문은 한 줄씩, 번호 없이 작성하세요.\n\n"
            f"질문: {display_query}\n답변: {answer[:500]}\n\n후속 질문:"
        )
        result = await llm.generate(prompt, temperature=0.3, max_tokens=200)
        if result:
            return [q.strip().lstrip("- ·•123.") for q in result.strip().split("\n") if q.strip()][:3]
    except Exception as e:  # noqa: BLE001
        logger.debug("Follow-up generation skipped: %s", e)
    return []


# Map hub_search query_type → TransparencyFormatter SourceType
_QUERY_TYPE_TO_SOURCE: dict[str, SourceType] = {
    "factual": SourceType.DOCUMENT,
    "analytical": SourceType.INFERENCE,
    "advisory": SourceType.GENERAL,
}
# Map Korean confidence labels → TransparencyFormatter confidence keys
_CONFIDENCE_KO_TO_EN: dict[str, str] = {
    "높음": "high",
    "보통": "medium",
    "낮음": "low",
}


def _step_build_transparency(
    answer: str | None,
    query_type: str,
    confidence: str,
) -> dict[str, Any] | None:
    """Step 9.5: Build transparency metadata for the response.

    Feature-gated via SEARCH_TRANSPARENCY_ENABLED (default: true).
    Reuses TransparencyFormatter constants for label consistency.
    """
    if os.environ.get("SEARCH_TRANSPARENCY_ENABLED", "true").lower() == "false":
        return None
    if not answer:
        return None

    source_type = _QUERY_TYPE_TO_SOURCE.get(query_type, SourceType.DOCUMENT)
    source_label = TransparencyFormatter.SOURCE_LABELS.get(source_type, "")
    confidence_key = _CONFIDENCE_KO_TO_EN.get(confidence, "")
    confidence_indicator = TransparencyFormatter.CONFIDENCE_INDICATORS.get(confidence_key, "")

    return {
        "source_type": source_type.value,
        "source_label": source_label,
        "confidence_indicator": confidence_indicator,
    }


async def _step_cache_store(
    query: str,
    response: HubSearchResponse,
    collections: list[str],
    effective_top_k: int,
    state: dict[str, Any],
) -> None:
    """Store response in caches (fire-and-forget)."""
    if not response.answer or any(p in response.answer for p in _ERROR_PATTERNS):
        return

    response_dict = response.model_dump()
    response_dict["_cache_version"] = weights.cache.cache_version

    multi_cache = state.get("multi_layer_cache")
    if multi_cache:
        try:
            from src.cache.cache_types import CacheDomain
            await multi_cache.set(
                query, response_dict, domain=CacheDomain.KB_SEARCH,
                metadata={"kb_ids": collections},
                kb_ids=collections, top_k=effective_top_k,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to write multi-layer cache: %s", e)

    search_cache = state.get("search_cache")
    if search_cache:
        try:
            await search_cache.set(query, collections, response_dict, effective_top_k)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to write search cache: %s", e)


async def _step_search_enrichment(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    collections: list[str],
    qdrant_url: str,
    effective_top_k: int,
) -> list[dict[str, Any]]:
    """Steps 4.35-4.46: identifier, keyword boost, diversity, date, week."""
    from src.api.routes.search_helpers import (
        identifier_search, keyword_boost, document_diversity,
        date_filter_search, week_name_search,
    )

    all_chunks = await identifier_search(display_query, all_chunks, collections, qdrant_url)

    _query_tokens = _extract_query_keywords(display_query)
    _pool_size = effective_top_k * weights.search.rerank_pool_multiplier

    all_chunks = keyword_boost(
        all_chunks, _query_tokens, collections, _pool_size, weights.search.keyword_boost_weight,
    )
    all_chunks = document_diversity(all_chunks, _pool_size)
    all_chunks = await date_filter_search(
        display_query, all_chunks, collections, qdrant_url, _pool_size,
    )
    all_chunks = await week_name_search(
        display_query, all_chunks, collections, qdrant_url, _pool_size,
    )
    return all_chunks


async def _step_tree_expand(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    collections: list[str],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Step 5.5: Tree context expansion (형제 확장 + 섹션 제목 검색)."""
    from src.config import get_settings
    tree_settings = get_settings().tree_index
    if not tree_settings.enabled or not all_chunks:
        return all_chunks

    graph_repo = state.get("graph_repo")
    if not graph_repo:
        return all_chunks

    try:
        from src.search.tree_context_expander import (
            expand_siblings, search_by_section_titles,
        )

        chunk_ids = [c.get("chunk_id", "") for c in all_chunks if c.get("chunk_id")]
        chunk_scores = {c.get("chunk_id", ""): c.get("score", 0.0) for c in all_chunks}
        kb_id = collections[0] if len(collections) == 1 else None

        # 5.5a+b: 형제 확장 + 섹션 제목 검색 (병렬)
        # 섹션 보너스는 composite_reranker._apply_section_bonus에서 처리
        coros = [
            expand_siblings(
                chunk_ids, chunk_scores, graph_repo,
                window=tree_settings.sibling_window,
                max_per_hit=tree_settings.max_tree_chunks_per_hit,
                score_decay=tree_settings.sibling_score_decay,
                max_total_chars=tree_settings.max_context_chars,
            ),
            search_by_section_titles(
                display_query, graph_repo,
                kb_id=kb_id, existing_chunk_ids=set(chunk_ids),
            ) if tree_settings.section_title_search else asyncio.sleep(0),
        ]
        siblings_result, section_result = await asyncio.gather(*coros)

        siblings = siblings_result if isinstance(siblings_result, list) else []
        section_hits = section_result if isinstance(section_result, list) else []

        # 확장 청크를 Qdrant에서 로드하여 결과에 추가
        expanded_ids = [s.chunk_id for s in siblings] + [s.chunk_id for s in section_hits]
        if expanded_ids:
            expanded_scores = {s.chunk_id: s.score for s in (*siblings, *section_hits)}
            from src.pipeline.qdrant_utils import str_to_uuid
            from src.api.routes.search_helpers import retrieve_chunks_by_ids
            point_ids = [str_to_uuid(eid) for eid in expanded_ids if eid]
            loaded = await retrieve_chunks_by_ids(
                state.get("qdrant_client"), collections, point_ids, expanded_scores,
            )
            all_chunks.extend(loaded)

        logger.info(
            "Tree expansion: siblings=%d, section_hits=%d",
            len(siblings), len(section_hits),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Tree context expansion failed: %s", e)

    return all_chunks


def _parse_datetime_safe(value: Any) -> datetime | None:
    """Parse a datetime value from string or passthrough, returning None on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
    return None


def _step_apply_trust_and_freshness(
    all_chunks: list[dict[str, Any]],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Step 5.6: Attach KTS trust score and freshness score to chunk metadata.

    Feature-gated via SEARCH_KTS_ENABLED / SEARCH_FRESHNESS_ENABLED env vars.
    Scores are informational (metadata only) — no reranking impact yet.
    """
    kts_enabled = os.environ.get("SEARCH_KTS_ENABLED", "true").lower() != "false"
    freshness_enabled = os.environ.get("SEARCH_FRESHNESS_ENABLED", "true").lower() != "false"

    if not kts_enabled and not freshness_enabled:
        return all_chunks

    freshness_predictor = state.get("freshness_predictor") if freshness_enabled else None

    for chunk in all_chunks:
        meta = chunk.get("metadata") or {}

        if freshness_predictor:
            raw_date = meta.get("last_modified") or meta.get("updated_at") or chunk.get("updated_at")
            updated_at = _parse_datetime_safe(raw_date)
            if updated_at:
                doc_type = meta.get("doc_type", "general") or "general"
                meta["freshness_score"] = round(freshness_predictor.score(updated_at, doc_type), 4)

        if kts_enabled:
            source_type = meta.get("source_type", "auto_extracted")
            meta["kts_source_credibility"] = SOURCE_CREDIBILITY.get(source_type, 0.0)

        chunk["metadata"] = meta

    return all_chunks


async def _step_graph_expand(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    collections: list[str],
    state: dict[str, Any],
    qdrant_url: str,
) -> list[dict[str, Any]]:
    """Step 6: Graph expansion."""
    graph_expander = state.get("graph_expander")
    logger.info(
        "Graph expander: %s, chunks: %d",
        type(graph_expander).__name__ if graph_expander else "None",
        len(all_chunks),
    )
    if not graph_expander or not all_chunks:
        return all_chunks
    from src.api.routes.search_helpers import graph_expansion
    return await graph_expansion(
        display_query, all_chunks, collections, graph_expander, qdrant_url,
    )


async def _step_crag_evaluate(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    start: float,
    state: dict[str, Any],
) -> Any:
    """Step 7: CRAG evaluation."""
    crag_evaluator = state.get("crag_evaluator")
    if not crag_evaluator or not all_chunks:
        return None
    try:
        crag_evaluation = await crag_evaluator.evaluate(
            display_query, all_chunks, search_time_ms=(time.time() - start) * 1000,
        )
        logger.info(
            "CRAG evaluation: action=%s confidence=%.3f level=%s",
            crag_evaluation.action.value,
            crag_evaluation.confidence_score,
            crag_evaluation.confidence_level.value,
        )
        return crag_evaluation
    except Exception as e:  # noqa: BLE001
        logger.warning("CRAG evaluation failed: %s", e)
        return None


async def _step_log_usage(
    query: str,
    display_query: str,
    all_chunks: list[dict[str, Any]],
    elapsed: float,
    collections: list[str],
    request: HubSearchRequest,
    answer: str | None,
    follow_ups: list[str],
    rerank_applied: bool,
    state: dict[str, Any],
    crag_evaluation: Any = None,
) -> None:
    """Log search usage to repository."""
    usage_repo = state.get("usage_log_repo")
    if not usage_repo:
        return
    try:
        context: dict[str, Any] = {
            "query": query, "display_query": display_query,
            "total_chunks": len(all_chunks), "search_time_ms": elapsed,
            "mode": request.mode, "group_name": request.group_name,
            "embed_calls": 1, "llm_calls": 1 if answer else 0,
            "cross_encoder_calls": 1 if all_chunks else 0,
            "follow_up_generated": len(follow_ups) > 0,
            "rerank_applied": rerank_applied,
        }
        # CRAG 평가 결과 (distill 학습 데이터 품질 필터용)
        if crag_evaluation:
            context["crag_action"] = crag_evaluation.action.value
            context["crag_confidence"] = crag_evaluation.confidence_score

        # Distill 학습 데이터용 (answer + chunks)
        from src.config import get_settings
        if get_settings().distill.log_full_context:
            context["answer"] = answer
            context["chunks"] = [
                {
                    "content": c.get("content", "")[:500],
                    "document_name": c.get("document_name", ""),
                    "score": round(c.get("score", 0), 4),
                }
                for c in all_chunks[:5]
            ]
        await usage_repo.log_search(
            knowledge_id=query, kb_id=",".join(collections),
            user_id="local-user", usage_type="hub_search",
            context=context,
        )
    except Exception as e:  # noqa: BLE001
        # usage log 실패는 hub_search 전체를 막지 않음 (best-effort) —
        # 하지만 조용히 삼키지 말고 로그를 남겨 상위 인시던트 디버깅 가능하게 함.
        logger.warning("Failed to log hub_search usage: %s", e, exc_info=True)


# Hub Search helper functions (extracted from hub_search for cognitive complexity)
# ---------------------------------------------------------------------------

# Error patterns that should NOT be cached
_ERROR_PATTERNS = [
    "응답 생성 중 오류",
    "검색 결과의 신뢰도가 낮아",
    "검색 조건에 맞는 문서를 찾지 못했습니다",
]


def _is_valid_cache(cached_dict: dict, expected_version: str) -> bool:
    """Check if cached result is valid (correct version, not error)."""
    ver = cached_dict.get("_cache_version", "")
    if ver != expected_version:
        return False
    answer = cached_dict.get("answer", "")
    return not (answer and any(p in answer for p in _ERROR_PATTERNS))


def _try_deserialize_cache(
    cached: dict, start: float, cache_layer: str,
) -> HubSearchResponse | None:
    """Deserialize a validated cache dict into a HubSearchResponse."""
    metrics_inc("search_cache_hits")
    cached["metadata"] = cached.get("metadata", {})
    cached["metadata"]["cache_hit"] = True
    if cache_layer:
        cached["metadata"]["cache_layer"] = cache_layer
    cached["search_time_ms"] = round((time.time() - start) * 1000, 1)
    try:
        from src.api.routes.search import HubSearchResponse as _HSR
        return _HSR(**cached)
    except Exception:  # noqa: BLE001
        logger.warning("%s cache deserialization failed, proceeding", cache_layer or "Legacy")
        return None


async def _check_multi_layer_cache(
    multi_cache: Any, query: str, cache_collections: list[str],
    top_k: int, expected_version: str, start: float,
) -> HubSearchResponse | None:
    """Try multi-layer cache lookup."""
    try:
        from src.cache.cache_types import CacheDomain
        cache_entry = await multi_cache.get(
            query, domain=CacheDomain.KB_SEARCH,
            kb_ids=cache_collections, top_k=top_k,
            cache_version=expected_version,
        )
        if not (cache_entry and cache_entry.response):
            return None
        cached = cache_entry.response
        if isinstance(cached, dict) and _is_valid_cache(cached, expected_version):
            return _try_deserialize_cache(cached, start, "multi_layer")
    except Exception as e:  # noqa: BLE001
        logger.warning("MultiLayerCache lookup failed: %s", e)
    return None


async def _check_legacy_cache(
    search_cache: Any, query: str, cache_collections: list[str],
    top_k: int, expected_version: str, start: float,
) -> HubSearchResponse | None:
    """Try legacy search cache lookup."""
    try:
        cached = await search_cache.get(query, cache_collections, top_k)
        if cached and isinstance(cached, dict) and _is_valid_cache(cached, expected_version):
            return _try_deserialize_cache(cached, start, "")
    except Exception as e:  # noqa: BLE001
        logger.warning("Search cache lookup failed: %s", e)
    return None


