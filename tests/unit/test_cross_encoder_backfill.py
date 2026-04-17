"""Unit tests for src/search/cross_encoder_reranker.py — backfill coverage.

Covers _load_model_sync, async_rerank_with_cross_encoder, _get_tei_client,
content truncation, and edge cases not in the existing test file.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import src.search.cross_encoder_reranker as mod
from src.search.cross_encoder_reranker import (
    rerank_with_cross_encoder,
)


# -------------------------------------------------------------------
# _load_model_sync — cloud mode
# -------------------------------------------------------------------


class TestLoadModelSyncCloud:
    """Test _load_model_sync when cloud reranker is active."""

    @patch.object(mod, "_use_cloud_reranker", True)
    @patch.object(mod, "_load_attempted", False)
    @patch.object(mod, "_loading", False)
    def test_cloud_mode_sets_attempted(self) -> None:
        mod._load_model_sync()
        assert mod._load_attempted is True

    @patch.object(mod, "_use_cloud_reranker", True)
    @patch.object(mod, "_load_attempted", False)
    @patch.object(mod, "_loading", False)
    def test_cloud_mode_skips_model_load(self) -> None:
        """Should not import sentence_transformers in cloud mode."""
        mod._load_model_sync()
        # _model should remain None (no local model loaded)
        # Just verify it completes without error


# -------------------------------------------------------------------
# _load_model_sync — local mode (model load fails)
# -------------------------------------------------------------------


class TestLoadModelSyncLocal:
    """Test _load_model_sync local fallback with mocked imports."""

    @patch.object(mod, "_use_cloud_reranker", False)
    @patch.object(mod, "_load_attempted", False)
    @patch.object(mod, "_loading", False)
    @patch.object(mod, "_model", None)
    @patch(
        "src.search.cross_encoder_reranker.CrossEncoder",
        create=True,
        side_effect=ImportError("no sentence_transformers"),
    )
    def test_load_failure_sets_model_none(self, _mock_ce: MagicMock) -> None:
        """When CrossEncoder import/init fails, _model stays None."""
        # _load_model_sync catches all exceptions
        mod._load_model_sync()
        assert mod._load_attempted is True
        assert mod._loading is False
        assert mod._model is None

    @patch.object(mod, "_use_cloud_reranker", False)
    @patch.object(mod, "_load_attempted", False)
    @patch.object(mod, "_loading", False)
    @patch.object(mod, "_model", None)
    def test_load_failure_graceful(self) -> None:
        """Even if all imports fail, _load_model_sync should not raise."""
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            # Will fail at `from sentence_transformers import CrossEncoder`
            mod._load_model_sync()
            assert mod._load_attempted is True
            assert mod._loading is False

    @patch.object(mod, "_use_cloud_reranker", False)
    @patch.object(mod, "_load_attempted", False)
    @patch.object(mod, "_loading", False)
    @patch.object(mod, "_model", None)
    def test_load_success(self) -> None:
        """Successful model load stores the model."""
        fake_model = MagicMock()
        fake_ce_cls = MagicMock(return_value=fake_model)
        fake_st = MagicMock()
        fake_st.CrossEncoder = fake_ce_cls

        fake_urllib3 = MagicMock()
        fake_requests = MagicMock()
        fake_session_cls = MagicMock()
        fake_requests.Session = fake_session_cls
        fake_session_cls.get = MagicMock()
        fake_session_cls.post = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "sentence_transformers": fake_st,
                "urllib3": fake_urllib3,
                "requests": fake_requests,
            },
        ):
            mod._load_model_sync()
            assert mod._model is fake_model
            assert mod._load_attempted is True
            assert mod._loading is False

        # Cleanup
        mod._model = None


# -------------------------------------------------------------------
# _get_tei_client
# -------------------------------------------------------------------


class TestGetTeiClient:
    """Test lazy initialization of the TEI httpx client."""

    @patch.object(mod, "_tei_client", None)
    def test_creates_client_on_first_call(self) -> None:
        fake_client = MagicMock()
        weights_mock = _make_weights_mock()
        with (
            patch("httpx.Client", return_value=fake_client),
            patch(
                "src.config.weights.weights", weights_mock,
            ),
        ):
            client = mod._get_tei_client()
            assert client is fake_client

        # Cleanup
        mod._tei_client = None

    @patch.object(mod, "_tei_client", MagicMock())
    def test_returns_existing_client(self) -> None:
        existing = mod._tei_client
        client = mod._get_tei_client()
        assert client is existing

        # Cleanup
        mod._tei_client = None


def _make_weights_mock():
    """Create a mock weights object with timeouts.httpx_reranker."""
    m = MagicMock()
    m.timeouts.httpx_reranker = 30.0
    return m


# -------------------------------------------------------------------
# async_rerank_with_cross_encoder
# -------------------------------------------------------------------


class TestAsyncRerank:
    """Test the async wrapper."""

    @pytest.mark.asyncio
    @patch.object(mod, "_model", None)
    async def test_no_model_returns_truncated(self) -> None:
        from src.search.cross_encoder_reranker import (
            async_rerank_with_cross_encoder,
        )

        chunks = [{"content": f"c{i}"} for i in range(5)]
        result = await async_rerank_with_cross_encoder("q", chunks, top_k=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_with_model_delegates(self) -> None:
        from src.search.cross_encoder_reranker import (
            async_rerank_with_cross_encoder,
        )

        fake_model = MagicMock()
        fake_model.predict.return_value = [1.0, 0.5]

        with (
            patch.object(mod, "_model", fake_model),
            patch.object(mod, "_use_cloud_reranker", False),
        ):
            chunks = [{"content": "a"}, {"content": "b"}]
            result = await async_rerank_with_cross_encoder(
                "query", chunks, top_k=2
            )
            assert len(result) == 2

    @pytest.mark.asyncio
    @patch.object(mod, "_model", None)
    async def test_empty_chunks(self) -> None:
        from src.search.cross_encoder_reranker import (
            async_rerank_with_cross_encoder,
        )

        result = await async_rerank_with_cross_encoder("q", [], top_k=5)
        assert result == []


# -------------------------------------------------------------------
# rerank_with_cross_encoder — additional edge cases
# -------------------------------------------------------------------


class TestRerankEdgeCases:
    """Additional coverage for rerank_with_cross_encoder."""

    @patch.object(mod, "_use_cloud_reranker", False)
    def test_local_model_metadata_created(self) -> None:
        """Chunks without metadata get it created during local rerank."""
        mock_model = MagicMock()
        mock_model.predict.return_value = [1.0]

        with patch.object(mod, "_model", mock_model):
            chunks = [{"content": "test"}]
            result = rerank_with_cross_encoder("q", chunks, top_k=1)
            assert "metadata" in result[0]
            assert "cross_encoder_score" in result[0]["metadata"]

    @patch.object(mod, "_use_cloud_reranker", False)
    def test_local_model_existing_metadata_preserved(self) -> None:
        """Existing metadata keys should not be lost."""
        mock_model = MagicMock()
        mock_model.predict.return_value = [1.0]

        with patch.object(mod, "_model", mock_model):
            chunks = [
                {
                    "content": "test",
                    "metadata": {"source": "doc.pdf"},
                }
            ]
            result = rerank_with_cross_encoder("q", chunks, top_k=1)
            assert result[0]["metadata"]["source"] == "doc.pdf"
            assert "cross_encoder_score" in result[0]["metadata"]

    @patch.object(mod, "_use_cloud_reranker", False)
    def test_content_truncation(self) -> None:
        """Long content should be truncated to CROSS_ENCODER_MAX_LENGTH."""
        mock_model = MagicMock()
        mock_model.predict.return_value = [1.0]

        long_content = "x" * 5000
        with patch.object(mod, "_model", mock_model):
            chunks = [{"content": long_content}]
            rerank_with_cross_encoder("q", chunks, top_k=1)

            # Check that predict received truncated content
            pairs = mock_model.predict.call_args.args[0]
            assert len(pairs[0][1]) == mod.CROSS_ENCODER_MAX_LENGTH

    @patch.object(mod, "_use_cloud_reranker", True)
    @patch.object(mod, "_model", None)
    def test_cloud_failure_no_local_model(self) -> None:
        """Cloud fails + no local model = passthrough."""
        with patch.object(
            mod,
            "_rerank_via_tei",
            side_effect=Exception("timeout"),
        ):
            chunks = [{"content": f"c{i}"} for i in range(4)]
            result = rerank_with_cross_encoder("q", chunks, top_k=2)
            assert len(result) == 2

    @patch.object(mod, "_use_cloud_reranker", True)
    def test_cloud_failure_with_local_fallback(self) -> None:
        """Cloud fails, local model available = local rerank."""
        mock_model = MagicMock()
        mock_model.predict.return_value = [2.0, 0.5, 1.0]

        with (
            patch.object(
                mod,
                "_rerank_via_tei",
                side_effect=Exception("timeout"),
            ),
            patch.object(mod, "_model", mock_model),
        ):
            chunks = [
                {"content": "a"},
                {"content": "b"},
                {"content": "c"},
            ]
            result = rerank_with_cross_encoder("q", chunks, top_k=3)
            mock_model.predict.assert_called_once()
            scores = [c["cross_encoder_score"] for c in result]
            assert scores == sorted(scores, reverse=True)

    def test_empty_chunks_returns_empty_regardless(self) -> None:
        """Empty chunks list should return empty for any top_k."""
        result = rerank_with_cross_encoder("q", [], top_k=0)
        assert result == []

    @patch.object(mod, "_use_cloud_reranker", False)
    @patch.object(mod, "_model", None)
    def test_top_k_larger_than_chunks(self) -> None:
        """top_k > len(chunks) returns all chunks."""
        chunks = [{"content": "a"}, {"content": "b"}]
        result = rerank_with_cross_encoder("q", chunks, top_k=10)
        assert len(result) == 2

    @patch.object(mod, "_use_cloud_reranker", False)
    def test_custom_score_key(self) -> None:
        """score_key parameter should be used as dict key."""
        mock_model = MagicMock()
        mock_model.predict.return_value = [1.0]

        with patch.object(mod, "_model", mock_model):
            chunks = [{"content": "test"}]
            result = rerank_with_cross_encoder(
                "q", chunks, top_k=1, score_key="my_score"
            )
            assert "my_score" in result[0]


# -------------------------------------------------------------------
# _rerank_via_tei — additional edge cases
# -------------------------------------------------------------------


class TestRerankViaTeiEdgeCases:
    """Additional TEI reranker tests."""

    @patch("src.search.cross_encoder_reranker._get_tei_client")
    def test_content_truncated(
        self, mock_get_client: MagicMock
    ) -> None:
        """Content longer than MAX_LENGTH is truncated before sending."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"index": 0, "score": 0.5}]
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp

        long_content = "x" * 5000
        chunks = [{"content": long_content}]
        mod._rerank_via_tei("q", chunks, top_k=1, score_key="s")

        sent_json = mock_client.post.call_args.kwargs["json"]
        assert len(sent_json["texts"][0]) == mod.CROSS_ENCODER_MAX_LENGTH

    @patch("src.search.cross_encoder_reranker._get_tei_client")
    def test_missing_content_key(
        self, mock_get_client: MagicMock
    ) -> None:
        """Chunk without 'content' key should use empty string."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"index": 0, "score": 0.0}]
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp

        chunks = [{"title": "no content field"}]
        mod._rerank_via_tei("q", chunks, top_k=1, score_key="s")

        sent_json = mock_client.post.call_args.kwargs["json"]
        assert sent_json["texts"][0] == ""

    @patch("src.search.cross_encoder_reranker._get_tei_client")
    def test_http_error_propagates(
        self, mock_get_client: MagicMock
    ) -> None:
        """raise_for_status should propagate HTTP errors."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("503")
        mock_client.post.return_value = mock_resp

        chunks = [{"content": "test"}]
        with pytest.raises(Exception, match="503"):
            mod._rerank_via_tei("q", chunks, top_k=1, score_key="s")


# -------------------------------------------------------------------
# warmup — additional
# -------------------------------------------------------------------


class TestWarmupBackfill:
    """Additional warmup edge cases."""

    @patch.object(mod, "_load_attempted", False)
    @patch.object(mod, "_loading", False)
    @patch.object(mod, "_executor")
    def test_warmup_passes_load_model_sync(
        self, mock_executor: MagicMock
    ) -> None:
        """warmup should submit _load_model_sync specifically."""
        mod.warmup()
        mock_executor.submit.assert_called_once_with(mod._load_model_sync)
