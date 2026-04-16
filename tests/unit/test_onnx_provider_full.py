"""Full unit tests for src/embedding/onnx_provider.py — 152 uncovered lines."""

from __future__ import annotations

import asyncio
import os
from collections import OrderedDict
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

from src.nlp.embedding.onnx_provider import OnnxBgeEmbeddingProvider


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# Init & properties
# ---------------------------------------------------------------------------

class TestInit:
    def test_defaults(self):
        p = OnnxBgeEmbeddingProvider()
        assert p.backend == "onnx"
        assert p.dimension == p._DENSE_DIM
        assert p._ready is False
        assert p._session is None
        assert p._tokenizer is None

    def test_custom_params(self):
        p = OnnxBgeEmbeddingProvider(
            model_name="custom/model",
            model_path="/tmp/model",
            use_fp16=False,
            use_sparse=False,
            max_length=256,
        )
        assert p._model_name == "custom/model"
        assert p._model_path == "/tmp/model"
        assert p._use_fp16 is False
        assert p._max_length == 256

    def test_cache_info(self):
        p = OnnxBgeEmbeddingProvider()
        info = p.cache_info
        assert info["hits"] == 0
        assert info["misses"] == 0
        assert info["size"] == 0


# ---------------------------------------------------------------------------
# _resolve_source
# ---------------------------------------------------------------------------

class TestResolveSource:
    def test_explicit_model_path(self):
        p = OnnxBgeEmbeddingProvider(model_path="/my/model")
        assert p._resolve_source() == "/my/model"

    def test_env_var(self):
        p = OnnxBgeEmbeddingProvider()
        with patch.dict(os.environ, {"KNOWLEDGE_BGE_ONNX_MODEL_PATH": "/env/model"}):
            assert p._resolve_source() == "/env/model"

    def test_s3_cache(self):
        p = OnnxBgeEmbeddingProvider()
        with patch.dict(os.environ, {"KNOWLEDGE_BGE_ONNX_MODEL_PATH": "", "KNOWLEDGE_BGE_S3_CACHE_DIR": "/tmp/test"}):
            with patch("os.path.isfile") as mock_isfile:
                mock_isfile.side_effect = lambda path: True  # Both files exist
                result = p._resolve_source()
                assert result == "/tmp/test"

    def test_fallback_to_model_name(self):
        p = OnnxBgeEmbeddingProvider(model_name="BAAI/bge-m3")
        with patch.dict(os.environ, {"KNOWLEDGE_BGE_ONNX_MODEL_PATH": ""}):
            with patch("os.path.isfile", return_value=False):
                assert p._resolve_source() == "BAAI/bge-m3"


# ---------------------------------------------------------------------------
# is_ready
# ---------------------------------------------------------------------------

class TestIsReady:
    def test_not_ready_initially(self):
        p = OnnxBgeEmbeddingProvider()
        # _ensure_model will fail since no model file exists
        with patch.object(p, "_ensure_model", side_effect=FileNotFoundError("no model")):
            with pytest.raises(FileNotFoundError):
                p.is_ready()

    def test_ready_after_init(self):
        p = OnnxBgeEmbeddingProvider()
        p._ready = True
        assert p.is_ready() is True


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------

class TestClose:
    def test_close_releases_session(self):
        p = OnnxBgeEmbeddingProvider()
        p._session = MagicMock()
        p.close()
        assert p._session is None


# ---------------------------------------------------------------------------
# _extract_dense
# ---------------------------------------------------------------------------

