"""Search API endpoints - Hub Search compatible."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.api.app import _get_state
from src.api.routes.metrics import inc as metrics_inc
from src.config_weights import weights
from src.domain.models import SearchChunk
from src.search.answer_guard import AnswerGuard
from src.search.crag_evaluator import RetrievalAction
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
    metrics_inc("search_requests")

    # 0. Check cache: MultiLayerCache (L1+L2) first, then legacy SearchCache fallback
    multi_cache = state.get("multi_layer_cache")
    search_cache = state.get("search_cache")
    # Resolve collections early for cache key
    _cache_collections = request.kb_ids or []
    if not _cache_collections and request.kb_filter and request.kb_filter.kb_ids:
        _cache_collections = request.kb_filter.kb_ids
    if not _cache_collections:
        _cache_collections = ["knowledge"]

    # Try MultiLayerCache first
    if multi_cache:
        try:
            from src.cache.cache_types import CacheDomain
            cache_entry = await multi_cache.get(
                query, domain=CacheDomain.KB_SEARCH,
                kb_ids=_cache_collections, top_k=request.top_k,
            )
            if cache_entry and cache_entry.response:
                metrics_inc("search_cache_hits")
                cached = cache_entry.response
                if isinstance(cached, dict):
                    cached["metadata"] = cached.get("metadata", {})
                    cached["metadata"]["cache_hit"] = True
                    cached["metadata"]["cache_layer"] = "multi_layer"
                    cached["search_time_ms"] = round((time.time() - start) * 1000, 1)
                    try:
                        return HubSearchResponse(**cached)
                    except Exception:
                        logger.warning("MultiLayerCache deserialization failed, proceeding")
        except Exception as e:
            logger.warning("MultiLayerCache lookup failed: %s", e)

    # Fallback: legacy SearchCache (exact hash match via Redis)
    if search_cache:
        try:
            cached = await search_cache.get(query, _cache_collections, request.top_k)
            if cached:
                metrics_inc("search_cache_hits")
                cached["metadata"] = cached.get("metadata", {})
                cached["metadata"]["cache_hit"] = True
                cached["search_time_ms"] = round((time.time() - start) * 1000, 1)
                try:
                    return HubSearchResponse(**cached)
                except Exception:
                    logger.warning("Cache deserialization failed, proceeding without cache")
        except Exception as e:
            logger.warning("Search cache lookup failed: %s", e)

    metrics_inc("search_cache_misses")

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

    # 1.5 KB tier access control — filter collections by user's accessible tiers
    kb_registry = state.get("kb_registry")
    if kb_registry and collections != ["knowledge"]:
        try:
            accessible_kbs = await kb_registry.list_all()
            active_kb_ids = {kb["kb_id"] for kb in accessible_kbs if kb.get("status") == "active"}
            filtered = [c for c in collections if c in active_kb_ids]
            if filtered:
                collections = filtered
        except Exception:
            pass  # Graceful degradation: use original collections

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

    # 2b. Query expansion - enrich query with synonyms/related terms
    expanded_terms: list[str] = []
    query_expander = state.get("query_expander")
    if query_expander:
        try:
            first_kb = collections[0] if collections else "knowledge"
            expansion_result = await query_expander.expand_query(first_kb, corrected_query)
            expanded_terms = getattr(expansion_result, "expanded_terms", [])
            expanded_q = getattr(expansion_result, "expanded_query", None)
            if expanded_q and expanded_q != corrected_query:
                corrected_query = expanded_q
        except Exception as e:
            logger.warning("Query expansion failed: %s", e)

    # 2.5 Query type classification — adjust strategy per type
    # Use local top_k to avoid mutating request model (cache key consistency)
    effective_top_k = request.top_k
    query_classification = None
    classifier = state.get("query_classifier")
    if classifier:
        try:
            from src.search.query_classifier import QueryClassifier
            query_classification = classifier.classify(corrected_query)
            if query_classification.query_type.value == "owner_query":
                effective_top_k = max(effective_top_k, 10)
            elif query_classification.query_type.value == "concept":
                effective_top_k = max(effective_top_k, 8)
        except Exception:
            pass

    # 3. Embed the preprocessed query (include ColBERT vectors when reranking is enabled)
    embedder = state.get("embedder")
    if not embedder:
        raise HTTPException(status_code=503, detail="Embedding provider not initialized")

    colbert_enabled = weights.hybrid_search.enable_colbert_reranking
    encoded = await asyncio.to_thread(
        lambda: embedder.encode(
            [corrected_query],
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=colbert_enabled,
        ),
    )
    dense_vector = encoded["dense_vecs"][0]
    sparse_weights = encoded["lexical_weights"][0] if encoded.get("lexical_weights") else {}
    sparse_vector = {int(k): float(v) for k, v in sparse_weights.items()} if sparse_weights else None
    colbert_vectors = (
        encoded["colbert_vecs"][0]
        if colbert_enabled and encoded.get("colbert_vecs")
        else None
    )

    # 4. Search across collections in parallel
    all_chunks: list[dict[str, Any]] = []
    searched_kbs: list[str] = []

    async def _search_collection(collection: str) -> tuple[str, list[dict[str, Any]]]:
        if colbert_enabled and colbert_vectors:
            results = await search_engine.search_with_colbert_rerank(
                kb_id=collection,
                dense_vector=dense_vector,
                sparse_vector=sparse_vector,
                colbert_vectors=colbert_vectors,
                top_k=effective_top_k,
            )
        else:
            results = await search_engine.search(
                kb_id=collection,
                dense_vector=dense_vector,
                sparse_vector=sparse_vector,
                top_k=effective_top_k,
            )
        chunks = []
        for r in results:
            chunk_meta = r.metadata or {}
            doc_name = chunk_meta.get("document_name", "")
            # Document filter: skip chunks not matching filter
            if request.document_filter:
                if not any(f.lower() in doc_name.lower() for f in request.document_filter):
                    continue
            chunks.append({
                "chunk_id": r.point_id,
                "content": r.content,
                "score": r.score,
                "kb_id": collection,
                "document_name": doc_name,
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
    all_chunks = all_chunks[: effective_top_k * weights.search.rerank_pool_multiplier]

    # 4.5. Passage cleaning - normalize text before reranking
    all_chunks = clean_chunks(all_chunks)

    # 4.6. Cross-encoder reranking - neural relevance scoring
    try:
        all_chunks = await async_rerank_with_cross_encoder(
            query=corrected_query,
            chunks=all_chunks,
            top_k=effective_top_k * weights.search.rerank_pool_multiplier,
        )
    except Exception as _ce_err:
        logger.warning("Cross-encoder reranking skipped: %s", _ce_err)

    # 5. CompositeReranker - rerank results with weighted fusion
    rerank_applied = False
    composite_reranker = state.get("composite_reranker")
    if composite_reranker and all_chunks:
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
            top_k=effective_top_k,
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
    all_chunks = all_chunks[: effective_top_k]

    # 6. Graph Expansion - enrich results with structurally related content
    #    Must run BEFORE answer generation so expanded chunks are included in the answer.
    graph_expander = state.get("graph_expander")
    if graph_expander and all_chunks:
        try:
            # Graph expansion with timeout (max 3s to avoid blocking search)
            if hasattr(graph_expander, "expand_with_entities"):
                expansion = await asyncio.wait_for(
                    graph_expander.expand_with_entities(
                        corrected_query, all_chunks, scope_kb_ids=collections,
                    ),
                    timeout=5.0,
                )
            else:
                expansion = await asyncio.wait_for(
                    graph_expander.expand(
                        corrected_query, all_chunks, scope_kb_ids=collections,
                    ),
                    timeout=5.0,
                )
            logger.info(
                "Graph expansion: %d URIs, %d related",
                len(expansion.expanded_source_uris), expansion.graph_related_count,
            )
            if expansion.expanded_source_uris:
                # Boost existing chunks that match graph expansion
                all_chunks = graph_expander.boost_chunks(
                    all_chunks, expansion.expanded_source_uris
                )

                # Inject graph-found documents NOT already in results
                existing_docs = {c.get("document_name", "") for c in all_chunks}
                import unicodedata as _uc
                existing_docs_nfc = {_uc.normalize("NFC", d) for d in existing_docs}
                new_docs = {
                    d for d in expansion.expanded_source_uris
                    if _uc.normalize("NFC", d) not in existing_docs_nfc
                }
                if new_docs:
                    import httpx as _hx
                    qdrant_url = state.get("qdrant_url", "http://localhost:6333")
                    async with _hx.AsyncClient(timeout=3.0) as _qc:
                        for doc_name in list(new_docs)[:3]:
                            for coll in collections:
                                try:
                                    coll_name = f"kb_{coll.replace('-', '_')}"
                                    # NFD normalize for Qdrant match
                                    dn_nfd = _uc.normalize("NFD", doc_name)
                                    resp = await _qc.post(
                                        f"{qdrant_url}/collections/{coll_name}/points/scroll",
                                        json={
                                            "limit": 2,
                                            "with_payload": True,
                                            "with_vector": False,
                                            "filter": {"must": [{"key": "document_name", "match": {"value": doc_name}}]},
                                        },
                                    )
                                    if resp.status_code != 200:
                                        # Try NFD
                                        resp = await _qc.post(
                                            f"{qdrant_url}/collections/{coll_name}/points/scroll",
                                            json={
                                                "limit": 2,
                                                "with_payload": True,
                                                "with_vector": False,
                                                "filter": {"must": [{"key": "document_name", "match": {"value": dn_nfd}}]},
                                            },
                                        )
                                    if resp.status_code == 200:
                                        points = resp.json().get("result", {}).get("points", [])
                                        for pt in points:
                                            pay = pt.get("payload", {})
                                            all_chunks.append({
                                                "content": pay.get("content", ""),
                                                "document_name": pay.get("document_name", ""),
                                                "source_uri": pay.get("source_uri", ""),
                                                "metadata": pay,
                                                "score": 0.35,
                                                "graph_injected": True,
                                                "graph_boosted": True,
                                            })
                                except Exception:
                                    pass

                all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
        except asyncio.TimeoutError:
            logger.warning("Graph expansion timed out (5s), skipping")
        except Exception as e:
            logger.warning("Graph expansion failed in search route: %s", e)

    # 7. CRAG Evaluation - assess retrieval quality before answer generation
    crag_evaluation = None
    crag_evaluator = state.get("crag_evaluator")
    if crag_evaluator and all_chunks:
        try:
            elapsed_so_far = (time.time() - start) * 1000
            crag_evaluation = await crag_evaluator.evaluate(
                corrected_query, all_chunks, search_time_ms=elapsed_so_far,
            )
            logger.info(
                "CRAG evaluation: action=%s confidence=%.3f level=%s",
                crag_evaluation.action.value,
                crag_evaluation.confidence_score,
                crag_evaluation.confidence_level.value,
            )
        except Exception as e:
            logger.warning("CRAG evaluation failed: %s", e)

    # 8. Generate answer
    answer = None
    query_type = ""
    confidence = ""

    # Inline quality blocking: skip answer when confidence too low
    if crag_evaluation and crag_evaluation.action == RetrievalAction.INCORRECT:
        answer = crag_evaluation.recommendation
        confidence = crag_evaluation.confidence_level.value
    elif crag_evaluation and crag_evaluation.confidence_score < 0.3:
        answer = "검색 결과의 신뢰도가 낮아 정확한 답변을 제공하기 어렵습니다. 질문을 더 구체적으로 해주세요."
        confidence = "낮음"
    elif request.include_answer and all_chunks:
        # Try TieredResponseGenerator first, fall back to AnswerService
        tiered_gen = state.get("tiered_response_generator")
        llm = state.get("llm")

        if tiered_gen and llm:
            try:
                from src.search.query_classifier import QueryClassifier
                from src.search.tiered_response import RAGContext

                classifier = state.get("query_classifier") or QueryClassifier()
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
            # Fallback to AnswerService (pre-initialized in _init_services)
            answer_service = state.get("answer_service")
            if answer_service:
                result = await answer_service.enrich(corrected_query, all_chunks)
                answer = result.answer
                query_type = result.query_type
                confidence = result.confidence_indicator

    # 9. Answer Guard - replace generic LLM answers with chunk-based fallback
    if answer:
        answer = _answer_guard.guard(answer, all_chunks, corrected_query)

    # 9.5 Conflict detection - detect contradictory answers from different KBs
    conflicts: list[dict[str, Any]] = []
    if len(searched_kbs) > 1 and all_chunks:
        kb_answers: dict[str, list[str]] = {}
        for c in all_chunks[:10]:
            kb = c.get("kb_id", "")
            content = c.get("content", "")[:200]
            if kb and content:
                kb_answers.setdefault(kb, []).append(content)
        if len(kb_answers) > 1:
            # Simple conflict detection: check if top chunks from different KBs
            # have very different content (low overlap)
            kb_list = list(kb_answers.keys())
            for i in range(len(kb_list)):
                for j in range(i + 1, len(kb_list)):
                    kb_a, kb_b = kb_list[i], kb_list[j]
                    texts_a = " ".join(kb_answers[kb_a][:3])
                    texts_b = " ".join(kb_answers[kb_b][:3])
                    # Check word overlap ratio
                    words_a = set(texts_a.split())
                    words_b = set(texts_b.split())
                    if words_a and words_b:
                        overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
                        if overlap < 0.1:  # Very low overlap = potential conflict
                            conflicts.append({
                                "kb_a": kb_a,
                                "kb_b": kb_b,
                                "overlap_ratio": round(overlap, 3),
                                "warning": f"KB '{kb_a}'와 '{kb_b}'의 답변이 상이할 수 있습니다.",
                            })

    # 9.6 Follow-up question generation (only when answer is included)
    follow_ups: list[str] = []
    if request.include_answer and answer and all_chunks:
        try:
            llm = state.get("llm")
            if llm:
                follow_up_prompt = (
                    f"다음 질문과 답변을 바탕으로, 사용자가 추가로 궁금해할 수 있는 후속 질문 3개를 생성하세요.\n"
                    f"각 질문은 한 줄씩, 번호 없이 작성하세요.\n\n"
                    f"질문: {corrected_query}\n답변: {answer[:500]}\n\n후속 질문:"
                )
                follow_up_result = await llm.generate(follow_up_prompt, temperature=0.3, max_tokens=200)
                if follow_up_result:
                    follow_ups = [q.strip().lstrip("- ·•123.") for q in follow_up_result.strip().split("\n") if q.strip()][:3]
        except Exception as _fu_err:
            logger.debug("Follow-up generation skipped: %s", _fu_err)

    elapsed = (time.time() - start) * 1000

    # Log search usage (fire-and-forget, never fail the search)
    usage_repo = state.get("usage_log_repo")
    if usage_repo:
        try:
            await usage_repo.log_search(
                knowledge_id=query,
                kb_id=",".join(collections),
                user_id="local-user",
                usage_type="hub_search",
                context={
                    "query": query,
                    "corrected_query": corrected_query,
                    "total_chunks": len(all_chunks),
                    "search_time_ms": elapsed,
                    "mode": request.mode,
                    "group_name": request.group_name,
                    "embed_calls": 1,
                    "llm_calls": 1 if answer else 0,
                    "cross_encoder_calls": 1 if all_chunks else 0,
                    "follow_up_generated": len(follow_ups) > 0,
                    "rerank_applied": rerank_applied,
                },
            )
        except Exception:
            pass  # Don't fail search because of logging

    response = HubSearchResponse(
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
            **({"crag_action": crag_evaluation.action.value,
                "crag_confidence": crag_evaluation.confidence_score,
                "crag_recommendation": crag_evaluation.recommendation,
                } if crag_evaluation else {}),
            **({"conflicts": conflicts} if conflicts else {}),
            **({"follow_up_questions": follow_ups} if follow_ups else {}),
        },
    )

    # Store in caches (fire-and-forget)
    _response_dict = response.model_dump()

    # Only cache results that include an answer (avoid serving empty answers from cache)
    if response.answer:
        if multi_cache:
            try:
                from src.cache.cache_types import CacheDomain
                await multi_cache.set(
                    query, _response_dict, domain=CacheDomain.KB_SEARCH,
                    metadata={"kb_ids": collections},
                    kb_ids=collections, top_k=effective_top_k,
                )
            except Exception:
                pass

        if search_cache:
            try:
                await search_cache.set(query, collections, _response_dict, effective_top_k)
            except Exception:
                pass

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
        return {"kbs": [{"kb_id": n, "name": n} for n in names]}
    except Exception as e:
        logger.warning("KB list failed: %s", e)
        return {"kbs": []}
