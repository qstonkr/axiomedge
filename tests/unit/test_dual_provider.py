"""Unit tests for DualEmbeddingProvider."""

from __future__ import annotations

import pytest

from src.nlp.embedding.dual_provider import DualEmbeddingProvider, DualEmbedding
from src.nlp.embedding.embedding_guard import EXPECTED_DIMENSION


# ---------------------------------------------------------------------------
# Fake underlying provider
# ---------------------------------------------------------------------------

class FakeProvider:
    """Fake BGE-M3 provider with encode() method."""

    DIMENSION = EXPECTED_DIMENSION

    def __init__(self, fail: bool = False):
        self._fail = fail
        self.encode_calls: list[dict] = []

    def encode(
        self,
        texts: list[str],
        return_dense: bool = True,
        return_sparse: bool = True,
        return_colbert: bool = False,
    ) -> dict:
        self.encode_calls.append({
            "texts": texts,
            "return_dense": return_dense,
            "return_sparse": return_sparse,
        })
        if self._fail:
            raise RuntimeError("Encoding failed")

        dense_vecs = [[0.1] * self.DIMENSION for _ in texts]
        sparse_vecs = [{100: 0.5, 200: 0.3} for _ in texts]
        colbert_vecs = [[[0.01] * 128] for _ in texts] if return_colbert else []

        return {
            "dense_vecs": dense_vecs,
            "lexical_weights": sparse_vecs,
            "colbert_vecs": colbert_vecs,
        }


class NoEncodeProvider:
    """Provider without encode() method."""
    pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDualEmbeddingProvider:
    @pytest.fixture
    def provider(self) -> FakeProvider:
        return FakeProvider()

    @pytest.fixture
    def dual(self, provider: FakeProvider) -> DualEmbeddingProvider:
        return DualEmbeddingProvider(provider=provider, use_sparse=True, use_colbert=False)

    @pytest.mark.asyncio
    async def test_embed_dual_single(self, dual: DualEmbeddingProvider) -> None:
        result = await dual.embed_dual("테스트 쿼리")
        assert isinstance(result, DualEmbedding)
        assert len(result.dense) == EXPECTED_DIMENSION
        assert isinstance(result.sparse, dict)
        assert len(result.sparse) > 0

    @pytest.mark.asyncio
    async def test_embed_dual_batch(self, dual: DualEmbeddingProvider) -> None:
        results = await dual.embed_dual_batch(["쿼리1", "쿼리2"])
        assert len(results) == 2
        for r in results:
            assert len(r.dense) == EXPECTED_DIMENSION

    @pytest.mark.asyncio
    async def test_embed_dual_empty(self, dual: DualEmbeddingProvider) -> None:
        results = await dual.embed_dual_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_embed_dense_only(self, dual: DualEmbeddingProvider) -> None:
        vec = await dual.embed("단일 임베딩")
        assert len(vec) == EXPECTED_DIMENSION
        assert any(v != 0.0 for v in vec)

    @pytest.mark.asyncio
    async def test_embed_documents(self, dual: DualEmbeddingProvider) -> None:
        vecs = await dual.embed_documents(["문서1", "문서2", "문서3"])
        assert len(vecs) == 3
        for v in vecs:
            assert len(v) == EXPECTED_DIMENSION

    @pytest.mark.asyncio
    async def test_embed_documents_empty(self, dual: DualEmbeddingProvider) -> None:
        vecs = await dual.embed_documents([])
        assert vecs == []


class TestDualProviderFallback:
    @pytest.mark.asyncio
    async def test_encode_failure_returns_empty(self) -> None:
        dual = DualEmbeddingProvider(provider=FakeProvider(fail=True))
        result = await dual.embed_dual("실패 테스트")
        assert result.dense == []
        assert result.sparse == {}

    @pytest.mark.asyncio
    async def test_no_encode_method_returns_zeros(self) -> None:
        dual = DualEmbeddingProvider(provider=NoEncodeProvider())
        vec = await dual.embed("인코드없음")
        assert len(vec) == EXPECTED_DIMENSION
        assert all(v == 0.0 for v in vec)

    @pytest.mark.asyncio
    async def test_embed_documents_failure_returns_zeros(self) -> None:
        dual = DualEmbeddingProvider(provider=FakeProvider(fail=True))
        vecs = await dual.embed_documents(["실패1", "실패2"])
        assert len(vecs) == 2
        for v in vecs:
            assert len(v) == EXPECTED_DIMENSION
            assert all(val == 0.0 for val in v)


class TestDualProviderConfig:
    def test_dimension_from_provider_attr(self) -> None:
        dual = DualEmbeddingProvider(provider=FakeProvider())
        assert dual.dimension == EXPECTED_DIMENSION

    def test_dimension_fallback(self) -> None:
        dual = DualEmbeddingProvider(provider=NoEncodeProvider())
        assert dual.dimension == EXPECTED_DIMENSION

    def test_sanitize_truncates(self) -> None:
        dual = DualEmbeddingProvider(provider=FakeProvider(), max_text_chars=10)
        sanitized = dual._sanitize_texts(["a" * 100])
        assert len(sanitized[0]) == 10

    def test_sanitize_empty(self) -> None:
        dual = DualEmbeddingProvider(provider=FakeProvider())
        assert dual._sanitize_texts([]) == []

    def test_batch_size_minimum(self) -> None:
        dual = DualEmbeddingProvider(provider=FakeProvider(), batch_size=0)
        assert dual._batch_size == 1
