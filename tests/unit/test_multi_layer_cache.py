"""Unit tests for src/cache/multi_layer_cache.py

Tests MultiLayerCache: get/set, L1 hit, L2 hit, miss, invalidate, clear,
get_or_compute stampede protection, metrics tracking.
L2 is mocked (no Redis needed).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.stores.redis.cache_types import CacheDomain, CacheEntry, ICacheLayer
from src.stores.redis.l1_memory_cache import L1InMemoryCache
from src.stores.redis.multi_layer_cache import MultiLayerCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockL2Cache(ICacheLayer):
    """In-memory mock for L2 semantic cache layer."""

    def __init__(self) -> None:
        self._store: dict[str, CacheEntry] = {}

    async def get(self, key: str, **kwargs: Any) -> CacheEntry | None:
        return self._store.get(key)

    async def set(self, entry: CacheEntry, ttl_seconds: int | None = None) -> None:
        self._store[entry.key] = entry

    async def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    async def clear(self) -> int:
        count = len(self._store)
        self._store.clear()
        return count


@pytest.fixture
def l1() -> L1InMemoryCache:
    return L1InMemoryCache(max_size=100, ttl_seconds=300)


@pytest.fixture
def l2() -> MockL2Cache:
    return MockL2Cache()


@pytest.fixture
def cache(l1: L1InMemoryCache, l2: MockL2Cache) -> MultiLayerCache:
    return MultiLayerCache(l1_cache=l1, l2_cache=l2, enable_metrics=True)


@pytest.fixture
def cache_l1_only(l1: L1InMemoryCache) -> MultiLayerCache:
    return MultiLayerCache(l1_cache=l1, l2_cache=None, enable_metrics=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetSet:
    @pytest.mark.asyncio
    async def test_set_then_l1_hit(self, cache: MultiLayerCache) -> None:
        key = await cache.set("hello", {"answer": "world"})
        entry = await cache.get("hello")
        assert entry is not None
        assert entry.response == {"answer": "world"}
        metrics = cache.get_metrics()
        assert metrics is not None
        assert metrics.l1_hits == 1

    @pytest.mark.asyncio
    async def test_miss_returns_none(self, cache: MultiLayerCache) -> None:
        entry = await cache.get("nonexistent")
        assert entry is None
        metrics = cache.get_metrics()
        assert metrics is not None
        assert metrics.total_misses == 1

    @pytest.mark.asyncio
    async def test_l2_hit_promotes_to_l1(
        self, l1: L1InMemoryCache, l2: MockL2Cache, cache: MultiLayerCache
    ) -> None:
        # Manually store only in L2
        entry = CacheEntry(key="mlc:test123", query="test", response="from_l2")
        await l2.set(entry)

        # Generate key for "test" query to match
        real_key = cache._generate_key("test")
        entry_real = CacheEntry(key=real_key, query="test", response="from_l2")
        await l2.set(entry_real)

        result = await cache.get("test")
        assert result is not None
        assert result.response == "from_l2"

        metrics = cache.get_metrics()
        assert metrics is not None
        assert metrics.l2_hits == 1

        # Now L1 should have it (promoted)
        result2 = await cache.get("test")
        assert result2 is not None
        assert metrics.l1_hits == 1

    @pytest.mark.asyncio
    async def test_l1_only_mode_miss(self, cache_l1_only: MultiLayerCache) -> None:
        result = await cache_l1_only.get("no_l2_query")
        assert result is None


class TestInvalidate:
    @pytest.mark.asyncio
    async def test_invalidate_removes_from_both_layers(self, cache: MultiLayerCache) -> None:
        await cache.set("to_delete", "value")
        deleted = await cache.invalidate("to_delete")
        assert deleted is True
        assert await cache.get("to_delete") is None

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent(self, cache: MultiLayerCache) -> None:
        deleted = await cache.invalidate("does_not_exist")
        assert deleted is False


class TestClear:
    @pytest.mark.asyncio
    async def test_clear_all_layers(self, cache: MultiLayerCache) -> None:
        await cache.set("q1", "r1")
        await cache.set("q2", "r2")
        result = await cache.clear()
        assert result["l1"] >= 2
        assert await cache.get("q1") is None


class TestKeyGeneration:
    def test_key_includes_kb_ids(self) -> None:
        k1 = MultiLayerCache._generate_key("hello", kb_ids=["kb1"])
        k2 = MultiLayerCache._generate_key("hello", kb_ids=["kb2"])
        assert k1 != k2
        assert k1.startswith("mlc:")

    def test_key_includes_top_k(self) -> None:
        k1 = MultiLayerCache._generate_key("hello", top_k=5)
        k2 = MultiLayerCache._generate_key("hello", top_k=10)
        assert k1 != k2

    def test_key_deterministic(self) -> None:
        k1 = MultiLayerCache._generate_key("hello", kb_ids=["a"], top_k=3)
        k2 = MultiLayerCache._generate_key("hello", kb_ids=["a"], top_k=3)
        assert k1 == k2


class TestGetOrCompute:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_compute(self, cache: MultiLayerCache) -> None:
        await cache.set("cached_q", "cached_r")
        compute_fn = AsyncMock(return_value="computed")

        result, hit = await cache.get_or_compute("cached_q", compute_fn)
        assert hit is True
        assert result == "cached_r"
        compute_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cache_miss_calls_compute(self, cache: MultiLayerCache) -> None:
        compute_fn = AsyncMock(return_value="fresh_result")

        result, hit = await cache.get_or_compute("new_q", compute_fn)
        assert hit is False
        assert result == "fresh_result"
        compute_fn.assert_awaited_once()

        # Subsequent call should hit cache
        result2, hit2 = await cache.get_or_compute("new_q", compute_fn)
        assert hit2 is True
        assert result2 == "fresh_result"


class TestMetrics:
    @pytest.mark.asyncio
    async def test_metrics_tracking(self, cache: MultiLayerCache) -> None:
        await cache.set("m1", "v1")
        await cache.get("m1")  # L1 hit
        await cache.get("unknown")  # miss

        metrics = cache.get_metrics()
        assert metrics is not None
        assert metrics.l1_hits == 1
        assert metrics.total_misses == 1
        assert metrics.hit_rate > 0

    @pytest.mark.asyncio
    async def test_reset_metrics(self, cache: MultiLayerCache) -> None:
        await cache.set("m1", "v1")
        await cache.get("m1")
        cache.reset_metrics()
        metrics = cache.get_metrics()
        assert metrics is not None
        assert metrics.l1_hits == 0
        assert metrics.total_misses == 0
