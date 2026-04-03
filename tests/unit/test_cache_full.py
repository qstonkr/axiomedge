"""Comprehensive tests for src/cache/ modules."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cache.cache_types import (
    CacheDomain,
    CacheEntry,
    CacheMetrics,
    ICacheLayer,
    _utc_now,
)
from src.cache.l1_memory_cache import L1InMemoryCache
from src.cache.l2_semantic_cache import L2SemanticCache, _cosine_similarity
from src.cache.multi_layer_cache import MultiLayerCache
from src.cache.idempotency_cache import IdempotencyCache, request_hash


# ===========================================================================
# cache_types.py
# ===========================================================================


class TestUtcNow:
    def test_returns_utc(self):
        now = _utc_now()
        assert now.tzinfo is not None
        assert now.tzinfo == timezone.utc


class TestCacheDomain:
    def test_values(self):
        assert CacheDomain.POLICY.value == "policy"
        assert CacheDomain.CODE.value == "code"
        assert CacheDomain.KB_SEARCH.value == "kb_search"
        assert CacheDomain.GENERAL.value == "general"


class TestCacheEntry:
    def test_defaults(self):
        e = CacheEntry(key="k", query="q", response="r")
        assert e.embedding is None
        assert e.similarity == 1.0
        assert e.domain == CacheDomain.GENERAL
        assert e.metadata == {}
        assert e.hit_count == 0
        assert e.last_accessed_at is None
        assert isinstance(e.created_at, datetime)

    def test_to_dict(self):
        e = CacheEntry(key="k", query="q", response={"data": 1}, similarity=0.9)
        d = e.to_dict()
        assert d["key"] == "k"
        assert d["response"] == {"data": 1}
        assert d["similarity"] == 0.9
        assert d["domain"] == "general"
        assert "created_at" in d

    def test_custom_domain(self):
        e = CacheEntry(key="k", query="q", response="r", domain=CacheDomain.POLICY)
        assert e.domain == CacheDomain.POLICY


class TestCacheMetrics:
    def test_defaults(self):
        m = CacheMetrics()
        assert m.l1_hits == 0
        assert m.l2_hits == 0
        assert m.total_misses == 0

    def test_total_hits(self):
        m = CacheMetrics(l1_hits=3, l2_hits=2)
        assert m.total_hits == 5

    def test_total_requests(self):
        m = CacheMetrics(l1_hits=1, l2_hits=2, total_misses=3)
        assert m.total_requests == 6

    def test_hit_rate_zero_requests(self):
        m = CacheMetrics()
        assert m.hit_rate == 0.0

    def test_hit_rate(self):
        m = CacheMetrics(l1_hits=2, l2_hits=3, total_misses=5)
        assert m.hit_rate == pytest.approx(0.5)

    def test_to_dict(self):
        m = CacheMetrics(l1_hits=1, l2_hits=1, total_misses=2)
        d = m.to_dict()
        assert d["total_hits"] == 2
        assert d["total_requests"] == 4
        assert d["hit_rate"] == 0.5


class TestICacheLayer:
    def test_abstract(self):
        with pytest.raises(TypeError):
            ICacheLayer()  # type: ignore[abstract]


# ===========================================================================
# L1InMemoryCache
# ===========================================================================


class TestL1InMemoryCache:
    async def test_get_miss(self):
        cache = L1InMemoryCache()
        result = await cache.get("nonexistent")
        assert result is None

    async def test_set_and_get(self):
        cache = L1InMemoryCache()
        entry = CacheEntry(key="k1", query="q", response="resp")
        await cache.set(entry)
        result = await cache.get("k1")
        assert result is not None
        assert result.response == "resp"
        assert result.hit_count == 1

    async def test_ttl_expiry(self):
        cache = L1InMemoryCache(ttl_seconds=0)
        entry = CacheEntry(key="k1", query="q", response="r")
        await cache.set(entry, ttl_seconds=0)
        # TTL=0 means it expires immediately after time.time() > expire_time
        # Need a tiny delay
        await asyncio.sleep(0.01)
        result = await cache.get("k1")
        assert result is None

    async def test_lru_eviction(self):
        cache = L1InMemoryCache(max_size=2)
        await cache.set(CacheEntry(key="a", query="q", response="1"))
        await cache.set(CacheEntry(key="b", query="q", response="2"))
        await cache.set(CacheEntry(key="c", query="q", response="3"))
        # "a" should be evicted (LRU)
        assert await cache.get("a") is None
        assert await cache.get("b") is not None
        assert await cache.get("c") is not None

    async def test_delete_exists(self):
        cache = L1InMemoryCache()
        await cache.set(CacheEntry(key="k", query="q", response="r"))
        assert await cache.delete("k") is True
        assert await cache.get("k") is None

    async def test_delete_not_exists(self):
        cache = L1InMemoryCache()
        assert await cache.delete("nope") is False

    async def test_delete_by_prefix(self):
        cache = L1InMemoryCache()
        await cache.set(CacheEntry(key="prefix:1", query="q", response="r"))
        await cache.set(CacheEntry(key="prefix:2", query="q", response="r"))
        await cache.set(CacheEntry(key="other:1", query="q", response="r"))
        deleted = await cache.delete_by_prefix("prefix:")
        assert deleted == 2
        assert await cache.get("other:1") is not None

    async def test_invalidate_by_metadata_value(self):
        cache = L1InMemoryCache()
        await cache.set(CacheEntry(key="k1", query="q", response="r", metadata={"kb_id": "kb1"}))
        await cache.set(CacheEntry(key="k2", query="q", response="r", metadata={"kb_id": "kb2"}))
        deleted = await cache.invalidate_by_metadata_value("kb_id", "kb1")
        assert deleted == 1
        assert await cache.get("k1") is None
        assert await cache.get("k2") is not None

    async def test_invalidate_by_metadata_list_value(self):
        cache = L1InMemoryCache()
        await cache.set(CacheEntry(key="k1", query="q", response="r", metadata={"tags": ["a", "b"]}))
        deleted = await cache.invalidate_by_metadata_value("tags", "a")
        assert deleted == 1

    async def test_invalidate_by_metadata_also_removes_expired(self):
        cache = L1InMemoryCache(ttl_seconds=0)
        await cache.set(CacheEntry(key="k1", query="q", response="r", metadata={"x": "y"}))
        await asyncio.sleep(0.01)
        # k1 is expired, invalidation should clean it up
        deleted = await cache.invalidate_by_metadata_value("x", "y")
        assert deleted >= 1

    async def test_clear(self):
        cache = L1InMemoryCache()
        await cache.set(CacheEntry(key="k1", query="q", response="r"))
        await cache.set(CacheEntry(key="k2", query="q", response="r"))
        count = await cache.clear()
        assert count == 2
        assert len(cache) == 0

    def test_stats(self):
        cache = L1InMemoryCache(max_size=100, ttl_seconds=60)
        s = cache.stats()
        assert s["size"] == 0
        assert s["max_size"] == 100
        assert s["ttl_seconds"] == 60

    def test_len(self):
        cache = L1InMemoryCache()
        assert len(cache) == 0

    async def test_custom_ttl_on_set(self):
        cache = L1InMemoryCache(ttl_seconds=300)
        entry = CacheEntry(key="k1", query="q", response="r")
        await cache.set(entry, ttl_seconds=1)
        # Should be retrievable now
        assert await cache.get("k1") is not None


# ===========================================================================
# _cosine_similarity
# ===========================================================================


class TestCosineSimilarity:
    def test_identical(self):
        assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_different_lengths(self):
        assert _cosine_similarity([1.0], [1.0, 0.0]) == 0.0

    def test_empty(self):
        assert _cosine_similarity([], []) == 0.0

    def test_zero_vector(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


# ===========================================================================
# L2SemanticCache (mocked Redis)
# ===========================================================================


class TestL2SemanticCache:
    def _make_cache(self, redis_mock=None, embedding_provider=None):
        with patch("redis.asyncio.from_url", return_value=redis_mock or AsyncMock()):
            return L2SemanticCache(
                redis_url="redis://localhost:6379",
                embedding_provider=embedding_provider,
            )

    async def test_exact_match_hit(self):
        mock_redis = AsyncMock()
        stored = json.dumps({
            "query": "q",
            "response": "cached",
            "embedding": None,
            "domain": "general",
            "metadata": {},
        })
        mock_redis.get = AsyncMock(return_value=stored)
        cache = self._make_cache(redis_mock=mock_redis)

        result = await cache._exact_match("key1")
        assert result is not None
        assert result.response == "cached"
        assert result.hit_count == 1

    async def test_exact_match_miss(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        cache = self._make_cache(redis_mock=mock_redis)

        result = await cache._exact_match("missing")
        assert result is None

    async def test_exact_match_error(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=Exception("redis error"))
        cache = self._make_cache(redis_mock=mock_redis)

        result = await cache._exact_match("key")
        assert result is None

    async def test_get_policy_domain_exact_only(self):
        """Policy domain threshold >= 1.0 -> exact match only."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        cache = self._make_cache(redis_mock=mock_redis)

        result = await cache.get("key", query="q", domain=CacheDomain.POLICY)
        assert result is None

    async def test_get_no_embedding_provider(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        cache = self._make_cache(redis_mock=mock_redis, embedding_provider=None)

        result = await cache.get("key", query="q", domain=CacheDomain.GENERAL)
        assert result is None

    async def test_get_semantic_error_falls_back(self):
        mock_emb = AsyncMock()
        mock_emb.embed = AsyncMock(side_effect=Exception("embed fail"))
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        cache = self._make_cache(redis_mock=mock_redis, embedding_provider=mock_emb)

        result = await cache.get("key", query="q", domain=CacheDomain.GENERAL)
        assert result is None

    async def test_get_empty_embedding(self):
        mock_emb = AsyncMock()
        mock_emb.embed = AsyncMock(return_value=[])
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        cache = self._make_cache(redis_mock=mock_redis, embedding_provider=mock_emb)

        result = await cache.get("key", query="q", domain=CacheDomain.GENERAL)
        assert result is None

    async def test_set_basic(self):
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()
        cache = self._make_cache(redis_mock=mock_redis)

        entry = CacheEntry(key="k", query="q", response="r")
        await cache.set(entry)
        mock_redis.setex.assert_called_once()

    async def test_set_with_embedding_provider(self):
        mock_emb = AsyncMock()
        mock_emb.embed = AsyncMock(return_value=[0.1, 0.2])
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()
        cache = self._make_cache(redis_mock=mock_redis, embedding_provider=mock_emb)

        entry = CacheEntry(key="k", query="q", response="r")
        await cache.set(entry)
        assert entry.embedding == [0.1, 0.2]

    async def test_set_embedding_error(self):
        mock_emb = AsyncMock()
        mock_emb.embed = AsyncMock(side_effect=Exception("fail"))
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()
        cache = self._make_cache(redis_mock=mock_redis, embedding_provider=mock_emb)

        entry = CacheEntry(key="k", query="q", response="r")
        await cache.set(entry)  # should not raise
        mock_redis.setex.assert_called_once()

    async def test_set_redis_error(self):
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock(side_effect=Exception("redis down"))
        cache = self._make_cache(redis_mock=mock_redis)

        entry = CacheEntry(key="k", query="q", response="r")
        await cache.set(entry)  # should not raise

    async def test_delete_success(self):
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(return_value=1)
        cache = self._make_cache(redis_mock=mock_redis)

        assert await cache.delete("k") is True

    async def test_delete_not_found(self):
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(return_value=0)
        cache = self._make_cache(redis_mock=mock_redis)

        assert await cache.delete("k") is False

    async def test_delete_error(self):
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(side_effect=Exception("err"))
        cache = self._make_cache(redis_mock=mock_redis)

        assert await cache.delete("k") is False

    async def test_clear(self):
        mock_redis = AsyncMock()

        async def mock_scan_iter(**kw):
            for key in ["knowledge:l2cache:k1", "knowledge:l2cache:k2"]:
                yield key

        mock_redis.scan_iter = mock_scan_iter
        mock_redis.delete = AsyncMock(return_value=2)
        cache = self._make_cache(redis_mock=mock_redis)

        count = await cache.clear()
        assert count == 2

    async def test_clear_empty(self):
        mock_redis = AsyncMock()

        async def mock_scan_iter(**kw):
            return
            yield  # make it an async generator

        mock_redis.scan_iter = mock_scan_iter
        cache = self._make_cache(redis_mock=mock_redis)

        count = await cache.clear()
        assert count == 0

    async def test_clear_error(self):
        mock_redis = AsyncMock()

        async def mock_scan_iter(**kw):
            raise Exception("scan error")
            yield  # noqa: unreachable

        mock_redis.scan_iter = mock_scan_iter
        cache = self._make_cache(redis_mock=mock_redis)

        count = await cache.clear()
        assert count == 0

    def test_stats(self):
        cache = self._make_cache()
        s = cache.stats()
        assert "prefix" in s
        assert "threshold" in s
        assert "has_embedding_provider" in s

    async def test_close(self):
        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock()
        cache = self._make_cache(redis_mock=mock_redis)
        await cache.close()
        mock_redis.aclose.assert_called_once()


# ===========================================================================
# MultiLayerCache
# ===========================================================================


class TestMultiLayerCache:
    async def test_l1_hit(self):
        l1 = L1InMemoryCache()
        entry = CacheEntry(key="", query="test", response="cached_r")
        mlc = MultiLayerCache(l1_cache=l1)
        # Manually generate key to set in L1
        key = mlc._generate_key("test")
        entry.key = key
        await l1.set(entry)

        result = await mlc.get("test")
        assert result is not None
        assert result.response == "cached_r"
        assert mlc._metrics.l1_hits == 1

    async def test_l1_miss_l2_hit(self):
        l1 = L1InMemoryCache()
        l2 = AsyncMock(spec=ICacheLayer)
        l2_entry = CacheEntry(key="k", query="test", response="from_l2", similarity=0.95)
        l2.get = AsyncMock(return_value=l2_entry)

        mlc = MultiLayerCache(l1_cache=l1, l2_cache=l2)
        result = await mlc.get("test")
        assert result is not None
        assert result.response == "from_l2"
        assert mlc._metrics.l2_hits == 1
        # L2 hit should be promoted to L1 under the entry's key
        l1_result = await l1.get(l2_entry.key)
        assert l1_result is not None

    async def test_both_miss(self):
        l1 = L1InMemoryCache()
        l2 = AsyncMock(spec=ICacheLayer)
        l2.get = AsyncMock(return_value=None)

        mlc = MultiLayerCache(l1_cache=l1, l2_cache=l2)
        result = await mlc.get("test")
        assert result is None
        assert mlc._metrics.total_misses == 1

    async def test_miss_no_l2(self):
        mlc = MultiLayerCache()
        result = await mlc.get("anything")
        assert result is None
        assert mlc._metrics.total_misses == 1

    async def test_set_write_through(self):
        l1 = AsyncMock(spec=ICacheLayer)
        l1.set = AsyncMock()
        l1.get = AsyncMock(return_value=None)
        l2 = AsyncMock(spec=ICacheLayer)
        l2.set = AsyncMock()
        l2.get = AsyncMock(return_value=None)

        mlc = MultiLayerCache(l1_cache=l1, l2_cache=l2)
        key = await mlc.set("query", "response")
        assert key.startswith("mlc:")
        l1.set.assert_called_once()
        l2.set.assert_called_once()

    async def test_set_l1_error_continues(self):
        l1 = AsyncMock(spec=ICacheLayer)
        l1.set = AsyncMock(side_effect=Exception("l1 fail"))
        l1.get = AsyncMock(return_value=None)
        l2 = AsyncMock(spec=ICacheLayer)
        l2.set = AsyncMock()

        mlc = MultiLayerCache(l1_cache=l1, l2_cache=l2)
        key = await mlc.set("q", "r")
        assert key  # should not raise
        l2.set.assert_called_once()

    async def test_set_with_embedding_provider(self):
        mock_emb = AsyncMock()
        mock_emb.embed = AsyncMock(return_value=[0.1, 0.2])
        mlc = MultiLayerCache(embedding_provider=mock_emb)
        await mlc.set("q", "r")
        mock_emb.embed.assert_called_once()

    async def test_invalidate(self):
        l1 = L1InMemoryCache()
        mlc = MultiLayerCache(l1_cache=l1)
        await mlc.set("q", "r")
        result = await mlc.invalidate("q")
        assert result is True

    async def test_invalidate_miss(self):
        mlc = MultiLayerCache()
        result = await mlc.invalidate("nonexistent")
        assert result is False

    async def test_invalidate_with_l2(self):
        l2 = AsyncMock(spec=ICacheLayer)
        l2.delete = AsyncMock(return_value=True)
        mlc = MultiLayerCache(l2_cache=l2)
        result = await mlc.invalidate("q")
        assert result is True

    async def test_invalidate_kb(self):
        l1 = L1InMemoryCache()
        await l1.set(CacheEntry(key="k1", query="q", response="r", metadata={"kb_id": "kb1"}))
        mlc = MultiLayerCache(l1_cache=l1)
        result = await mlc.invalidate_kb("kb1")
        assert result["l1"] == 1

    async def test_invalidate_kb_with_l2(self):
        l1 = L1InMemoryCache()
        l2 = AsyncMock()
        l2.invalidate_by_metadata_value = AsyncMock(return_value=3)
        mlc = MultiLayerCache(l1_cache=l1, l2_cache=l2)
        result = await mlc.invalidate_kb("kb1")
        assert result["l2"] == 3

    async def test_clear(self):
        l1 = L1InMemoryCache()
        await l1.set(CacheEntry(key="k", query="q", response="r"))
        mlc = MultiLayerCache(l1_cache=l1)
        result = await mlc.clear()
        assert result["l1"] == 1
        assert result["l2"] == 0

    async def test_clear_with_l2(self):
        l2 = AsyncMock(spec=ICacheLayer)
        l2.clear = AsyncMock(return_value=5)
        mlc = MultiLayerCache(l2_cache=l2)
        result = await mlc.clear()
        assert result["l2"] == 5

    def test_get_metrics(self):
        mlc = MultiLayerCache()
        m = mlc.get_metrics()
        assert isinstance(m, CacheMetrics)

    def test_get_metrics_disabled(self):
        mlc = MultiLayerCache(enable_metrics=False)
        assert mlc.get_metrics() is None

    def test_reset_metrics(self):
        mlc = MultiLayerCache()
        mlc._metrics.l1_hits = 10
        mlc.reset_metrics()
        assert mlc._metrics.l1_hits == 0

    def test_reset_metrics_disabled(self):
        mlc = MultiLayerCache(enable_metrics=False)
        mlc.reset_metrics()  # no error

    def test_stats(self):
        mlc = MultiLayerCache()
        s = mlc.stats()
        assert "l1" in s
        assert "metrics" in s

    def test_stats_with_l2(self):
        l2 = MagicMock()
        l2.stats = MagicMock(return_value={"prefix": "test"})
        mlc = MultiLayerCache(l2_cache=l2)
        s = mlc.stats()
        assert "l2" in s

    async def test_get_or_compute_cache_hit(self):
        l1 = L1InMemoryCache()
        mlc = MultiLayerCache(l1_cache=l1)
        await mlc.set("q", "cached")

        compute = AsyncMock(return_value="computed")
        result, hit = await mlc.get_or_compute("q", compute)
        assert result == "cached"
        assert hit is True
        compute.assert_not_called()

    async def test_get_or_compute_cache_miss(self):
        mlc = MultiLayerCache()
        compute = AsyncMock(return_value="computed")
        result, hit = await mlc.get_or_compute("q", compute)
        assert result == "computed"
        assert hit is False
        compute.assert_called_once()

    def test_generate_key_basic(self):
        key = MultiLayerCache._generate_key("hello")
        assert key.startswith("mlc:")

    def test_generate_key_with_kb_ids(self):
        k1 = MultiLayerCache._generate_key("q", kb_ids=["a", "b"])
        k2 = MultiLayerCache._generate_key("q", kb_ids=["b", "a"])
        assert k1 != k2  # ordered, not sorted

    def test_generate_key_with_top_k(self):
        k1 = MultiLayerCache._generate_key("q", top_k=5)
        k2 = MultiLayerCache._generate_key("q", top_k=10)
        assert k1 != k2

    def test_generate_key_deterministic(self):
        k1 = MultiLayerCache._generate_key("q", kb_ids=["a"], top_k=5)
        k2 = MultiLayerCache._generate_key("q", kb_ids=["a"], top_k=5)
        assert k1 == k2

    async def test_l2_version_mismatch_discards(self):
        l1 = L1InMemoryCache()
        l2 = AsyncMock(spec=ICacheLayer)
        stale_entry = CacheEntry(
            key="k", query="q",
            response={"_cache_version": "v1", "data": "old"},
            similarity=0.99,
        )
        l2.get = AsyncMock(return_value=stale_entry)

        mlc = MultiLayerCache(l1_cache=l1, l2_cache=l2)
        result = await mlc.get("q", cache_version="v2")
        assert result is None  # stale version discarded


# ===========================================================================
# IdempotencyCache
# ===========================================================================


class TestRequestHash:
    def test_basic(self):
        h = request_hash("hello")
        assert isinstance(h, str)
        assert len(h) == 24

    def test_deterministic(self):
        assert request_hash("q") == request_hash("q")

    def test_case_insensitive(self):
        assert request_hash("Hello") == request_hash("hello")

    def test_with_kb_ids(self):
        h1 = request_hash("q", kb_ids=["a", "b"])
        h2 = request_hash("q", kb_ids=["b", "a"])
        assert h1 == h2  # sorted

    def test_with_kwargs(self):
        h1 = request_hash("q", top_k=5)
        h2 = request_hash("q", top_k=10)
        assert h1 != h2


class TestIdempotencyCache:
    async def test_memory_new_request(self):
        cache = IdempotencyCache()
        assert await cache.check_and_set("hash1") is True

    async def test_memory_duplicate(self):
        cache = IdempotencyCache()
        await cache.check_and_set("hash1")
        assert await cache.check_and_set("hash1") is False

    async def test_memory_ttl_expiry(self):
        cache = IdempotencyCache(ttl_seconds=0)
        await cache.check_and_set("hash1")
        await asyncio.sleep(0.01)
        assert await cache.check_and_set("hash1") is True

    async def test_redis_new_request(self):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        cache = IdempotencyCache(redis_client=mock_redis)
        assert await cache.check_and_set("hash1") is True

    async def test_redis_duplicate(self):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=None)
        cache = IdempotencyCache(redis_client=mock_redis)
        assert await cache.check_and_set("hash1") is False

    async def test_redis_error_allows(self):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=Exception("fail"))
        cache = IdempotencyCache(redis_client=mock_redis)
        assert await cache.check_and_set("hash1") is True

    async def test_remove_redis(self):
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(return_value=1)
        cache = IdempotencyCache(redis_client=mock_redis)
        assert await cache.remove("hash1") is True

    async def test_remove_redis_not_found(self):
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(return_value=0)
        cache = IdempotencyCache(redis_client=mock_redis)
        assert await cache.remove("hash1") is False

    async def test_remove_redis_error(self):
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(side_effect=Exception("fail"))
        cache = IdempotencyCache(redis_client=mock_redis)
        assert await cache.remove("hash1") is False

    async def test_remove_memory(self):
        cache = IdempotencyCache()
        await cache.check_and_set("hash1")
        assert await cache.remove("hash1") is True

    async def test_remove_memory_not_found(self):
        cache = IdempotencyCache()
        assert await cache.remove("hash1") is False