class TestExtractDense:
    def test_sentence_embedding(self):
        p = OnnxBgeEmbeddingProvider()
        output_map = {
            "sentence_embedding": np.array([[0.1, 0.2, 0.3]], dtype=np.float32),
        }
        mask = np.array([[1, 1, 1]], dtype=np.int64)
        result = p._extract_dense(output_map, mask, 1)
        assert len(result) == 1
        assert len(result[0]) == 3

    def test_token_embeddings_fallback(self):
        p = OnnxBgeEmbeddingProvider()
        token_emb = np.random.randn(1, 5, 3).astype(np.float32)
        mask = np.array([[1, 1, 1, 0, 0]], dtype=np.int64)
        output_map = {"token_embeddings": token_emb}
        result = p._extract_dense(output_map, mask, 1)
        assert len(result) == 1
        assert len(result[0]) == 3

    def test_last_hidden_state_fallback(self):
        p = OnnxBgeEmbeddingProvider()
        hidden = np.random.randn(1, 5, 3).astype(np.float32)
        mask = np.array([[1, 1, 1, 0, 0]], dtype=np.int64)
        output_map = {"last_hidden_state": hidden}
        result = p._extract_dense(output_map, mask, 1)
        assert len(result) == 1

    def test_last_resort_2d(self):
        p = OnnxBgeEmbeddingProvider()
        arr = np.random.randn(2, 3).astype(np.float32)
        output_map = {"custom_output": arr}
        mask = np.array([[1, 1, 1], [1, 1, 1]], dtype=np.int64)
        result = p._extract_dense(output_map, mask, 2)
        assert len(result) == 2

    def test_no_usable_output(self):
        p = OnnxBgeEmbeddingProvider()
        output_map = {"weird": np.array([1, 2, 3])}
        mask = np.array([[1]], dtype=np.int64)
        result = p._extract_dense(output_map, mask, 2)
        assert len(result) == 2
        assert result[0] == []


# ---------------------------------------------------------------------------
# _extract_sparse
# ---------------------------------------------------------------------------

class TestExtractSparse:
    def test_basic(self):
        input_ids = np.array([[101, 2003, 102, 0]], dtype=np.int64)
        attention_mask = np.array([[1, 1, 1, 0]], dtype=np.int64)
        result = OnnxBgeEmbeddingProvider._extract_sparse(input_ids, attention_mask)
        assert len(result) == 1
        assert isinstance(result[0], dict)
        # Token 101, 2003, 102 should have weights
        assert 101 in result[0]
        assert 0 not in result[0]  # PAD token excluded

    def test_normalization(self):
        input_ids = np.array([[5, 5, 5, 10]], dtype=np.int64)
        attention_mask = np.array([[1, 1, 1, 1]], dtype=np.int64)
        result = OnnxBgeEmbeddingProvider._extract_sparse(input_ids, attention_mask)
        assert len(result) == 1
        # Token 5 appears 3 times, token 10 once. Max is 3. Normalized: 5->1.0, 10->0.333
        assert result[0][5] == 1.0
        assert abs(result[0][10] - 1/3) < 0.01

    def test_empty_after_filter(self):
        input_ids = np.array([[0, 0, 0]], dtype=np.int64)
        attention_mask = np.array([[1, 1, 1]], dtype=np.int64)
        result = OnnxBgeEmbeddingProvider._extract_sparse(input_ids, attention_mask)
        assert result[0] == {}

    def test_multiple_rows(self):
        input_ids = np.array([[1, 2, 3], [4, 5, 0]], dtype=np.int64)
        attention_mask = np.array([[1, 1, 1], [1, 1, 0]], dtype=np.int64)
        result = OnnxBgeEmbeddingProvider._extract_sparse(input_ids, attention_mask)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _extract_colbert
# ---------------------------------------------------------------------------

class TestExtractColbert:
    def test_with_token_embeddings(self):
        token_emb = np.random.randn(1, 5, 3).astype(np.float32)
        mask = np.array([[1, 1, 1, 0, 0]], dtype=np.int64)
        output_map = {"token_embeddings": token_emb}
        result = OnnxBgeEmbeddingProvider._extract_colbert(output_map, mask)
        assert len(result) == 1
        assert len(result[0]) == 3  # 3 valid tokens

    def test_no_token_embeddings(self):
        output_map = {}
        mask = np.array([[1, 1]], dtype=np.int64)
        result = OnnxBgeEmbeddingProvider._extract_colbert(output_map, mask)
        assert result == [[]]

    def test_empty_mask(self):
        token_emb = np.random.randn(1, 3, 4).astype(np.float32)
        mask = np.array([[0, 0, 0]], dtype=np.int64)
        output_map = {"token_embeddings": token_emb}
        result = OnnxBgeEmbeddingProvider._extract_colbert(output_map, mask)
        assert result == [[]]


