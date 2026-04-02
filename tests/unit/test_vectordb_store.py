"""Unit tests for src/vectordb/store.py -- QdrantStoreOperations."""

from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.vectordb.client import (
    ADMIN_STATS_CACHE_TTL_S,
    DEFAULT_HYDRATION_EXCLUDE_FIELDS,
    QdrantClientProvider,
    QdrantConfig,
    QdrantSearchResult,
)
from src.vectordb.collections import QdrantCollectionManager
from src.vectordb.store import QdrantStoreOperations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config() -> QdrantConfig:
    return QdrantConfig(
        url="http://localhost:6333",
        dense_dimension=1024,
        dense_vector_name="bge_dense",
        sparse_vector_name="bge_sparse",
        collection_prefix="kb",
        embedding_version_tracking_enabled=False,
    )


@pytest.fixture()
def provider(config: QdrantConfig) -> QdrantClientProvider:
    p = QdrantClientProvider(config=config)
    p._client = AsyncMock()
    return p


@pytest.fixture()
def collection_mgr(provider: QdrantClientProvider) -> QdrantCollectionManager:
    mgr = QdrantCollectionManager(provider)
    mgr._collection_exists_cache.add("kb_test")
    return mgr


@pytest.fixture()
def store(
    provider: QdrantClientProvider,
    collection_mgr: QdrantCollectionManager,
) -> QdrantStoreOperations:
    return QdrantStoreOperations(provider, collection_mgr)


# ---------------------------------------------------------------------------
# Admin stats cache
# ---------------------------------------------------------------------------

class TestAdminStatsCache:
    def test_get_set_cached_stat(self, store: QdrantStoreOperations):
        assert store._get_cached_stat("foo") is None
        store._set_cached_stat("foo", 42)
        assert store._get_cached_stat("foo") == 42

    def test_get_cached_stat_expired(self, store: QdrantStoreOperations):
        store._admin_stats_cache["expired"] = (99, time.monotonic() - 10)
        assert store._get_cached_stat("expired") is None
        assert "expired" not in store._admin_stats_cache

    def test_invalidate_cache(self, store: QdrantStoreOperations):
        store._set_cached_stat("a", 1)
        store._set_cached_stat("b", 2)
        store.invalidate_admin_stats_cache()
        assert store._get_cached_stat("a") is None
        assert store._get_cached_stat("b") is None


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

