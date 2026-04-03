"""Unit tests for embedding providers — coverage push.

Targets: ollama_provider (41), tei_provider (33), dual_provider (56).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

# ---------------------------------------------------------------------------
# OllamaEmbeddingProvider
# ---------------------------------------------------------------------------

from src.embedding.ollama_provider import OllamaEmbeddingProvider


class TestOllamaProvider:
    def test_encode_empty(self):
        p = OllamaEmbeddingProvider(base_url="http://localhost:11434")
        result = p.encode([])
        assert result == {"dense_vecs": [], "lexical_weights": [], "colbert_vecs": []}

    def test_encode_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"embeddings": [[0.1] * 1024]}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp

        p = OllamaEmbeddingProvider(base_url="http://localhost:11434")
        p._client = mock_client

        result = p.encode(["hello"], return_sparse=True)
        assert len(result["dense_vecs"]) == 1
        assert len(result["dense_vecs"][0]) == 1024
        assert len(result["lexical_weights"]) == 1

    def test_encode_no_embeddings_returned(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"embeddings": []}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp

        p = OllamaEmbeddingProvider()
        p._client = mock_client

        result = p.encode(["hello"])
        assert result["dense_vecs"] == []

    def test_encode_batching(self):
        """Texts > BATCH_SIZE should be split into batches."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"embeddings": [[0.1] * 1024]}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp

        p = OllamaEmbeddingProvider()
        p._client = mock_client

        texts = [f"text{i}" for i in range(12)]
        result = p.encode(texts, return_sparse=False)
        # 12 texts / BATCH_SIZE=5 = 3 calls
        assert mock_client.post.call_count == 3

    def test_encode_without_sparse(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"embeddings": [[0.1] * 1024]}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp

        p = OllamaEmbeddingProvider()
        p._client = mock_client

        result = p.encode(["hello"], return_sparse=False)
        assert result["lexical_weights"] == []

    def test_is_ready_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "bge-m3:latest"}]}

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        p = OllamaEmbeddingProvider(model="bge-m3")
        p._client = mock_client

        assert p.is_ready() is True

    def test_is_ready_no_model(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "llama3"}]}

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        p = OllamaEmbeddingProvider(model="bge-m3")
        p._client = mock_client

        assert p.is_ready() is False

    def test_is_ready_connection_error(self):
        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("connection refused")

        p = OllamaEmbeddingProvider()
        p._client = mock_client

        assert p.is_ready() is False

    async def test_embed_async(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"embeddings": [[0.5] * 1024]}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp

        p = OllamaEmbeddingProvider()
        p._client = mock_client

        result = await p.embed("test")
        assert len(result) == 1024

    async def test_embed_documents_async(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"embeddings": [[0.5] * 1024, [0.3] * 1024]}
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp

        p = OllamaEmbeddingProvider()
        p._client = mock_client

        result = await p.embed_documents(["a", "b"])
        assert len(result) == 2

    def test_dimension(self):
        p = OllamaEmbeddingProvider()
        assert p.dimension > 0

    def test_close(self):
        mock_client = MagicMock()
        p = OllamaEmbeddingProvider()
        p._client = mock_client
        p.close()
        mock_client.close.assert_called_once()
        assert p._client is None

    def test_close_no_client(self):
        p = OllamaEmbeddingProvider()
        p.close()  # should not crash


# ---------------------------------------------------------------------------
# TEIEmbeddingProvider
# ---------------------------------------------------------------------------

from src.embedding.tei_provider import TEIEmbeddingProvider