# ---------------------------------------------------------------------------
# _mean_pool_numpy
# ---------------------------------------------------------------------------

class TestMeanPool:
    def test_basic(self):
        token_emb = np.array([[[1.0, 2.0], [3.0, 4.0], [0.0, 0.0]]], dtype=np.float32)
        mask = np.array([[1, 1, 0]], dtype=np.int64)
        result = OnnxBgeEmbeddingProvider._mean_pool_numpy(token_emb, mask)
        expected = np.array([[2.0, 3.0]], dtype=np.float32)
        np.testing.assert_allclose(result, expected, atol=1e-6)


# ---------------------------------------------------------------------------
# encode (full path with mock session)
# ---------------------------------------------------------------------------

class TestEncode:
    def _make_ready_provider(self):
        p = OnnxBgeEmbeddingProvider()
        p._ready = True

        # Mock tokenizer
        p._tokenizer = MagicMock()
        p._tokenizer.return_value = {
            "input_ids": np.array([[101, 2003, 102]], dtype=np.int64),
            "attention_mask": np.array([[1, 1, 1]], dtype=np.int64),
        }

        # Mock ONNX session
        sentence_emb = np.random.randn(1, 1024).astype(np.float32)
        p._session = MagicMock()
        p._session.run = MagicMock(return_value=[sentence_emb])
        p._session.get_outputs = MagicMock(return_value=[MagicMock(name="sentence_embedding")])
        p._output_names = ["sentence_embedding"]

        return p

    def test_encode_empty(self):
        p = self._make_ready_provider()
        result = p.encode([])
        assert result["dense_vecs"] == []

    def test_encode_single(self):
        p = self._make_ready_provider()
        result = p.encode(["hello"])
        assert len(result["dense_vecs"]) == 1
        assert len(result["dense_vecs"][0]) == 1024

    def test_encode_cache_hit(self):
        p = self._make_ready_provider()
        # First call
        p.encode(["hello"])
        assert p._cache_misses == 1
        # Second call (cache hit)
        p.encode(["hello"])
        assert p._cache_hits == 1

    def test_encode_cache_eviction(self):
        p = self._make_ready_provider()
        p._cache_max = 2
        p.encode(["a"])
        p.encode(["b"])
        p.encode(["c"])  # evicts "a"
        assert len(p._cache) == 2

    def test_encode_no_sparse(self):
        p = self._make_ready_provider()
        result = p.encode(["hello"], return_sparse=False)
        assert result["lexical_weights"] == []

    def test_encode_with_colbert(self):
        p = self._make_ready_provider()
        # Add token_embeddings for colbert
        token_emb = np.random.randn(1, 3, 1024).astype(np.float32)
        p._session.run = MagicMock(return_value=[
            np.random.randn(1, 1024).astype(np.float32),
            token_emb,
        ])
        p._output_names = ["sentence_embedding", "token_embeddings"]
        result = p.encode(["hello"], return_colbert_vecs=True)
        # colbert_vecs should not be empty
        assert isinstance(result["colbert_vecs"], list)

    def test_encode_not_ready(self):
        p = OnnxBgeEmbeddingProvider()
        p._ready = False
        with patch.object(p, "_ensure_model", side_effect=FileNotFoundError):
            with pytest.raises((RuntimeError, FileNotFoundError)):
                p.encode(["hello"])


# ---------------------------------------------------------------------------
# embed / embed_documents / embed_batch
# ---------------------------------------------------------------------------

class TestEmbedAsync:
    def test_embed_empty(self):
        p = OnnxBgeEmbeddingProvider()
        result = _run(p.embed(""))
        assert result == []

    def test_embed_documents_empty(self):
        p = OnnxBgeEmbeddingProvider()
        result = _run(p.embed_documents([]))
        assert result == []

    def test_embed_batch_alias(self):
        p = OnnxBgeEmbeddingProvider()
        result = _run(p.embed_batch([]))
        assert result == []
