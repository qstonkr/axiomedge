"""Multi-layer caching and dedup for knowledge-local.

Layers:
- L1: In-memory LRU (exact match, <1ms)
- L2: Redis semantic cache (cosine similarity, <10ms)
- MultiLayerCache: Orchestrates L1 -> L2 -> miss

Also includes:
- SearchCache: Simple Redis hash cache (legacy, replaced by MultiLayerCache)
- DedupCache: Content-hash dedup for ingestion
- IdempotencyCache: Request dedup
- CacheKeyBuilder: Structured key generation
"""

from .redis_cache import SearchCache
from .dedup_cache import DedupCache
from .cache_types import CacheDomain, CacheEntry, CacheMetrics, DOMAIN_THRESHOLDS, ICacheLayer
from .l1_memory_cache import L1InMemoryCache
from .l2_semantic_cache import L2SemanticCache
from .multi_layer_cache import MultiLayerCache
from .cache_key_builder import build_cache_key, normalize_query
from .idempotency_cache import IdempotencyCache, request_hash

__all__ = [
    # Legacy
    "SearchCache",
    "DedupCache",
    # Types
    "CacheDomain",
    "CacheEntry",
    "CacheMetrics",
    "DOMAIN_THRESHOLDS",
    "ICacheLayer",
    # Layers
    "L1InMemoryCache",
    "L2SemanticCache",
    # Orchestrator
    "MultiLayerCache",
    # Key builder
    "build_cache_key",
    "normalize_query",
    # Idempotency
    "IdempotencyCache",
    "request_hash",
]