class TestTEIProvider:
    def test_encode_empty(self):
        p = TEIEmbeddingProvider()
        result = p.encode([])
        assert result["dense_vecs"] == []

    def test_encode_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [[0.1] * 1024]
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp

        p = TEIEmbeddingProvider()
        p._client = mock_client

        result = p.encode(["hello"], return_sparse=True)
        assert len(result["dense_vecs"]) == 1
        assert len(result["lexical_weights"]) == 1

    def test_encode_no_dense(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [[0.1] * 1024]
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp

        p = TEIEmbeddingProvider()
        p._client = mock_client

        result = p.encode(["hello"], return_dense=False)
        assert result["dense_vecs"] == []

    def test_is_ready_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        p = TEIEmbeddingProvider()
        p._client = mock_client

        assert p.is_ready() is True

    def test_is_ready_failure(self):
        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("down")

        p = TEIEmbeddingProvider()
        p._client = mock_client

        assert p.is_ready() is False

    async def test_embed_async(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [[0.5] * 1024]
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp

        p = TEIEmbeddingProvider()
        p._client = mock_client

        result = await p.embed("test")
        assert len(result) == 1024

    async def test_embed_documents_async(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [[0.5] * 1024, [0.3] * 1024]
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp

        p = TEIEmbeddingProvider()
        p._client = mock_client

        result = await p.embed_documents(["a", "b"])
        assert len(result) == 2

    def test_dimension(self):
        p = TEIEmbeddingProvider()
        assert p.dimension > 0

    def test_close(self):
        mock_client = MagicMock()
        p = TEIEmbeddingProvider()
        p._client = mock_client
        p.close()
        mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# DualEmbeddingProvider
# ---------------------------------------------------------------------------

from src.embedding.dual_provider import DualEmbeddingProvider, DualEmbedding


class TestDualEmbedding:
    def test_defaults(self):
        d = DualEmbedding(dense=[0.1, 0.2])
        assert d.sparse == {}
        assert d.colbert == []
        assert d.text == ""


class TestDualProvider:
    def _make_mock_provider(self, dense_vecs=None, sparse=None):
        provider = MagicMock(spec=["encode", "dimension"])
        provider.dimension = 1024
        # DualEmbeddingProvider checks hasattr(provider, "DIMENSION") first
        del provider.DIMENSION  # ensure it's not set
        dense_vecs = dense_vecs or [[0.1] * 1024]
        sparse = sparse or [{"123": 0.5}]
        provider.encode.return_value = {
            "dense_vecs": dense_vecs,
            "lexical_weights": sparse,
            "colbert_vecs": [],
        }
        return provider

    async def test_embed_dual(self):
        provider = self._make_mock_provider()
        dual = DualEmbeddingProvider(provider=provider)
        result = await dual.embed_dual("hello")
        assert len(result.dense) == 1024

    async def test_embed_dual_batch(self):
        provider = self._make_mock_provider(
            dense_vecs=[[0.1] * 1024, [0.2] * 1024],
            sparse=[{"1": 0.5}, {"2": 0.3}],
        )
        dual = DualEmbeddingProvider(provider=provider)
        results = await dual.embed_dual_batch(["a", "b"])
        assert len(results) == 2

    async def test_embed_dual_batch_empty(self):
        dual = DualEmbeddingProvider(provider=MagicMock())
        results = await dual.embed_dual_batch([])
        assert results == []

    async def test_embed_dual_encode_failure(self):
        provider = MagicMock()
        provider.encode.side_effect = Exception("encode failed")
        dual = DualEmbeddingProvider(provider=provider)
        result = await dual.embed_dual("hello")
        assert result.dense == []

    async def test_embed_interface(self):
        provider = self._make_mock_provider()
        dual = DualEmbeddingProvider(provider=provider)
        result = await dual.embed("hello")
        assert len(result) == 1024

    async def test_embed_failure_returns_zeros(self):
        provider = MagicMock(spec=["encode", "dimension"])
        provider.encode.side_effect = Exception("fail")
        provider.dimension = 1024
        dual = DualEmbeddingProvider(provider=provider)
        result = await dual.embed("hello")
        assert result == [0.0] * 1024

    async def test_embed_documents(self):
        provider = self._make_mock_provider(
            dense_vecs=[[0.1] * 1024, [0.2] * 1024],
        )
        dual = DualEmbeddingProvider(provider=provider)
        result = await dual.embed_documents(["a", "b"])
        assert len(result) == 2

    async def test_embed_documents_empty(self):
        dual = DualEmbeddingProvider(provider=MagicMock())
        result = await dual.embed_documents([])
        assert result == []

    async def test_embed_documents_failure(self):
        provider = MagicMock(spec=["encode", "dimension"])
        provider.encode.side_effect = Exception("fail")
        provider.dimension = 1024
        dual = DualEmbeddingProvider(provider=provider)
        result = await dual.embed_documents(["a"])
        assert result == [[0.0] * 1024]

    def test_dimension_from_provider(self):
        provider = MagicMock(spec=["encode", "dimension"])
        provider.dimension = 512
        dual = DualEmbeddingProvider(provider=provider)
        assert dual.dimension == 512

    def test_dimension_fallback(self):
        provider = MagicMock(spec=[])  # no dimension attr
        dual = DualEmbeddingProvider(provider=provider)
        # Falls back to EXPECTED_DIMENSION
        assert dual.dimension > 0

    async def test_no_encode_method(self):
        provider = MagicMock(spec=[])  # no encode
        dual = DualEmbeddingProvider(provider=provider)
        result = await dual.embed("hello")
        assert result == [0.0] * dual.dimension

    def test_sanitize_texts(self):
        provider = MagicMock()
        dual = DualEmbeddingProvider(provider=provider, max_text_chars=10)
        sanitized = dual._sanitize_texts(["a" * 20, "b" * 5])
        assert len(sanitized[0]) == 10
        assert len(sanitized[1]) == 5

    def test_sanitize_empty(self):
        dual = DualEmbeddingProvider(provider=MagicMock())
        assert dual._sanitize_texts([]) == []

    async def test_embed_dual_batch_partial_results(self):
        """When provider returns fewer vectors than inputs."""
        provider = MagicMock(spec=["encode", "dimension"])
        provider.dimension = 1024
        provider.encode.return_value = {
            "dense_vecs": [[0.1] * 1024],  # only 1 vec for 2 inputs
            "lexical_weights": [],
            "colbert_vecs": [],
        }
        dual = DualEmbeddingProvider(provider=provider)
        results = await dual.embed_dual_batch(["a", "b"])
        assert len(results) == 2
        # Second should have empty/zero dense
        assert len(results[1].dense) == 1024
