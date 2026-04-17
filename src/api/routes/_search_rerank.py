"""Search reranking and scoring steps.

Extracted from _search_steps.py for module size management.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from src.core.models import SearchChunk
from src.search.trust_score_service import SOURCE_CREDIBILITY

logger = logging.getLogger(__name__)


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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            return None
    return None


def _step_apply_trust_and_freshness(
    all_chunks: list[dict[str, Any]],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Step 5.6: Attach KTS trust score and freshness score to chunk metadata.

    Feature-gated via SEARCH_KTS_ENABLED / SEARCH_FRESHNESS_ENABLED env vars.
    Scores are informational (metadata only) -- no reranking impact yet.
    """
    kts_enabled = os.environ.get("SEARCH_KTS_ENABLED", "true").lower() != "false"
    freshness_enabled = os.environ.get("SEARCH_FRESHNESS_ENABLED", "true").lower() != "false"

    if not kts_enabled and not freshness_enabled:
        return all_chunks

    freshness_predictor = state.get("freshness_predictor") if freshness_enabled else None

    for chunk in all_chunks:
        meta = chunk.get("metadata") or {}

        if freshness_predictor:
            raw_date = (
                meta.get("last_modified")
                or meta.get("updated_at")
                or chunk.get("updated_at")
            )
            updated_at = _parse_datetime_safe(raw_date)
            if updated_at:
                doc_type = meta.get("doc_type", "general") or "general"
                meta["freshness_score"] = round(freshness_predictor.score(updated_at, doc_type), 4)

        if kts_enabled:
            source_type = meta.get("source_type", "auto_extracted")
            meta["kts_source_credibility"] = SOURCE_CREDIBILITY.get(source_type, 0.0)

        chunk["metadata"] = meta

    return all_chunks
