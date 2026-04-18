# pyright: reportAttributeAccessIssue=false
"""Search preprocessing, query expansion, classification, and collection resolution.

Extracted from _search_steps.py for module size management.
"""

from __future__ import annotations

import logging
import re as _re_dq
from typing import Any, TYPE_CHECKING

from src.config.weights import weights

if TYPE_CHECKING:
    pass

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
        if keywords:
            return keywords
        return [
            t.strip() for t in query.lower().split()
            if len(t.strip()) >= 2
        ]
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return [t.strip() for t in query.lower().split() if len(t.strip()) >= 2]


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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("KB registry filter failed, using unfiltered collections: %s", e)
        return collections


async def _step_resolve_collections(
    request: Any,
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Query expansion failed: %s", e)

    return search_query, display_query, expanded_terms


def _step_classify_query(
    display_query: str,
    top_k: int,
    state: dict[str, Any],
) -> tuple[int, float, float]:
    """Step 2.5: Classify query type and adjust weights.

    Returns (effective_top_k, dense_w, sparse_w).
    """
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Query type weight adjustment failed: %s", e)

    if _re_dq.search(r"20\d{2}년\s*\d{1,2}월|20\d{2}[_\-]\d{2}|\d{1,2}월\s*\d주차", display_query):
        _dense_w = weights.hybrid_search.date_query_dense_weight
        _sparse_w = weights.hybrid_search.date_query_sparse_weight

    return effective_top_k, _dense_w, _sparse_w
