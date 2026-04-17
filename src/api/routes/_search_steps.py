"""Search pipeline step functions -- facade with re-exports.

Original monolith split into sub-modules for maintainability:
- _search_preprocess.py: query expansion, classification, collection resolution
- _search_embed.py: embedding
- _search_retrieve.py: collection search, keyword fallback, enrichment
- _search_rerank.py: composite reranking, trust & freshness scoring
- _search_answer.py: answer generation, CRAG, conflicts, transparency

Cache and keyword functions remain here because tests patch their
internal dependencies (weights, _is_valid_cache, _kiwi_instance) on
this module via unittest.mock.
"""

from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

from src.api.routes.metrics import inc as metrics_inc  # noqa: F401
from src.config.weights import weights
from src.core.models import SearchChunk  # noqa: F401

if TYPE_CHECKING:
    from src.stores.redis.multi_layer_cache import MultiLayerCache
    from src.stores.redis.redis_cache import SearchCache

logger = logging.getLogger(__name__)


# ── KiwiPy morpheme-based keyword extraction (singleton) ──
_kiwi_instance = None
_NOUN_TAGS = frozenset({"NNG", "NNP", "SL", "SH"})


def _extract_query_keywords(query: str) -> list[str]:
    """Extract meaningful keywords via KiwiPy morphological analysis."""
    global _kiwi_instance
    if _kiwi_instance is None:
        try:
            from kiwipiepy import Kiwi
            _kiwi_instance = Kiwi()
        except ImportError:
            return [
                t.strip() for t in query.lower().split()
                if len(t.strip()) >= 2
            ]
    try:
        tokens = _kiwi_instance.tokenize(query)
        keywords = [
            tok.form for tok in tokens
            if tok.tag in _NOUN_TAGS and len(tok.form) >= 2
        ]
        if keywords:
            return keywords
        return [
            t.strip() for t in query.lower().split()
            if len(t.strip()) >= 2
        ]
    except (
        RuntimeError, OSError, ValueError, TypeError,
        KeyError, AttributeError,
    ):
        return [
            t.strip() for t in query.lower().split()
            if len(t.strip()) >= 2
        ]


# ── Cache helpers (kept inline for test-patch compat) ──

_ERROR_PATTERNS = [
    "응답 생성 중 오류",
    "검색 결과의 신뢰도가 낮아",
    "검색 조건에 맞는 문서를 찾지 못했습니다",
]


def _is_valid_cache(
    cached_dict: dict, expected_version: str,
) -> bool:
    """Check if cached result is valid."""
    ver = cached_dict.get("_cache_version", "")
    if ver != expected_version:
        return False
    answer = cached_dict.get("answer", "")
    return not (
        answer and any(p in answer for p in _ERROR_PATTERNS)
    )


def _try_deserialize_cache(
    cached: dict, start: float, cache_layer: str,
) -> Any | None:
    """Deserialize a validated cache dict into HubSearchResponse."""
    metrics_inc("search_cache_hits")
    cached["metadata"] = cached.get("metadata", {})
    cached["metadata"]["cache_hit"] = True
    if cache_layer:
        cached["metadata"]["cache_layer"] = cache_layer
    cached["search_time_ms"] = round(
        (time.time() - start) * 1000, 1,
    )
    try:
        from src.api.routes.search import (
            HubSearchResponse as _HSR,
        )
        return _HSR(**cached)
    except (
        RuntimeError, OSError, ValueError, TypeError,
        KeyError, AttributeError,
    ):
        logger.warning(
            "%s cache deserialization failed, proceeding",
            cache_layer or "Legacy",
        )
        return None


async def _check_multi_layer_cache(
    multi_cache: MultiLayerCache,
    query: str,
    cache_collections: list[str],
    top_k: int,
    expected_version: str,
    start: float,
) -> Any | None:
    """Try multi-layer cache lookup."""
    try:
        from src.stores.redis.cache_types import CacheDomain
        cache_entry = await multi_cache.get(
            query,
            domain=CacheDomain.KB_SEARCH,
            kb_ids=cache_collections,
            top_k=top_k,
            cache_version=expected_version,
        )
        if not (cache_entry and cache_entry.response):
            return None
        cached = cache_entry.response
        if isinstance(cached, dict) and _is_valid_cache(
            cached, expected_version,
        ):
            return _try_deserialize_cache(
                cached, start, "multi_layer",
            )
    except (
        RuntimeError, OSError, ValueError, TypeError,
        KeyError, AttributeError,
    ) as e:
        logger.warning("MultiLayerCache lookup failed: %s", e)
    return None


