"""Multi-Layer Cache orchestrator.

Orchestrates L1 (in-memory) -> L2 (Redis semantic) -> miss.
Domain-specific thresholds determine cache behavior:
- policy=1.0 (exact match only)
- code=0.95
- kb=0.92
- general=0.85

Promotes L2 hits to L1 for faster subsequent access.

Adapted from oreo-ecosystem infrastructure/cache/multi_layer_cache.py.
Simplified: 2 layers (no L3 distributed), no Datadog metrics.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from .cache_types import (
    CacheDomain,
    CacheEntry,
    CacheMetrics,
    ICacheLayer,
)
from .l1_memory_cache import L1InMemoryCache

logger = logging.getLogger(__name__)


class MultiLayerCache:
    """2-Layer semantic cache: L1 (memory) -> L2 (Redis semantic) -> miss.

    L1: Exact match, <1ms, 5 min TTL.
    L2: Semantic match, <10ms, 6 hour TTL.

    Write-through: stores to both layers on set().
    L2 hits are promoted to L1.
    """

    def __init__(
        self,
        l1_cache: ICacheLayer | None = None,
        l2_cache: ICacheLayer | None = None,
        embedding_provider: Any | None = None,
        enable_metrics: bool = True,
    ):
        self._l1 = l1_cache if l1_cache is not None else L1InMemoryCache()
        self._l2 = l2_cache
        self._embedding_provider = embedding_provider
        self._metrics = CacheMetrics() if enable_metrics else None

    async def get(
        self,
        query: str,
        domain: CacheDomain = CacheDomain.GENERAL,
        kb_ids: list[str] | None = None,
        top_k: int = 0,
        **kwargs: Any,
    ) -> CacheEntry | None:
        """Cache lookup: L1 -> L2 -> miss.

        Args:
            query: Search query.
            domain: Cache domain for threshold selection.
            kb_ids: KB IDs (included in key for isolation).
            top_k: Top-K parameter (included in key).

        Returns:
            CacheEntry on hit, None on miss.
        """
        key = self._generate_key(query, kb_ids, top_k)

        # L1: Exact match
        start = time.time()
        entry = await self._l1.get(key)
        l1_latency = (time.time() - start) * 1000

        if entry:
            if self._metrics:
                self._metrics.l1_hits += 1
                self._metrics.l1_latency_sum_ms += l1_latency
            logger.debug("L1 hit: %s... (%.2fms)", key[:16], l1_latency)
            return entry

        # L2: Semantic match
        if self._l2 is not None:
            start = time.time()
            entry = await self._l2.get(
                key, query=query, domain=domain,
                kb_ids=kb_ids,
                cache_version=kwargs.get("cache_version", ""),
            )
            # Validate L2 result version if cache_version provided
            if entry and kwargs.get("cache_version"):
                resp = entry.response
                if isinstance(resp, dict):
                    stored_ver = resp.get("_cache_version", "")
                    if stored_ver and stored_ver != kwargs["cache_version"]:
                        entry = None  # Stale version, discard
            l2_latency = (time.time() - start) * 1000

            if entry:
                if self._metrics:
                    self._metrics.l2_hits += 1
                    self._metrics.l2_latency_sum_ms += l2_latency
                logger.debug(
                    "L2 hit: similarity=%.3f (%.2fms)",
                    entry.similarity,
                    l2_latency,
                )
                # Promote to L1
                await self._l1.set(entry)
                return entry

        # Miss
        if self._metrics:
            self._metrics.total_misses += 1
        logger.debug("Cache miss: %s...", key[:16])
        return None

    async def set(
        self,
        query: str,
        response: Any,
        domain: CacheDomain = CacheDomain.GENERAL,
        metadata: dict[str, Any] | None = None,
        kb_ids: list[str] | None = None,
        top_k: int = 0,
    ) -> str:
        """Store in all layers (write-through).

        Args:
            query: Query text.
            response: Response to cache.
            domain: Cache domain.
            metadata: Extra metadata (e.g. kb_id for invalidation).
            kb_ids: KB IDs for key generation.
            top_k: Top-K for key generation.

        Returns:
            Cache key.
        """
        key = self._generate_key(query, kb_ids, top_k)

        # Generate embedding for L2
        embedding = None
        if self._embedding_provider:
            try:
                embedding = await self._embedding_provider.embed(query)
            except Exception as e:
                logger.warning("Embedding generation for cache failed: %s", e)

        entry = CacheEntry(
            key=key,
            query=query,
            response=response,
            embedding=embedding,
            domain=domain,
            metadata=metadata or {},
        )

        # Write to L1
        try:
            await self._l1.set(entry)
        except Exception as e:
            logger.warning("L1 cache write failed: %s", e)

        # Write to L2
        if self._l2 is not None:
            try:
                await self._l2.set(entry)
            except Exception as e:
                logger.warning("L2 cache write failed: %s", e)

        return key

    async def invalidate(self, query: str, kb_ids: list[str] | None = None, top_k: int = 0) -> bool:
        """Invalidate a specific query from all layers."""
        key = self._generate_key(query, kb_ids, top_k)
        results = [await self._l1.delete(key)]
        if self._l2 is not None:
            results.append(await self._l2.delete(key))
        return any(results)

    async def invalidate_kb(self, kb_id: str) -> dict[str, int]:
        """Invalidate all cache entries for a specific KB.

        Uses metadata-based selective deletion when supported.
        """
        logger.info("Invalidating cache for KB: %s", kb_id)
        l1_deleted = await self._invalidate_layer(self._l1, kb_id)
        l2_deleted = 0
        if self._l2 is not None:
            l2_deleted = await self._invalidate_layer(self._l2, kb_id)
        result = {"l1": l1_deleted, "l2": l2_deleted}
        logger.info("Cache invalidated for KB %s: L1=%d, L2=%d", kb_id, l1_deleted, l2_deleted)
        return result

    @staticmethod
    async def _invalidate_layer(layer: ICacheLayer, kb_id: str) -> int:
        if hasattr(layer, "invalidate_by_metadata_value"):
            return await layer.invalidate_by_metadata_value("kb_id", kb_id)
        return await layer.clear()

    async def clear(self) -> dict[str, int]:
        """Clear all layers."""
        l1 = await self._l1.clear()
        l2 = 0
        if self._l2 is not None:
            l2 = await self._l2.clear()
        return {"l1": l1, "l2": l2}

    def get_metrics(self) -> CacheMetrics | None:
        return self._metrics

    def reset_metrics(self) -> None:
        if self._metrics:
            self._metrics = CacheMetrics()

    def stats(self) -> dict[str, Any]:
        """Aggregate stats from all layers."""
        result: dict[str, Any] = {}
        if hasattr(self._l1, "stats"):
            result["l1"] = self._l1.stats()
        if self._l2 is not None and hasattr(self._l2, "stats"):
            result["l2"] = self._l2.stats()
        if self._metrics:
            result["metrics"] = self._metrics.to_dict()
        return result

    async def get_or_compute(
        self,
        query: str,
        compute_fn: Any,
        domain: CacheDomain = CacheDomain.GENERAL,
        metadata: dict[str, Any] | None = None,
        kb_ids: list[str] | None = None,
        top_k: int = 0,
    ) -> tuple[Any, bool]:
        """Cache lookup or compute on miss.

        Returns:
            (response, cache_hit).
        """
        entry = await self.get(query, domain=domain, kb_ids=kb_ids, top_k=top_k)
        if entry:
            return entry.response, True

        response = await compute_fn()
        await self.set(query, response, domain=domain, metadata=metadata, kb_ids=kb_ids, top_k=top_k)
        return response, False

    @staticmethod
    def _generate_key(query: str, kb_ids: list[str] | None = None, top_k: int = 0) -> str:
        """Generate cache key from query, kb_ids (ordered), and top_k.

        KB order is preserved (not sorted) because first KB may have
        priority in search expansion and reranking.
        """
        raw = query.lower().strip()
        if kb_ids:
            raw += "::" + ",".join(kb_ids)  # Ordered, not sorted
        if top_k:
            raw += f"::top_k={top_k}"
        h = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"mlc:{h}"
