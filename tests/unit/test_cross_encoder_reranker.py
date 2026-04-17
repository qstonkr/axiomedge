"""Unit tests for src/search/cross_encoder_reranker.py.

Tests the cross-encoder reranking module without loading any models
or making real HTTP calls. All external dependencies are mocked.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from src.search.cross_encoder_reranker import (
    _sigmoid,
    _rerank_via_tei,
    rerank_with_cross_encoder,
    warmup,
)


# ---------------------------------------------------------------------------
# _sigmoid
# ---------------------------------------------------------------------------


class TestSigmoid:
    """Test sigmoid normalization function."""

    def test_zero_input(self) -> None:
        assert _sigmoid(0.0) == pytest.approx(0.5)

    def test_positive_input(self) -> None:
        result = _sigmoid(3.0, temperature=1.0)
        assert result > 0.9

    def test_negative_input(self) -> None:
        result = _sigmoid(-3.0, temperature=1.0)
        assert result < 0.1

    def test_large_positive_no_overflow(self) -> None:
        """Even with extreme input, sigmoid should not overflow."""
        result = _sigmoid(10000.0)
        assert result == pytest.approx(1.0, abs=1e-6)

    def test_large_negative_no_overflow(self) -> None:
        result = _sigmoid(-10000.0)
        assert result == pytest.approx(0.0, abs=1e-6)

    def test_output_range(self) -> None:
        """Sigmoid output should always be in (0, 1)."""
        for x in [-100, -10, -1, 0, 1, 10, 100]:
            result = _sigmoid(float(x))
            assert 0.0 <= result <= 1.0

    def test_temperature_effect(self) -> None:
        """Higher temperature should flatten the sigmoid curve."""
        steep = _sigmoid(3.0, temperature=1.0)
        flat = _sigmoid(3.0, temperature=10.0)
        # With higher temperature, output is closer to 0.5
        assert abs(flat - 0.5) < abs(steep - 0.5)

    def test_symmetry(self) -> None:
        """sigmoid(x) + sigmoid(-x) should equal 1.0."""
        for x in [0.5, 1.0, 3.0, 10.0]:
            assert _sigmoid(x) + _sigmoid(-x) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _rerank_via_tei (mocked HTTP)
# ---------------------------------------------------------------------------


class TestRerankViaTei:
    """Test TEI cloud reranker with mocked httpx calls."""

    def _make_chunks(self, n: int = 3) -> list[dict]:
        return [
            {"content": f"chunk content {i}", "score": 0.5}
            for i in range(n)
        ]

    @patch("src.search.cross_encoder_reranker._get_tei_client")
    def test_basic_rerank(self, mock_get_client: MagicMock) -> None:
        """TEI reranker should sort chunks by score descending."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Simulate TEI response: index 2 has highest score
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"index": 0, "score": -1.0},
            {"index": 1, "score": 0.0},
            {"index": 2, "score": 2.0},
        ]
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        chunks = self._make_chunks(3)
        result = _rerank_via_tei("test query", chunks, top_k=3, score_key="score")

        assert len(result) == 3
        # Chunk at original index 2 should be first (highest score)
        assert result[0]["metadata"]["cross_encoder_score"] > result[1]["metadata"]["cross_encoder_score"]

    @patch("src.search.cross_encoder_reranker._get_tei_client")
    def test_top_k_limit(self, mock_get_client: MagicMock) -> None:
        """Should return at most top_k results."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"index": i, "score": float(i)} for i in range(5)
        ]
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        chunks = self._make_chunks(5)
        result = _rerank_via_tei("query", chunks, top_k=2, score_key="score")

        assert len(result) == 2

    @patch("src.search.cross_encoder_reranker._get_tei_client")
    def test_metadata_created_if_missing(self, mock_get_client: MagicMock) -> None:
        """Chunks without metadata dict should get one created."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = [{"index": 0, "score": 1.0}]
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        chunks = [{"content": "test"}]  # no metadata key
        result = _rerank_via_tei("q", chunks, top_k=1, score_key="score")

        assert "metadata" in result[0]
        assert "cross_encoder_score" in result[0]["metadata"]

    @patch("src.search.cross_encoder_reranker._get_tei_client")
    def test_scores_are_sigmoid_normalized(self, mock_get_client: MagicMock) -> None:
        """Raw TEI scores should be passed through sigmoid."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        raw_score = 5.0
        mock_response = MagicMock()
        mock_response.json.return_value = [{"index": 0, "score": raw_score}]
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        chunks = [{"content": "test"}]
        _rerank_via_tei("q", chunks, top_k=1, score_key="score")

        expected = _sigmoid(raw_score)
        assert chunks[0]["score"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# rerank_with_cross_encoder (integration-level with mocks)
# ---------------------------------------------------------------------------


class TestRerankWithCrossEncoder:
    """Test the main rerank_with_cross_encoder function."""

    def test_empty_chunks_returns_empty(self) -> None:
        result = rerank_with_cross_encoder("query", [], top_k=5)
        assert result == []

    @patch("src.search.cross_encoder_reranker._use_cloud_reranker", False)
    @patch("src.search.cross_encoder_reranker._model", None)
    def test_no_model_returns_truncated(self) -> None:
        """When no model is loaded, return chunks[:top_k] unchanged."""
        chunks = [{"content": f"c{i}"} for i in range(5)]
        result = rerank_with_cross_encoder("query", chunks, top_k=3)
        assert len(result) == 3
        assert result == chunks[:3]

    @patch("src.search.cross_encoder_reranker._use_cloud_reranker", True)
    @patch("src.search.cross_encoder_reranker._rerank_via_tei")
    def test_cloud_reranker_used_when_enabled(self, mock_tei: MagicMock) -> None:
        """When cloud reranker is enabled, _rerank_via_tei should be called."""
        mock_tei.return_value = [{"content": "reranked"}]
        chunks = [{"content": "test"}]
        result = rerank_with_cross_encoder("query", chunks, top_k=1)
        mock_tei.assert_called_once()
        assert result == [{"content": "reranked"}]

    @patch("src.search.cross_encoder_reranker._use_cloud_reranker", True)
    @patch("src.search.cross_encoder_reranker._rerank_via_tei", side_effect=RuntimeError("network error"))
    @patch("src.search.cross_encoder_reranker._model", None)
    def test_cloud_failure_falls_back(self, mock_tei: MagicMock) -> None:
        """Cloud reranker failure should fall back to local (which is None -> passthrough)."""
        chunks = [{"content": f"c{i}"} for i in range(3)]
        result = rerank_with_cross_encoder("query", chunks, top_k=2)
        assert len(result) == 2

    @patch("src.search.cross_encoder_reranker._use_cloud_reranker", False)
    def test_local_model_predict(self) -> None:
        """Test with a mocked local cross-encoder model."""
        mock_model = MagicMock()
        # Simulate predict returning raw scores
        mock_model.predict.return_value = [2.0, -1.0, 0.5]

        with patch("src.search.cross_encoder_reranker._model", mock_model):
            chunks = [
                {"content": "chunk A"},
                {"content": "chunk B"},
                {"content": "chunk C"},
            ]
            result = rerank_with_cross_encoder("query", chunks, top_k=3)

            assert len(result) == 3
            mock_model.predict.assert_called_once()
            # Result should be sorted by score descending
            scores = [c["cross_encoder_score"] for c in result]
            assert scores == sorted(scores, reverse=True)

    @patch("src.search.cross_encoder_reranker._use_cloud_reranker", False)
    def test_local_model_predict_failure_graceful(self) -> None:
        """If model.predict raises, return chunks[:top_k] unchanged."""
        mock_model = MagicMock()
        mock_model.predict.side_effect = RuntimeError("CUDA OOM")

        with patch("src.search.cross_encoder_reranker._model", mock_model):
            chunks = [{"content": f"c{i}"} for i in range(4)]
            result = rerank_with_cross_encoder("query", chunks, top_k=2)
            assert len(result) == 2


# ---------------------------------------------------------------------------
# warmup
# ---------------------------------------------------------------------------


class TestWarmup:
    """Test background model loading trigger."""

    @patch("src.search.cross_encoder_reranker._load_attempted", False)
    @patch("src.search.cross_encoder_reranker._loading", False)
    @patch("src.search.cross_encoder_reranker._executor")
    def test_warmup_submits_task(self, mock_executor: MagicMock) -> None:
        warmup()
        mock_executor.submit.assert_called_once()

    @patch("src.search.cross_encoder_reranker._load_attempted", True)
    @patch("src.search.cross_encoder_reranker._executor")
    def test_warmup_skips_if_already_attempted(self, mock_executor: MagicMock) -> None:
        warmup()
        mock_executor.submit.assert_not_called()

    @patch("src.search.cross_encoder_reranker._load_attempted", False)
    @patch("src.search.cross_encoder_reranker._loading", True)
    @patch("src.search.cross_encoder_reranker._executor")
    def test_warmup_skips_if_loading(self, mock_executor: MagicMock) -> None:
        warmup()
        mock_executor.submit.assert_not_called()
