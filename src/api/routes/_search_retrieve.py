"""Search retrieval steps: collection search, keyword fallback, enrichment, tree/graph expand.

Extracted from _search_steps.py for module size management.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.config.weights import weights
from src.config.weights import weights as _w

from src.api.routes._search_preprocess import _extract_query_keywords

logger = logging.getLogger(__name__)


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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.debug("Keyword fallback search failed: %s", e)

    return all_chunks


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

        expanded_ids = [s.chunk_id for s in siblings] + [s.chunk_id for s in section_hits]
        if expanded_ids:
            expanded_scores = {s.chunk_id: s.score for s in (*siblings, *section_hits)}
            from src.pipelines.qdrant_utils import str_to_uuid
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("Tree context expansion failed: %s", e)

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