async def _check_legacy_cache(
    search_cache: SearchCache,
    query: str,
    cache_collections: list[str],
    top_k: int,
    expected_version: str,
    start: float,
) -> Any | None:
    """Try legacy search cache lookup."""
    try:
        cached = await search_cache.get(
            query, cache_collections, top_k,
        )
        if (
            cached
            and isinstance(cached, dict)
            and _is_valid_cache(cached, expected_version)
        ):
            return _try_deserialize_cache(cached, start, "")
    except (
        RuntimeError, OSError, ValueError, TypeError,
        KeyError, AttributeError,
    ) as e:
        logger.warning("Search cache lookup failed: %s", e)
    return None


async def _step_cache_check(
    query: str,
    state: dict[str, Any],
    cache_collections: list[str],
    top_k: int,
    start: float,
) -> Any | None:
    """Step 0: Check multi-layer and legacy caches."""
    expected_version = weights.cache.cache_version

    multi_cache = state.get("multi_layer_cache")
    if multi_cache:
        result = await _check_multi_layer_cache(
            multi_cache, query, cache_collections,
            top_k, expected_version, start,
        )
        if result:
            return result

    search_cache = state.get("search_cache")
    if search_cache:
        result = await _check_legacy_cache(
            search_cache, query, cache_collections,
            top_k, expected_version, start,
        )
        if result:
            return result

    return None


async def _step_cache_store(
    query: str,
    response: Any,
    collections: list[str],
    effective_top_k: int,
    state: dict[str, Any],
) -> None:
    """Store response in caches (fire-and-forget)."""
    if not response.answer or any(
        p in response.answer for p in _ERROR_PATTERNS
    ):
        return

    response_dict = response.model_dump()
    response_dict["_cache_version"] = weights.cache.cache_version

    multi_cache = state.get("multi_layer_cache")
    if multi_cache:
        try:
            from src.stores.redis.cache_types import CacheDomain
            await multi_cache.set(
                query, response_dict,
                domain=CacheDomain.KB_SEARCH,
                metadata={"kb_ids": collections},
                kb_ids=collections,
                top_k=effective_top_k,
            )
        except (
            RuntimeError, OSError, ValueError, TypeError,
            KeyError, AttributeError,
        ) as e:
            logger.debug(
                "Failed to write multi-layer cache: %s", e,
            )

    search_cache = state.get("search_cache")
    if search_cache:
        try:
            await search_cache.set(
                query, collections, response_dict,
                effective_top_k,
            )
        except (
            RuntimeError, OSError, ValueError, TypeError,
            KeyError, AttributeError,
        ) as e:
            logger.debug(
                "Failed to write search cache: %s", e,
            )


# ── Re-exports from sub-modules ──

# Preprocessing & query handling
from src.api.routes._search_preprocess import (  # noqa: F401, E402
    _resolve_collections_from_qdrant,
    _filter_by_kb_registry,
    _step_resolve_collections,
    _step_preprocess,
    _step_expand_query,
    _step_classify_query,
)

# Embedding
from src.api.routes._search_embed import (  # noqa: F401, E402
    _step_embed,
)

# Retrieval
from src.api.routes._search_retrieve import (  # noqa: F401, E402
    _build_chunks_from_results,
    _step_search_collections,
    _step_keyword_fallback,
    _step_search_enrichment,
    _step_tree_expand,
    _step_graph_expand,
)

# Reranking
from src.api.routes._search_rerank import (  # noqa: F401, E402
    _step_composite_rerank,
    _step_week_match_guarantee,
    _parse_datetime_safe,
    _step_apply_trust_and_freshness,
)

# Answer generation & evaluation
from src.api.routes._search_answer import (  # noqa: F401, E402
    _step_generate_answer,
    _try_tiered_generation,
    _check_kb_pair_conflict,
    _step_detect_conflicts,
    _step_follow_ups,
    _step_build_transparency,
    _step_crag_evaluate,
    _step_log_usage,
)

__all__ = [
    "_step_cache_check",
    "_step_cache_store",
    "_ERROR_PATTERNS",
    "_is_valid_cache",
    "_try_deserialize_cache",
    "_check_multi_layer_cache",
    "_check_legacy_cache",
    "_extract_query_keywords",
    "_resolve_collections_from_qdrant",
    "_filter_by_kb_registry",
    "_step_resolve_collections",
    "_step_preprocess",
    "_step_expand_query",
    "_step_classify_query",
    "_step_embed",
    "_build_chunks_from_results",
    "_step_search_collections",
    "_step_keyword_fallback",
    "_step_search_enrichment",
    "_step_tree_expand",
    "_step_graph_expand",
    "_step_composite_rerank",
    "_step_week_match_guarantee",
    "_parse_datetime_safe",
    "_step_apply_trust_and_freshness",
    "_step_generate_answer",
    "_try_tiered_generation",
    "_check_kb_pair_conflict",
    "_step_detect_conflicts",
    "_step_follow_ups",
    "_step_build_transparency",
    "_step_crag_evaluate",
    "_step_log_usage",
]
