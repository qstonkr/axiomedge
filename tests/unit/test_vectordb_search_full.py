"""Unit tests for src/vectordb/search.py -- QdrantSearchEngine."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.vectordb.client import (
    QdrantClientProvider,
    QdrantConfig,
    QdrantSearchResult,
)
from src.vectordb.collections import QdrantCollectionManager
from src.vectordb.search import QdrantSearchEngine


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
        retrieval_projection_enabled=False,
        hybrid_prefetch_multiplier=3,
        hybrid_prefetch_max=100,
        colbert_rerank_candidate_multiplier=3,
    )


@pytest.fixture()
def provider(config: QdrantConfig) -> QdrantClientProvider:
    p = QdrantClientProvider(config=config)
    p._client = AsyncMock()
    return p


@pytest.fixture()
def collection_mgr(provider: QdrantClientProvider) -> QdrantCollectionManager:
    mgr = QdrantCollectionManager(provider)
    # Pre-populate resolve to avoid alias lookups
    return mgr


@pytest.fixture()
def engine(
    provider: QdrantClientProvider,
    collection_mgr: QdrantCollectionManager,
) -> QdrantSearchEngine:
    return QdrantSearchEngine(provider, collection_mgr)


def _make_point(pid: str, score: float, content: str = "test content", **extra_payload):
    payload = {"content": content, **extra_payload}
    return SimpleNamespace(id=pid, score=score, payload=payload)


# ---------------------------------------------------------------------------
# query_candidates -- hybrid (dense + sparse)
# ---------------------------------------------------------------------------

class TestQueryCandidates:
    async def test_hybrid_search_with_sparse(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        pt = _make_point("p1", 0.9)
        client.query_points = AsyncMock(return_value=SimpleNamespace(points=[pt]))

        results = await engine.query_candidates(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            sparse_vector={1: 0.5, 2: 0.3},
            top_k=10,
            score_threshold=None,
            filter_conditions=None,
            with_payload=None,
        )
        assert len(results) == 1
        assert results[0].point_id == "p1"
        assert results[0].score == 0.9

    async def test_dense_only_search(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        pt = _make_point("p2", 0.8)
        client.query_points = AsyncMock(return_value=SimpleNamespace(points=[pt]))

        results = await engine.query_candidates(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            sparse_vector=None,
            top_k=5,
            score_threshold=None,
            filter_conditions=None,
            with_payload=None,
        )
        assert len(results) == 1
        assert results[0].point_id == "p2"

    async def test_score_threshold_filtering(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        pts = [_make_point("high", 0.9), _make_point("low", 0.1)]
        client.query_points = AsyncMock(return_value=SimpleNamespace(points=pts))

        results = await engine.query_candidates(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            sparse_vector=None,
            top_k=10,
            score_threshold=0.5,
            filter_conditions=None,
            with_payload=None,
        )
        assert len(results) == 1
        assert results[0].point_id == "high"

    async def test_filter_conditions(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.query_points = AsyncMock(return_value=SimpleNamespace(points=[]))

        await engine.query_candidates(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            sparse_vector=None,
            top_k=5,
            score_threshold=None,
            filter_conditions={"kb_id": "test", "source_type": ["pdf", "docx"]},
            with_payload=None,
        )
        client.query_points.assert_awaited_once()

    async def test_filter_with_match_text(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.query_points = AsyncMock(return_value=SimpleNamespace(points=[]))

        await engine.query_candidates(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            sparse_vector=None,
            top_k=5,
            score_threshold=None,
            filter_conditions={"content": {"match_text": "keyword"}},
            with_payload=None,
        )
        client.query_points.assert_awaited_once()

    async def test_hybrid_fallback_to_legacy_names(self, engine: QdrantSearchEngine, provider, collection_mgr):
        """When named-vector query fails with non-500 error, retry with legacy names."""
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))

        call_count = 0

        async def query_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("named vector not found")
            return SimpleNamespace(points=[_make_point("fallback", 0.7)])

        client.query_points = AsyncMock(side_effect=query_side_effect)

        results = await engine.query_candidates(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            sparse_vector={1: 0.5},
            top_k=5,
            score_threshold=None,
            filter_conditions=None,
            with_payload=None,
        )
        assert len(results) == 1
        assert results[0].point_id == "fallback"
        assert call_count == 2

    async def test_dense_fallback_to_legacy(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))

        call_count = 0

        async def query_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("named vector not found")
            return SimpleNamespace(points=[_make_point("legacy", 0.6)])

        client.query_points = AsyncMock(side_effect=query_side_effect)

        results = await engine.query_candidates(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            sparse_vector=None,
            top_k=5,
            score_threshold=None,
            filter_conditions=None,
            with_payload=None,
        )
        assert len(results) == 1
        assert call_count == 2

    async def test_server_500_error_raises(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))

        err = ValueError("server error")
        err.status_code = 500
        client.query_points = AsyncMock(side_effect=err)

        with pytest.raises(ValueError, match="server error"):
            await engine.query_candidates(
                kb_id="test",
                dense_vector=[0.1] * 1024,
                sparse_vector={1: 0.5},
                top_k=5,
                score_threshold=None,
                filter_conditions=None,
                with_payload=None,
            )


# ---------------------------------------------------------------------------
# search (high-level)
# ---------------------------------------------------------------------------

class TestSearch:
    async def test_search_no_projection(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        pt = _make_point("s1", 0.85)
        client.query_points = AsyncMock(return_value=SimpleNamespace(points=[pt]))

        results = await engine.search(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            sparse_vector={1: 0.5},
            top_k=5,
        )
        assert len(results) == 1
        assert results[0].point_id == "s1"

    async def test_search_with_projection(self, engine: QdrantSearchEngine, provider, collection_mgr):
        provider.config.retrieval_projection_enabled = True
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        pt = _make_point("s2", 0.85)
        client.query_points = AsyncMock(return_value=SimpleNamespace(points=[pt]))
        record = SimpleNamespace(
            id="s2", payload={"content": "hydrated content", "extra": "data"}, score=None
        )
        client.retrieve = AsyncMock(return_value=[record])

        results = await engine.search(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            top_k=5,
        )
        assert len(results) == 1

    async def test_search_empty_results(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.query_points = AsyncMock(return_value=SimpleNamespace(points=[]))

        results = await engine.search(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            top_k=5,
        )
        assert results == []


# ---------------------------------------------------------------------------
# search_with_colbert_rerank
# ---------------------------------------------------------------------------

class TestColBERTRerank:
    async def test_colbert_rerank_no_colbert_vectors(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        pt = _make_point("c1", 0.9)
        client.query_points = AsyncMock(return_value=SimpleNamespace(points=[pt]))

        results = await engine.search_with_colbert_rerank(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            colbert_vectors=None,  # no colbert
            top_k=5,
        )
        assert len(results) == 1

    async def test_colbert_rerank_with_vectors(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        pt = _make_point("c2", 0.8, colbert_vectors=[[0.5] * 128])
        client.query_points = AsyncMock(return_value=SimpleNamespace(points=[pt]))

        results = await engine.search_with_colbert_rerank(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            colbert_vectors=[[0.5] * 128],
            top_k=5,
        )
        assert len(results) == 1

    async def test_colbert_rerank_with_projection(self, engine: QdrantSearchEngine, provider, collection_mgr):
        provider.config.retrieval_projection_enabled = True
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        pt = _make_point("c3", 0.8)
        client.query_points = AsyncMock(return_value=SimpleNamespace(points=[pt]))
        record = SimpleNamespace(
            id="c3", payload={"colbert_vectors": [[0.5] * 128], "content": "data"}, score=None
        )
        client.retrieve = AsyncMock(return_value=[record])

        results = await engine.search_with_colbert_rerank(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            colbert_vectors=[[0.5] * 128],
            top_k=5,
        )
        assert len(results) == 1

    async def test_colbert_score_threshold(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        pt = _make_point("low", 0.1, colbert_vectors=[[0.1] * 128])
        client.query_points = AsyncMock(return_value=SimpleNamespace(points=[pt]))

        results = await engine.search_with_colbert_rerank(
            kb_id="test",
            dense_vector=[0.1] * 1024,
            colbert_vectors=[[0.5] * 128],
            top_k=5,
            score_threshold=0.9,
        )
        assert len(results) == 0


# ---------------------------------------------------------------------------
# hydrate_by_ids
# ---------------------------------------------------------------------------

class TestHydrateByIds:
    async def test_hydrate_empty(self, engine: QdrantSearchEngine):
        result = await engine.hydrate_by_ids("test", [])
        assert result == {}

    async def test_hydrate_returns_map(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        record = SimpleNamespace(
            id="h1", payload={"content": "hydrated", "meta": "val"}, score=None
        )
        client.retrieve = AsyncMock(return_value=[record])

        result = await engine.hydrate_by_ids("test", ["h1"])
        assert "h1" in result
        assert result["h1"].content == "hydrated"

    async def test_hydrate_error_returns_empty(self, engine: QdrantSearchEngine, provider, collection_mgr):
        client = provider._client
        client.get_collection = AsyncMock(side_effect=Exception("no alias"))
        client.retrieve = AsyncMock(side_effect=Exception("fail"))

        result = await engine.hydrate_by_ids("test", ["h1"])
        assert result == {}


# ---------------------------------------------------------------------------
# MaxSim / cosine_sim
# ---------------------------------------------------------------------------

class TestMathUtilities:
    def test_compute_maxsim_identical_vectors(self):
        q = [[1.0, 0.0, 0.0]]
        d = [[1.0, 0.0, 0.0]]
        score = QdrantSearchEngine._compute_maxsim(q, d)
        assert score == pytest.approx(1.0, abs=1e-5)

    def test_compute_maxsim_orthogonal(self):
        q = [[1.0, 0.0]]
        d = [[0.0, 1.0]]
        score = QdrantSearchEngine._compute_maxsim(q, d)
        assert score == pytest.approx(0.0, abs=1e-5)

    def test_compute_maxsim_empty(self):
        assert QdrantSearchEngine._compute_maxsim([], [[1.0]]) == 0.0
        assert QdrantSearchEngine._compute_maxsim([[1.0]], []) == 0.0

    def test_compute_maxsim_multi_tokens(self):
        q = [[1.0, 0.0], [0.0, 1.0]]
        d = [[0.7, 0.7], [1.0, 0.0]]
        score = QdrantSearchEngine._compute_maxsim(q, d)
        # Each query token picks max similarity across doc tokens
        assert 0.0 < score < 1.0

    def test_cosine_sim_identical(self):
        v = [1.0, 0.0, 0.0]
        assert QdrantSearchEngine._cosine_sim(v, v) == pytest.approx(1.0, abs=1e-5)

    def test_cosine_sim_zero_vector(self):
        assert QdrantSearchEngine._cosine_sim([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_cosine_sim_orthogonal(self):
        assert QdrantSearchEngine._cosine_sim([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-5)
