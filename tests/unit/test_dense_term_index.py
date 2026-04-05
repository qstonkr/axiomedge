"""Unit tests for src/search/dense_term_index.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.search.dense_term_index import DenseTermIndex


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

DIM = 1024


def _mock_provider(ready: bool = True, vectors: list[list[float]] | None = None):
    """Create a mock OnnxBgeEmbeddingProvider."""
    provider = MagicMock()
    provider.is_ready.return_value = ready
    if vectors is None:
        # Return random normalized vectors
        def _encode(texts, **kwargs):
            vecs = []
            for _ in texts:
                v = np.random.randn(DIM).astype(np.float32)
                v = v / np.linalg.norm(v)
                vecs.append(v.tolist())
            return {"dense_vecs": vecs}
        provider.encode.side_effect = _encode
    else:
        provider.encode.return_value = {"dense_vecs": vectors}
    return provider


def _make_term(term: str, term_ko: str = "", definition: str = ""):
    """Create a mock precomputed term object."""
    t = SimpleNamespace(term=term, term_ko=term_ko, definition=definition)
    return SimpleNamespace(term=t)


# ---------------------------------------------------------------------------
# is_ready
# ---------------------------------------------------------------------------


class TestIsReady:
    def test_not_ready_initially(self):
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        assert idx.is_ready is False

    def test_ready_after_build(self):
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        terms = [_make_term("server"), _make_term("network")]
        idx.build(terms)
        assert idx.is_ready is True


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


class TestBuild:
    def test_build_stores_matrix(self):
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        terms = [_make_term("kubernetes"), _make_term("docker"), _make_term("helm")]
        idx.build(terms, batch_size=2)
        assert idx._matrix is not None
        assert idx._matrix.shape == (3, DIM)

    def test_build_normalizes_vectors(self):
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        terms = [_make_term("test")]
        idx.build(terms)
        norms = np.linalg.norm(idx._matrix, axis=1)
        np.testing.assert_allclose(norms, 1.0, atol=1e-5)

    def test_build_skips_when_provider_not_ready(self):
        provider = _mock_provider(ready=False)
        idx = DenseTermIndex(provider)
        idx.build([_make_term("test")])
        assert idx.is_ready is False

    def test_build_empty_terms(self):
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        idx.build([])
        assert idx.is_ready is False

    def test_build_handles_encode_failure(self):
        provider = _mock_provider()
        provider.encode.side_effect = RuntimeError("ONNX error")
        idx = DenseTermIndex(provider)
        terms = [_make_term("test")]
        idx.build(terms, batch_size=1)
        # Should still build with zero-padded vectors
        assert idx._matrix is not None

    def test_build_with_term_ko_and_definition(self):
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        terms = [_make_term("server", "서버", "물리 서버 장비")]
        idx.build(terms)
        # Verify encode was called with concatenated text
        call_args = provider.encode.call_args
        text = call_args[0][0][0]
        assert "server" in text
        assert "서버" in text
        assert "물리 서버 장비" in text


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    def _build_index(self) -> DenseTermIndex:
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        terms = [_make_term(f"term_{i}") for i in range(10)]
        idx.build(terms)
        return idx

    def test_search_returns_results(self):
        idx = self._build_index()
        results = idx.search("query text", top_k=3)
        assert len(results) == 3

    def test_search_returns_tuples(self):
        idx = self._build_index()
        results = idx.search("query", top_k=1)
        assert len(results) == 1
        index, score = results[0]
        assert isinstance(index, int)
        assert isinstance(score, float)

    def test_search_sorted_descending(self):
        idx = self._build_index()
        results = idx.search("query", top_k=5)
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_empty_index(self):
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        results = idx.search("query")
        assert results == []

    def test_search_top_k_exceeds_size(self):
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        terms = [_make_term("a"), _make_term("b")]
        idx.build(terms)
        results = idx.search("query", top_k=100)
        assert len(results) == 2

    def test_search_encode_failure_returns_empty(self):
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        terms = [_make_term("test")]
        idx.build(terms)
        # Now make encode fail on the query
        provider.encode.side_effect = RuntimeError("fail")
        results = idx.search("query")
        assert results == []

    def test_search_empty_vec_returns_empty(self):
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        terms = [_make_term("test")]
        idx.build(terms)
        # Override side_effect so return_value takes effect
        provider.encode.side_effect = None
        provider.encode.return_value = {"dense_vecs": []}
        results = idx.search("query")
        assert results == []


# ---------------------------------------------------------------------------
# search_batch
# ---------------------------------------------------------------------------


class TestSearchBatch:
    def _build_index(self, n: int = 10) -> DenseTermIndex:
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        terms = [_make_term(f"term_{i}") for i in range(n)]
        idx.build(terms)
        return idx

    def test_batch_returns_list_of_lists(self):
        idx = self._build_index()
        results = idx.search_batch(["q1", "q2", "q3"], top_k=3)
        assert len(results) == 3
        for r in results:
            assert len(r) == 3

    def test_batch_empty_queries(self):
        idx = self._build_index()
        results = idx.search_batch([], top_k=3)
        assert results == []

    def test_batch_on_empty_index(self):
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        results = idx.search_batch(["q1", "q2"])
        assert all(r == [] for r in results)

    def test_batch_results_sorted(self):
        idx = self._build_index()
        results = idx.search_batch(["query"], top_k=5)
        for r in results:
            scores = [s for _, s in r]
            assert scores == sorted(scores, reverse=True)

    def test_batch_top_k_capped(self):
        idx = self._build_index(n=3)
        results = idx.search_batch(["q1"], top_k=100)
        assert len(results[0]) == 3

    def test_batch_encode_failure_pads_zeros(self):
        provider = _mock_provider()
        idx = DenseTermIndex(provider)
        terms = [_make_term("t")]
        idx.build(terms)
        # After build, make batch encode fail
        call_count = [0]
        original_side_effect = provider.encode.side_effect

        def _fail_on_second(texts, **kwargs):
            call_count[0] += 1
            raise RuntimeError("fail")

        provider.encode.side_effect = _fail_on_second
        # Should not crash; returns results (with zero-padded query vecs)
        results = idx.search_batch(["q1"], top_k=1)
        assert len(results) == 1
