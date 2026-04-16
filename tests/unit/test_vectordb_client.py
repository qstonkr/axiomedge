"""Comprehensive tests for src/vectordb/client.py."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.stores.qdrant.client import (
    DEFAULT_DENSE_VECTOR_NAME,
    DEFAULT_SPARSE_VECTOR_NAME,
    DEFAULT_DENSE_WEIGHT,
    DEFAULT_SPARSE_WEIGHT,
    DEFAULT_COLBERT_WEIGHT,
    RRF_K,
    COLLECTION_CACHE_TTL_S,
    ADMIN_STATS_CACHE_TTL_S,
    RETRIEVAL_PAYLOAD_FIELDS,
    DEFAULT_HYDRATION_EXCLUDE_FIELDS,
    QdrantConfig,
    QdrantClientProvider,
    QdrantSearchResult,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_dense_vector_name(self):
        assert DEFAULT_DENSE_VECTOR_NAME == "bge_dense"

    def test_sparse_vector_name(self):
        assert DEFAULT_SPARSE_VECTOR_NAME == "bge_sparse"

    def test_rrf_k(self):
        assert RRF_K == 60

    def test_weights_are_floats(self):
        assert isinstance(DEFAULT_DENSE_WEIGHT, float)
        assert isinstance(DEFAULT_SPARSE_WEIGHT, float)
        assert isinstance(DEFAULT_COLBERT_WEIGHT, float)

    def test_retrieval_fields(self):
        assert "kb_id" in RETRIEVAL_PAYLOAD_FIELDS
        assert "document_id" in RETRIEVAL_PAYLOAD_FIELDS

    def test_hydration_exclude(self):
        assert "colbert_vectors" in DEFAULT_HYDRATION_EXCLUDE_FIELDS


# ---------------------------------------------------------------------------
# QdrantSearchResult
# ---------------------------------------------------------------------------


class TestQdrantSearchResult:
    def test_defaults(self):
        r = QdrantSearchResult(point_id="p1", score=0.9, content="text")
        assert r.point_id == "p1"
        assert r.score == 0.9
        assert r.content == "text"
        assert r.metadata == {}

    def test_with_metadata(self):
        r = QdrantSearchResult(
            point_id="p1", score=0.5, content="c", metadata={"kb_id": "kb1"}
        )
        assert r.metadata["kb_id"] == "kb1"


# ---------------------------------------------------------------------------
# QdrantConfig
# ---------------------------------------------------------------------------


class TestQdrantConfig:
    def test_defaults(self):
        cfg = QdrantConfig()
        assert cfg.url == ""
        assert cfg.api_key is None
        assert cfg.grpc_port == 6334
        assert cfg.prefer_grpc is True
        assert cfg.dense_vector_name == "bge_dense"
        assert cfg.sparse_vector_name == "bge_sparse"
        assert cfg.collection_prefix == "kb"
        assert cfg.timeout == 120
        assert cfg.clone_batch_size == 64
        assert cfg.collection_name_overrides == {}
        assert cfg.embedding_version_tracking_enabled is True
        assert cfg.retrieval_projection_enabled is True

    def test_from_env_defaults(self, monkeypatch):
        # Clear relevant env vars
        for key in [
            "QDRANT_URL", "QDRANT_API_KEY", "QDRANT_GRPC_PORT",
            "QDRANT_PREFER_GRPC", "QDRANT_COLLECTION_MAPPING",
            "QDRANT_DENSE_VECTOR_NAME", "QDRANT_SPARSE_VECTOR_NAME",
            "QDRANT_COLLECTION_PREFIX", "QDRANT_TIMEOUT",
        ]:
            monkeypatch.delenv(key, raising=False)

        cfg = QdrantConfig.from_env()
        assert cfg.url == "http://localhost:6333"
        assert cfg.api_key is None
        assert cfg.grpc_port == 6334
        assert cfg.prefer_grpc is True
        assert cfg.collection_prefix == "kb"

    def test_from_env_custom(self, monkeypatch):
        monkeypatch.setenv("QDRANT_URL", "http://custom:9999")
        monkeypatch.setenv("QDRANT_API_KEY", "secret123")
        monkeypatch.setenv("QDRANT_GRPC_PORT", "7777")
        monkeypatch.setenv("QDRANT_PREFER_GRPC", "false")
        monkeypatch.setenv("QDRANT_COLLECTION_PREFIX", "myprefix")
        monkeypatch.setenv("QDRANT_TIMEOUT", "60")
        monkeypatch.setenv("QDRANT_CLONE_BATCH_SIZE", "32")
        monkeypatch.setenv("KNOWLEDGE_EMBEDDING_VERSION_TRACKING", "false")
        monkeypatch.setenv("RETRIEVAL_PROJECTION_ENABLED", "false")

        cfg = QdrantConfig.from_env()
        assert cfg.url == "http://custom:9999"
        assert cfg.api_key == "secret123"
        assert cfg.grpc_port == 7777
        assert cfg.prefer_grpc is False
        assert cfg.collection_prefix == "myprefix"
        assert cfg.timeout == 60
        assert cfg.clone_batch_size == 32
        assert cfg.embedding_version_tracking_enabled is False
        assert cfg.retrieval_projection_enabled is False

    def test_from_env_collection_mapping(self, monkeypatch):
        monkeypatch.setenv("QDRANT_COLLECTION_MAPPING", "kb1:col_a, kb2:col_b")
        cfg = QdrantConfig.from_env()
        assert cfg.collection_name_overrides == {"kb1": "col_a", "kb2": "col_b"}

    def test_from_env_empty_mapping(self, monkeypatch):
        monkeypatch.setenv("QDRANT_COLLECTION_MAPPING", "")
        cfg = QdrantConfig.from_env()
        assert cfg.collection_name_overrides == {}

    def test_from_env_dense_vector_name(self, monkeypatch):
        monkeypatch.setenv("QDRANT_DENSE_VECTOR_NAME", "custom_dense")
        cfg = QdrantConfig.from_env()
        assert cfg.dense_vector_name == "custom_dense"

    def test_from_env_sparse_vector_name(self, monkeypatch):
        monkeypatch.setenv("QDRANT_SPARSE_VECTOR_NAME", "custom_sparse")
        cfg = QdrantConfig.from_env()
        assert cfg.sparse_vector_name == "custom_sparse"


# ---------------------------------------------------------------------------
# QdrantClientProvider
# ---------------------------------------------------------------------------


class TestQdrantClientProvider:
    def test_default_init(self):
        provider = QdrantClientProvider(config=QdrantConfig())
        assert provider.config is not None
        assert provider._client is None

    def test_init_from_env(self, monkeypatch):
        monkeypatch.setenv("QDRANT_URL", "http://test:1234")
        provider = QdrantClientProvider()
        assert provider.config.url == "http://test:1234"

    def test_build_metric_tags(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "test")
        provider = QdrantClientProvider(config=QdrantConfig())
        tags = provider.build_metric_tags(kb_id="kb1", collection_name="col", mode="hybrid")
        assert "env:test" in tags
        assert "kb_id:kb1" in tags
        assert "collection:col" in tags
        assert "mode:hybrid" in tags

    def test_build_metric_tags_minimal(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        provider = QdrantClientProvider(config=QdrantConfig())
        tags = provider.build_metric_tags()
        assert len(tags) == 1  # just env:local

    def test_emit_noop_methods(self):
        provider = QdrantClientProvider(config=QdrantConfig())
        # These are all no-ops, should not raise
        provider.emit_search_metric("test", 1.0)
        provider.emit_histogram("test", 1.0, tags=[])
        provider.emit_gauge("test", 1.0, tags=[])
        provider.emit_increment("test", tags=[])

    async def test_close_no_client(self):
        provider = QdrantClientProvider(config=QdrantConfig())
        await provider.close()  # should not raise

    async def test_close_with_client(self):
        provider = QdrantClientProvider(config=QdrantConfig())
        mock_client = AsyncMock()
        provider._client = mock_client
        await provider.close()
        mock_client.close.assert_called_once()
        assert provider._client is None


# ---------------------------------------------------------------------------
# Payload utilities
# ---------------------------------------------------------------------------


class TestPayloadUtilities:
    def test_build_hydration_no_exclude(self):
        result = QdrantClientProvider.build_hydration_payload_selector(None)
        assert result is True

    def test_build_hydration_empty_list(self):
        result = QdrantClientProvider.build_hydration_payload_selector([])
        assert result is True

    def test_build_hydration_with_fields(self):
        # If qdrant_client is available, returns PayloadSelectorExclude
        # Otherwise returns True
        result = QdrantClientProvider.build_hydration_payload_selector(["colbert_vectors"])
        assert result is not None  # either PayloadSelectorExclude or True

    def test_build_retrieval_no_include(self):
        result = QdrantClientProvider.build_retrieval_payload_selector(None)
        assert result is False

    def test_build_retrieval_empty(self):
        result = QdrantClientProvider.build_retrieval_payload_selector([])
        assert result is False

    def test_build_retrieval_with_fields(self):
        result = QdrantClientProvider.build_retrieval_payload_selector(["kb_id"])
        assert result is not None

    def test_estimate_result_payload_bytes(self):
        r = QdrantSearchResult(
            point_id="p1", score=0.9, content="hello world",
            metadata={"key": "value"},
        )
        bytes_est = QdrantClientProvider.estimate_result_payload_bytes(r)
        assert bytes_est > 0

    def test_estimate_result_empty_content(self):
        r = QdrantSearchResult(point_id="p1", score=0.9, content="", metadata={})
        bytes_est = QdrantClientProvider.estimate_result_payload_bytes(r)
        assert bytes_est >= 0

    def test_merge_search_result_payloads_no_hydrated(self):
        base = QdrantSearchResult(point_id="p1", score=0.9, content="c", metadata={"a": 1})
        result = QdrantClientProvider.merge_search_result_payloads(base, None)
        assert result is base

    def test_merge_search_result_payloads(self):
        base = QdrantSearchResult(
            point_id="p1", score=0.9, content="base",
            metadata={"a": 1, "b": 2},
        )
        hydrated = QdrantSearchResult(
            point_id="p1", score=0.9, content="hydrated",
            metadata={"a": 10, "c": 3},
        )
        result = QdrantClientProvider.merge_search_result_payloads(base, hydrated)
        assert result.content == "hydrated"
        assert result.score == 0.9
        # hydrated metadata takes precedence, base fills gaps
        assert result.metadata["a"] == 10
        assert result.metadata["b"] == 2
        assert result.metadata["c"] == 3


# ---------------------------------------------------------------------------
# augment_payload_with_embedding_tracking
# ---------------------------------------------------------------------------


class TestAugmentPayload:
    def test_basic(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDING_MODEL", "bge-m3")
        monkeypatch.setenv("KNOWLEDGE_EMBEDDING_VERSION", "v1")

        payload: dict = {}
        result = QdrantClientProvider.augment_payload_with_embedding_tracking(
            payload, dense_dimension=1024
        )
        assert result["embedding_model"] == "bge-m3"
        assert result["embedding_version"] == "v1"
        assert result["embedding_dimension"] == 1024
        assert "indexed_at" in result

    def test_existing_values_not_overwritten(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDING_MODEL", "bge-m3")
        payload = {"embedding_model": "custom", "embedding_version": "custom_v"}
        result = QdrantClientProvider.augment_payload_with_embedding_tracking(payload)
        assert result["embedding_model"] == "custom"
        assert result["embedding_version"] == "custom_v"

    def test_disabled_via_config(self):
        cfg = QdrantConfig(embedding_version_tracking_enabled=False)
        payload: dict = {}
        result = QdrantClientProvider.augment_payload_with_embedding_tracking(
            payload, config=cfg
        )
        assert "embedding_model" not in result

    def test_fallback_to_ollama_model(self, monkeypatch):
        monkeypatch.delenv("KNOWLEDGE_EMBEDDING_MODEL", raising=False)
        monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "ollama-m3")

        payload: dict = {}
        result = QdrantClientProvider.augment_payload_with_embedding_tracking(payload)
        assert result["embedding_model"] == "ollama-m3"

    def test_fallback_to_unknown(self, monkeypatch):
        monkeypatch.delenv("KNOWLEDGE_EMBEDDING_MODEL", raising=False)
        monkeypatch.delenv("OLLAMA_EMBEDDING_MODEL", raising=False)

        payload: dict = {}
        result = QdrantClientProvider.augment_payload_with_embedding_tracking(payload)
        assert result["embedding_model"] == "unknown"

    def test_no_dimension_when_not_provided(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDING_MODEL", "m")
        payload: dict = {}
        result = QdrantClientProvider.augment_payload_with_embedding_tracking(payload)
        assert "embedding_dimension" not in result

    def test_indexed_at_default(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDING_MODEL", "m")
        payload: dict = {"indexed_at": "existing"}
        result = QdrantClientProvider.augment_payload_with_embedding_tracking(payload)
        assert result["indexed_at"] == "existing"