class TestUpsert:
    @pytest.mark.asyncio
    async def test_upsert_returns_point_id(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.upsert = AsyncMock()

        pid = await store.upsert(
            kb_id="test",
            content="hello world",
            dense_vector=[0.1] * 1024,
            metadata={"source_uri": "http://example.com"},
        )
        assert pid  # non-empty string
        client.upsert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_with_sparse_vector(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.upsert = AsyncMock()

        pid = await store.upsert(
            kb_id="test",
            content="sparse test",
            dense_vector=[0.1] * 1024,
            sparse_vector={5: 0.8, 10: 0.3},
        )
        assert pid
        call_args = client.upsert.call_args
        points = call_args.kwargs.get("points") or call_args[1].get("points")
        point = points[0]
        assert "bge_sparse" in point.vector

    @pytest.mark.asyncio
    async def test_upsert_with_custom_point_id(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.upsert = AsyncMock()

        pid = await store.upsert(
            kb_id="test",
            content="custom id",
            dense_vector=[0.1] * 1024,
            point_id="my-custom-id",
        )
        assert pid == "my-custom-id"

    @pytest.mark.asyncio
    async def test_upsert_dimension_mismatch_logs_warning(
        self, store: QdrantStoreOperations, provider, caplog
    ):
        client = provider._client
        client.upsert = AsyncMock()

        import logging
        with caplog.at_level(logging.WARNING):
            await store.upsert(
                kb_id="test",
                content="wrong dim",
                dense_vector=[0.1] * 512,  # wrong dimension
            )
        assert "dimension mismatch" in caplog.text.lower()


# ---------------------------------------------------------------------------
# Upsert batch
# ---------------------------------------------------------------------------

class TestUpsertBatch:
    @pytest.mark.asyncio
    async def test_upsert_batch_empty(self, store: QdrantStoreOperations, provider):
        result = await store.upsert_batch(kb_id="test", items=[])
        assert result == []

    @pytest.mark.asyncio
    async def test_upsert_batch_valid_items(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.upsert = AsyncMock()

        items = [
            {
                "content": f"item {i}",
                "dense_vector": [0.1] * 1024,
                "point_id": f"pid-{i}",
            }
            for i in range(3)
        ]
        ids = await store.upsert_batch(kb_id="test", items=items)
        assert ids == ["pid-0", "pid-1", "pid-2"]
        client.upsert.assert_awaited()

    @pytest.mark.asyncio
    async def test_upsert_batch_skips_empty_vectors(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.upsert = AsyncMock()

        items = [
            {"content": "no vec", "dense_vector": [], "point_id": "bad"},
            {"content": "good", "dense_vector": [0.1] * 1024, "point_id": "good"},
        ]
        ids = await store.upsert_batch(kb_id="test", items=items)
        assert ids == ["good"]

    @pytest.mark.asyncio
    async def test_upsert_batch_all_invalid_returns_empty(self, store: QdrantStoreOperations, provider):
        items = [
            {"content": "bad1", "dense_vector": None},
            {"content": "bad2", "dense_vector": []},
        ]
        ids = await store.upsert_batch(kb_id="test", items=items)
        assert ids == []

    @pytest.mark.asyncio
    async def test_upsert_batch_with_colbert_and_sparse(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.upsert = AsyncMock()

        items = [
            {
                "content": "full",
                "dense_vector": [0.1] * 1024,
                "sparse_vector": {1: 0.5, 2: 0.3},
                "colbert_vectors": [[0.1] * 128],
            }
        ]
        ids = await store.upsert_batch(kb_id="test", items=items)
        assert len(ids) == 1


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_by_filter_empty_conditions(self, store: QdrantStoreOperations):
        result = await store.delete_by_filter(kb_id="test", filter_conditions={})
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_by_filter_simple(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.delete = AsyncMock()

        result = await store.delete_by_filter(
            kb_id="test",
            filter_conditions={"source_uri": "http://example.com"},
        )
        assert result is True
        client.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_by_filter_with_exclude_ids(self, store: QdrantStoreOperations, provider):
        client = provider._client
        pt1 = SimpleNamespace(id="keep-1")
        pt2 = SimpleNamespace(id="stale-1")
        client.scroll = AsyncMock(return_value=([pt1, pt2], None))
        client.delete = AsyncMock()

        result = await store.delete_by_filter(
            kb_id="test",
            filter_conditions={"source_uri": "x"},
            exclude_point_ids={"keep-1"},
        )
        assert result is True
        client.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_by_filter_collection_not_found(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.delete = AsyncMock(side_effect=Exception("Collection doesn't exist"))

        result = await store.delete_by_filter(
            kb_id="test",
            filter_conditions={"key": "val"},
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_by_filter_unexpected_error(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.delete = AsyncMock(side_effect=Exception("Network error"))

        result = await store.delete_by_filter(
            kb_id="test",
            filter_conditions={"key": "val"},
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_by_kb(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.delete_collection = AsyncMock()

        result = await store.delete_by_kb("test")
        assert result is True
        client.delete_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_by_kb_error(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.delete_collection = AsyncMock(side_effect=Exception("fail"))

        result = await store.delete_by_kb("test")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_points(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.delete = AsyncMock()

        result = await store.delete_points("test", ["id1", "id2"])
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_points_error(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.delete = AsyncMock(side_effect=Exception("fail"))

        result = await store.delete_points("test", ["id1"])
        assert result is False


# ---------------------------------------------------------------------------
# Fetch by IDs
# ---------------------------------------------------------------------------

class TestFetchByIds:
    @pytest.mark.asyncio
    async def test_fetch_by_ids_empty(self, store: QdrantStoreOperations):
        result = await store.fetch_by_ids("test", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_by_ids_returns_results(self, store: QdrantStoreOperations, provider, collection_mgr):
        record = SimpleNamespace(
            id="abc", payload={"content": "hello", "kb_id": "test"}, score=None
        )
        client = provider._client
        client.retrieve = AsyncMock(return_value=[record])
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))

        results = await store.fetch_by_ids("test", ["abc"])
        assert len(results) == 1
        assert results[0].point_id == "abc"
        assert results[0].content == "hello"

    @pytest.mark.asyncio
    async def test_fetch_by_ids_error_returns_empty(self, store: QdrantStoreOperations, provider):
        client = provider._client
        client.retrieve = AsyncMock(side_effect=Exception("fail"))
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))

        result = await store.fetch_by_ids("test", ["id1"])
        assert result == []


# ---------------------------------------------------------------------------
# Count / Statistics
# ---------------------------------------------------------------------------

class TestCountStatistics:
    @pytest.mark.asyncio
    async def test_count_returns_value(self, store: QdrantStoreOperations, provider, collection_mgr):
        client = provider._client
        client.count = AsyncMock(return_value=SimpleNamespace(count=42))
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.get_aliases = AsyncMock(return_value=SimpleNamespace(aliases=[]))

        result = await store.count("test")
        assert result == 42

    @pytest.mark.asyncio
    async def test_count_uses_cache(self, store: QdrantStoreOperations, provider, collection_mgr):
        client = provider._client
        client.count = AsyncMock(return_value=SimpleNamespace(count=42))
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.get_aliases = AsyncMock(return_value=SimpleNamespace(aliases=[]))

        await store.count("test")
        result2 = await store.count("test")
        assert result2 == 42
        # Second call should hit cache
        assert client.count.await_count == 1

    @pytest.mark.asyncio
    async def test_count_error_returns_zero(self, store: QdrantStoreOperations, provider, collection_mgr):
        client = provider._client
        client.count = AsyncMock(side_effect=Exception("fail"))
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.get_aliases = AsyncMock(return_value=SimpleNamespace(aliases=[]))

        result = await store.count("test")
        assert result == 0

    @pytest.mark.asyncio
    async def test_count_distinct_documents(self, store: QdrantStoreOperations, provider, collection_mgr):
        hit1 = SimpleNamespace(value="http://a.com")
        hit2 = SimpleNamespace(value="http://b.com#sec1")
        hit3 = SimpleNamespace(value="http://b.com#sec2")
        client = provider._client
        client.facet = AsyncMock(return_value=SimpleNamespace(hits=[hit1, hit2, hit3]))
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.get_aliases = AsyncMock(return_value=SimpleNamespace(aliases=[]))

        result = await store.count_distinct_documents("test")
        assert result == 2  # b.com deduped

    @pytest.mark.asyncio
    async def test_count_distinct_documents_facet_error(self, store: QdrantStoreOperations, provider, collection_mgr):
        client = provider._client
        client.facet = AsyncMock(side_effect=Exception("facet not supported"))
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.get_aliases = AsyncMock(return_value=SimpleNamespace(aliases=[]))

        result = await store.count_distinct_documents("test")
        assert result == 0

    @pytest.mark.asyncio
    async def test_facet_l1_categories(self, store: QdrantStoreOperations, provider, collection_mgr):
        hit1 = SimpleNamespace(value="인프라", count=10)
        hit2 = SimpleNamespace(value="개발", count=5)
        client = provider._client
        client.facet = AsyncMock(return_value=SimpleNamespace(hits=[hit1, hit2]))
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.get_aliases = AsyncMock(return_value=SimpleNamespace(aliases=[]))

        result = await store.facet_l1_categories("test")
        assert result == {"인프라": 10, "개발": 5}


# ---------------------------------------------------------------------------
# Source URI listing
# ---------------------------------------------------------------------------

class TestListDistinctSourceUris:
    @pytest.mark.asyncio
    async def test_list_via_facet(self, store: QdrantStoreOperations, provider, collection_mgr):
        hit = SimpleNamespace(value="http://a.com")
        client = provider._client
        client.facet = AsyncMock(return_value=SimpleNamespace(hits=[hit]))
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.get_aliases = AsyncMock(return_value=SimpleNamespace(aliases=[]))

        result = await store.list_distinct_source_uris("test")
        assert result == ["http://a.com"]

    @pytest.mark.asyncio
    async def test_list_facet_fallback_to_scroll(self, store: QdrantStoreOperations, provider, collection_mgr):
        client = provider._client
        client.facet = AsyncMock(side_effect=Exception("not supported"))
        pt = SimpleNamespace(payload={"source_uri": "http://b.com"})
        client.scroll = AsyncMock(return_value=([pt], None))
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.get_aliases = AsyncMock(return_value=SimpleNamespace(aliases=[]))

        result = await store.list_distinct_source_uris("test")
        assert result == ["http://b.com"]


# ---------------------------------------------------------------------------
# Scroll by source URIs
# ---------------------------------------------------------------------------

class TestScrollBySourceUris:
    @pytest.mark.asyncio
    async def test_scroll_empty_uris(self, store: QdrantStoreOperations):
        result = await store.scroll_by_source_uris("test", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_scroll_returns_results(self, store: QdrantStoreOperations, provider, collection_mgr):
        pt = SimpleNamespace(id="p1", payload={"content": "data", "kb_id": "test"})
        client = provider._client
        client.scroll = AsyncMock(return_value=([pt], None))
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.get_aliases = AsyncMock(return_value=SimpleNamespace(aliases=[]))

        results = await store.scroll_by_source_uris("test", ["http://a.com"])
        assert len(results) == 1
        assert results[0].point_id == "p1"
        assert results[0].score == 0.75

    @pytest.mark.asyncio
    async def test_scroll_error_returns_empty(self, store: QdrantStoreOperations, provider, collection_mgr):
        client = provider._client
        client.scroll = AsyncMock(side_effect=Exception("fail"))
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.get_aliases = AsyncMock(return_value=SimpleNamespace(aliases=[]))

        result = await store.scroll_by_source_uris("test", ["http://a.com"])
        assert result == []
