"""Cache Types and Interfaces for knowledge-local.

Common types shared across L1 memory cache, L2 semantic cache, and multi-layer cache.

Adapted from oreo-ecosystem infrastructure/cache/cache_types.py.
Simplified: no Datadog, no env-var override for thresholds.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _utc_now() -> datetime:
    """Return current UTC time."""
    return datetime.now(timezone.utc)


class CacheDomain(str, Enum):
    """Cache domain for domain-specific similarity thresholds."""

    POLICY = "policy"          # Exact match only
    CODE = "code"              # 0.95
    KB_SEARCH = "kb_search"    # 0.92
    GENERAL = "general"        # 0.85


# Domain-specific similarity thresholds — SSOT: config_weights.CacheConfig
def _build_domain_thresholds() -> dict[CacheDomain, float]:
    from src.config_weights import weights as _w
    c = _w.cache
    return {
        CacheDomain.POLICY: c.threshold_policy,
        CacheDomain.CODE: c.threshold_code,
        CacheDomain.KB_SEARCH: c.threshold_kb,
        CacheDomain.GENERAL: c.threshold_general,
    }

DOMAIN_THRESHOLDS: dict[CacheDomain, float] = _build_domain_thresholds()


@dataclass
class CacheEntry:
    """Cache entry storing query, response, and optional embedding.

    Attributes:
        key: Cache key.
        query: Original query text.
        response: Cached response (any serializable object).
        embedding: Dense embedding vector for semantic matching (L2).
        similarity: Similarity score when retrieved via semantic match.
        domain: Cache domain for threshold selection.
        metadata: Extra metadata (e.g. kb_id for selective invalidation).
        created_at: Entry creation time.
        hit_count: Number of cache hits.
        last_accessed_at: Last access time.
    """

    key: str
    query: str
    response: Any
    embedding: list[float] | None = None
    similarity: float = 1.0
    domain: CacheDomain = CacheDomain.GENERAL
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    hit_count: int = 0
    last_accessed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "query": self.query,
            "response": self.response,
            "similarity": self.similarity,
            "domain": self.domain.value,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "hit_count": self.hit_count,
        }


@dataclass
class CacheMetrics:
    """Aggregate cache metrics across layers."""

    l1_hits: int = 0
    l2_hits: int = 0
    total_misses: int = 0
    l1_latency_sum_ms: float = 0.0
    l2_latency_sum_ms: float = 0.0

    @property
    def total_hits(self) -> int:
        return self.l1_hits + self.l2_hits

    @property
    def total_requests(self) -> int:
        return self.total_hits + self.total_misses

    @property
    def hit_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_hits / self.total_requests

    def to_dict(self) -> dict[str, Any]:
        return {
            "l1_hits": self.l1_hits,
            "l2_hits": self.l2_hits,
            "total_misses": self.total_misses,
            "total_hits": self.total_hits,
            "total_requests": self.total_requests,
            "hit_rate": round(self.hit_rate, 4),
        }


class ICacheLayer(ABC):
    """Abstract cache layer interface."""

    @abstractmethod
    async def get(self, key: str, **kwargs: Any) -> CacheEntry | None:
        pass

    @abstractmethod
    async def set(self, entry: CacheEntry, ttl_seconds: int | None = None) -> None:
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        pass

    @abstractmethod
    async def clear(self) -> int:
        pass
