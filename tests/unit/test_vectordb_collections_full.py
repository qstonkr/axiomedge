"""Unit tests for src/vectordb/collections.py -- QdrantCollectionManager."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.vectordb.client import QdrantClientProvider, QdrantConfig
from src.vectordb.collections import QdrantCollectionManager


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
        collection_name_overrides={"custom": "my_custom_collection"},
    )


@pytest.fixture()
def provider(config: QdrantConfig) -> QdrantClientProvider:
    p = QdrantClientProvider(config=config)
    p._client = AsyncMock()
    return p


@pytest.fixture()
def mgr(provider: QdrantClientProvider) -> QdrantCollectionManager:
    return QdrantCollectionManager(provider)


# ---------------------------------------------------------------------------
# Collection name resolution
# ---------------------------------------------------------------------------

class TestCollectionNameResolution:
    def test_get_collection_name_default(self, mgr: QdrantCollectionManager):
        assert mgr.get_collection_name("infra") == "kb_infra"

    def test_get_collection_name_with_special_chars(self, mgr: QdrantCollectionManager):
        assert mgr.get_collection_name("my-kb name") == "kb_my_kb_name"

    def test_get_collection_name_override(self, mgr: QdrantCollectionManager):
        assert mgr.get_collection_name("custom") == "my_custom_collection"

    def test_get_write_collection_name(self, mgr: QdrantCollectionManager):
        assert mgr.get_write_collection_name("test") == mgr.get_collection_name("test")

    def test_get_live_alias_name(self, mgr: QdrantCollectionManager):
        assert mgr.get_live_alias_name("test") == "kb_test__live"

    def test_get_versioned_live_collection_name(self, mgr: QdrantCollectionManager):
        result = mgr.get_versioned_live_collection_name("test", "v2-beta")
        assert result == "kb_test__live_v2_beta"


# ---------------------------------------------------------------------------
# Collection existence
# ---------------------------------------------------------------------------

class TestCollectionExistence:
    @pytest.mark.asyncio
    async def test_collection_exists_cache_hit(self, mgr: QdrantCollectionManager):
        mgr._collection_exists_cache.add("kb_test")
        assert await mgr.collection_exists("test") is True

    @pytest.mark.asyncio
    async def test_collection_exists_remote_check(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        col = SimpleNamespace(name="kb_test")
        client.get_collections = AsyncMock(
            return_value=SimpleNamespace(collections=[col])
        )

        result = await mgr.collection_exists("test")
        assert result is True
        assert "kb_test" in mgr._collection_exists_cache

    @pytest.mark.asyncio
    async def test_collection_exists_false(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        client.get_collections = AsyncMock(
            return_value=SimpleNamespace(collections=[])
        )

        result = await mgr.collection_exists("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_get_existing_collection_names_with_ttl(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        col = SimpleNamespace(name="kb_a")
        client.get_collections = AsyncMock(
            return_value=SimpleNamespace(collections=[col])
        )
        client.get_aliases = AsyncMock(
            return_value=SimpleNamespace(aliases=[])
        )

        names = await mgr.get_existing_collection_names(ttl=300)
        assert "kb_a" in names

        # Second call should use cache
        names2 = await mgr.get_existing_collection_names(ttl=300)
        assert names2 == names
        assert client.get_collections.await_count == 1

    @pytest.mark.asyncio
    async def test_get_existing_collection_names_includes_aliases(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        col = SimpleNamespace(name="kb_a")
        alias = SimpleNamespace(alias_name="kb_a__live", collection_name="kb_a")
        client.get_collections = AsyncMock(
            return_value=SimpleNamespace(collections=[col])
        )
        client.get_aliases = AsyncMock(
            return_value=SimpleNamespace(aliases=[alias])
        )

        names = await mgr.get_existing_collection_names(ttl=0)
        assert "kb_a" in names
        assert "kb_a__live" in names


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------

class TestCacheInvalidation:
    def test_invalidate_cache(self, mgr: QdrantCollectionManager):
        mgr._collection_exists_cache.add("foo")
        mgr._collection_cache_ts = time.monotonic()
        mgr._alias_resolution_cache["kb"] = ("name", time.time() + 60)
        mgr._alias_target_cache["alias"] = "target"
        mgr._missing_alias_cache.add("missing")

        mgr.invalidate_cache()

        assert len(mgr._collection_exists_cache) == 0
        assert mgr._collection_cache_ts == 0.0
        assert len(mgr._alias_resolution_cache) == 0
        assert len(mgr._alias_target_cache) == 0
        assert len(mgr._missing_alias_cache) == 0


# ---------------------------------------------------------------------------
# ensure_collection
# ---------------------------------------------------------------------------

class TestEnsureCollection:
    @pytest.mark.asyncio
    async def test_ensure_collection_already_cached(self, mgr: QdrantCollectionManager, provider):
        mgr._collection_exists_cache.add("kb_test")
        await mgr.ensure_collection("test")
        provider._client.create_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ensure_collection_creates_new(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        client.get_collections = AsyncMock(
            return_value=SimpleNamespace(collections=[])
        )
        client.create_collection = AsyncMock()
        client.create_payload_index = AsyncMock()

        await mgr.ensure_collection("new_kb")
        client.create_collection.assert_awaited_once()
        assert "kb_new_kb" in mgr._collection_exists_cache

    @pytest.mark.asyncio
    async def test_ensure_collection_alias_conflict(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        client.get_collections = AsyncMock(
            return_value=SimpleNamespace(collections=[])
        )
        # First create fails with alias conflict, second succeeds
        client.create_collection = AsyncMock(
            side_effect=[
                Exception("Alias with the same name already exists"),
                None,
            ]
        )
        client.update_collection_aliases = AsyncMock()
        client.create_payload_index = AsyncMock()

        await mgr.ensure_collection("conflict")
        assert client.create_collection.await_count == 2

    @pytest.mark.asyncio
    async def test_ensure_collection_validates_existing_schema(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        col = SimpleNamespace(name="kb_existing")
        client.get_collections = AsyncMock(
            return_value=SimpleNamespace(collections=[col])
        )
        client.get_collection = AsyncMock(
            return_value=SimpleNamespace(
                config=SimpleNamespace(
                    params=SimpleNamespace(
                        vectors={"bge_dense": SimpleNamespace(size=1024)}
                    )
                )
            )
        )
        client.create_payload_index = AsyncMock()

        await mgr.ensure_collection("existing")
        client.create_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ensure_collection_schema_mismatch_empty_recreate(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        col = SimpleNamespace(name="kb_bad_schema")
        client.get_collections = AsyncMock(
            return_value=SimpleNamespace(collections=[col])
        )
        client.get_collection = AsyncMock(
            return_value=SimpleNamespace(
                config=SimpleNamespace(
                    params=SimpleNamespace(
                        vectors={"wrong_name": SimpleNamespace(size=1024)}
                    )
                )
            )
        )
        client.count = AsyncMock(return_value=SimpleNamespace(count=0))
        client.delete_collection = AsyncMock()
        client.create_collection = AsyncMock()
        client.create_payload_index = AsyncMock()

        await mgr.ensure_collection("bad_schema")
        client.delete_collection.assert_awaited()
        client.create_collection.assert_awaited()


# ---------------------------------------------------------------------------
# Alias management
# ---------------------------------------------------------------------------

class TestAliasManagement:
    @pytest.mark.asyncio
    async def test_resolve_collection_name_no_alias(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("not found"))

        result = await mgr.resolve_collection_name("test")
        assert result == "kb_test"

    @pytest.mark.asyncio
    async def test_resolve_collection_name_with_alias(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        client.get_collection = AsyncMock(return_value=SimpleNamespace())

        result = await mgr.resolve_collection_name("test")
        assert result == "kb_test__alias"

    @pytest.mark.asyncio
    async def test_resolve_collection_name_uses_cache(self, mgr: QdrantCollectionManager, provider):
        mgr._alias_resolution_cache["test"] = ("kb_test__alias", time.time() + 120)

        result = await mgr.resolve_collection_name("test")
        assert result == "kb_test__alias"
        provider._client.get_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resolve_collection_name_expired_cache(self, mgr: QdrantCollectionManager, provider):
        mgr._alias_resolution_cache["test"] = ("kb_test__alias", time.time() - 10)
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("not found"))

        result = await mgr.resolve_collection_name("test")
        assert result == "kb_test"

    @pytest.mark.asyncio
    async def test_get_alias_target(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        alias_item = SimpleNamespace(alias_name="kb_test__live", collection_name="kb_test_v2")
        client.get_aliases = AsyncMock(
            return_value=SimpleNamespace(aliases=[alias_item])
        )

        result = await mgr._get_alias_target("kb_test__live")
        assert result == "kb_test_v2"

    @pytest.mark.asyncio
    async def test_get_alias_target_missing(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        client.get_aliases = AsyncMock(return_value=SimpleNamespace(aliases=[]))

        result = await mgr._get_alias_target("nonexistent")
        assert result is None
        assert "nonexistent" in mgr._missing_alias_cache

    @pytest.mark.asyncio
    async def test_resolve_read_collection_name_with_live_alias(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        alias_item = SimpleNamespace(alias_name="kb_test__live", collection_name="kb_test_v2")
        client.get_aliases = AsyncMock(
            return_value=SimpleNamespace(aliases=[alias_item])
        )

        result = await mgr.resolve_read_collection_name("test")
        assert result == "kb_test__live"

    @pytest.mark.asyncio
    async def test_switch_live_alias(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        client.get_aliases = AsyncMock(return_value=SimpleNamespace(aliases=[]))
        client.update_collection_aliases = AsyncMock()

        prev = await mgr.switch_live_alias(kb_id="test", target_collection_name="kb_test_v2")
        assert prev is None
        client.update_collection_aliases.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_switch_live_alias_same_target_noop(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        alias_item = SimpleNamespace(alias_name="kb_test__live", collection_name="kb_test_v2")
        client.get_aliases = AsyncMock(
            return_value=SimpleNamespace(aliases=[alias_item])
        )

        prev = await mgr.switch_live_alias(kb_id="test", target_collection_name="kb_test_v2")
        assert prev == "kb_test_v2"
        client.update_collection_aliases.assert_not_awaited()


# ---------------------------------------------------------------------------
# Clone collection
# ---------------------------------------------------------------------------

class TestCloneCollection:
    @pytest.mark.asyncio
    async def test_clone_collection(self, mgr: QdrantCollectionManager, provider):
        client = provider._client
        client.get_collections = AsyncMock(
            return_value=SimpleNamespace(collections=[])
        )
        client.create_collection = AsyncMock()

        pt = SimpleNamespace(
            id="pt1",
            vector={"bge_dense": [0.1] * 1024},
            payload={"content": "x"},
        )
        client.scroll = AsyncMock(return_value=([pt], None))
        client.upsert = AsyncMock()

        copied = await mgr.clone_collection(
            source_collection_name="src_col",
            destination_collection_name="dst_col",
        )
        assert copied == 1
        client.create_collection.assert_awaited_once()
        client.upsert.assert_awaited()

    @pytest.mark.asyncio
    async def test_clone_upsert_batch_retry_and_split(self, mgr: QdrantCollectionManager, provider):
        """Test that _clone_upsert_batch retries and splits on failure."""
        client = provider._client
        from qdrant_client.models import PointStruct

        batch = [
            PointStruct(id="1", vector={"d": [0.1]}, payload={}),
            PointStruct(id="2", vector={"d": [0.2]}, payload={}),
        ]

        call_count = 0

        async def fail_then_succeed(**kwargs):
            nonlocal call_count
            call_count += 1
            pts = kwargs.get("points", [])
            # Fail for batches > 1 (first 3 tries), succeed for single items
            if len(pts) > 1:
                raise Exception("batch too large")

        client.upsert = AsyncMock(side_effect=fail_then_succeed)

        await mgr._clone_upsert_batch(
            client=client,
            destination_collection_name="dst",
            batch=batch,
        )
        # Should have attempted 3 retries on batch of 2, then split into 2 singles
        assert call_count >= 4
