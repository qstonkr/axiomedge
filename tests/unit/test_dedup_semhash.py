"""Unit tests for SemHash semantic dedup."""

from __future__ import annotations

import asyncio
import math

import pytest

from src.pipelines.dedup.semhash import DocumentEmbedding, SemHash, SemanticMatch


class FakeEmbeddingProvider:
    """Deterministic embedding provider for testing.

    Produces a simple hash-based embedding so identical texts get identical vectors.
    """

    def __init__(self, dimension: int = 16):
        self._dimension = dimension

    async def embed(self, text: str) -> list[float]:
        """Generate a deterministic embedding from text."""
        h = hash(text)
        vec = []
        for i in range(self._dimension):
            val = ((h + i * 997) % 10000) / 10000.0
            vec.append(val)
        # Normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class FixedEmbeddingProvider:
    """Returns a fixed embedding passed at construction."""

    def __init__(self, embeddings: dict[str, list[float]]):
        self._embeddings = embeddings
        self._default = [0.0] * 4

    async def embed(self, text: str) -> list[float]:
        return self._embeddings.get(text, self._default)


@pytest.fixture
def fake_provider():
    return FakeEmbeddingProvider(dimension=16)


class TestCosineSimlarity:
    """Tests for the static cosine_similarity method."""

    def test_identical_vectors(self):
        assert SemHash.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert SemHash.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert SemHash.cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_empty_vectors(self):
        assert SemHash.cosine_similarity([], []) == 0.0

    def test_mismatched_lengths(self):
        assert SemHash.cosine_similarity([1.0], [1.0, 2.0]) == 0.0

    def test_zero_vector(self):
        assert SemHash.cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


class TestSemHashAdd:
    """Tests for adding documents."""

    @pytest.mark.asyncio
    async def test_add_single_document(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.9)
        result = await sh.add("doc1", "hello world")
        assert isinstance(result, DocumentEmbedding)
        assert result.doc_id == "doc1"
        assert len(result.embedding) == 16
        assert sh.document_count == 1

    @pytest.mark.asyncio
    async def test_add_stores_text_preview(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.9)
        long_text = "x" * 300
        result = await sh.add("doc1", long_text)
        assert len(result.text_preview) == 200

    @pytest.mark.asyncio
    async def test_add_batch(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.9)
        docs = [("d1", "text one"), ("d2", "text two"), ("d3", "text three")]
        results = await sh.add_batch(docs)
        assert len(results) == 3
        assert sh.document_count == 3

    @pytest.mark.asyncio
    async def test_add_dimension_mismatch_skips(self):
        """If a subsequent embedding has different dimension, doc is skipped."""
        call_count = 0

        class VaryingProvider:
            async def embed(self, text: str) -> list[float]:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return [1.0, 2.0, 3.0]
                return [1.0, 2.0]  # different dimension

        sh = SemHash(embedding_provider=VaryingProvider(), threshold=0.9)
        await sh.add("doc1", "first")
        result = await sh.add("doc2", "second")
        assert result.embedding == []
        assert sh.document_count == 1  # second doc not stored


class TestSemHashFindSimilar:
    """Tests for find_similar."""

    @pytest.mark.asyncio
    async def test_find_similar_identical_text(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.9)
        await sh.add("doc1", "hello world")
        matches = await sh.find_similar("doc2", "hello world")
        assert len(matches) >= 1
        assert matches[0].similarity == pytest.approx(1.0)
        assert matches[0].is_duplicate is True

    @pytest.mark.asyncio
    async def test_find_similar_excludes_self(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.9)
        await sh.add("doc1", "test text")
        matches = await sh.find_similar("doc1", "test text")
        doc_ids = [m.doc_id_2 for m in matches]
        assert "doc1" not in doc_ids

    @pytest.mark.asyncio
    async def test_find_similar_top_k(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.01)
        for i in range(10):
            await sh.add(f"doc{i}", f"text number {i}")
        matches = await sh.find_similar("query", "text number 0", top_k=3)
        assert len(matches) <= 3

    @pytest.mark.asyncio
    async def test_find_similar_sorted_descending(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.01)
        await sh.add("doc1", "alpha beta")
        await sh.add("doc2", "gamma delta")
        matches = await sh.find_similar("q", "alpha beta", top_k=10)
        if len(matches) >= 2:
            assert matches[0].similarity >= matches[1].similarity


class TestSemHashFindDuplicates:
    """Tests for find_duplicates."""

    @pytest.mark.asyncio
    async def test_find_duplicates_identical(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.9)
        await sh.add("doc1", "same text here")
        await sh.add("doc2", "same text here")
        dupes = await sh.find_duplicates()
        assert len(dupes) >= 1
        assert dupes[0].is_duplicate is True
        assert dupes[0].similarity == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_find_duplicates_none(self):
        """With controlled embeddings, orthogonal vectors should not be duplicates."""
        provider = FixedEmbeddingProvider({
            "text_a": [1.0, 0.0, 0.0, 0.0],
            "text_b": [0.0, 1.0, 0.0, 0.0],
        })
        sh = SemHash(embedding_provider=provider, threshold=0.9)
        await sh.add("doc1", "text_a")
        await sh.add("doc2", "text_b")
        dupes = await sh.find_duplicates()
        assert len(dupes) == 0

    @pytest.mark.asyncio
    async def test_find_duplicates_sorted(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.01)
        await sh.add("a", "hello")
        await sh.add("b", "hello")
        await sh.add("c", "world")
        dupes = await sh.find_duplicates()
        if len(dupes) >= 2:
            assert dupes[0].similarity >= dupes[1].similarity


class TestSemHashCheckDuplicate:
    """Tests for check_duplicate."""

    @pytest.mark.asyncio
    async def test_check_duplicate_found(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.9)
        await sh.add("doc1", "some content")
        result = await sh.check_duplicate("doc2", "some content")
        assert result is not None
        assert result.is_duplicate is True

    @pytest.mark.asyncio
    async def test_check_duplicate_not_found(self):
        provider = FixedEmbeddingProvider({
            "text_a": [1.0, 0.0, 0.0, 0.0],
            "text_b": [0.0, 1.0, 0.0, 0.0],
        })
        sh = SemHash(embedding_provider=provider, threshold=0.9)
        await sh.add("doc1", "text_a")
        result = await sh.check_duplicate("doc2", "text_b")
        assert result is None


class TestSemHashMisc:
    """Tests for get_document, remove, clear, to_dict."""

    @pytest.mark.asyncio
    async def test_get_document(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.9)
        await sh.add("doc1", "hello")
        doc = sh.get_document("doc1")
        assert doc is not None
        assert doc.doc_id == "doc1"

    @pytest.mark.asyncio
    async def test_get_document_missing(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.9)
        assert sh.get_document("nonexistent") is None

    @pytest.mark.asyncio
    async def test_remove(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.9)
        await sh.add("doc1", "hello")
        assert sh.remove("doc1") is True
        assert sh.document_count == 0
        assert sh.remove("doc1") is False

    @pytest.mark.asyncio
    async def test_clear(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.9)
        await sh.add("doc1", "a")
        await sh.add("doc2", "b")
        count = sh.clear()
        assert count == 2
        assert sh.document_count == 0

    @pytest.mark.asyncio
    async def test_to_dict(self, fake_provider):
        sh = SemHash(embedding_provider=fake_provider, threshold=0.85)
        await sh.add("doc1", "text")
        d = sh.to_dict()
        assert d["threshold"] == 0.85
        assert d["document_count"] == 1

    def test_default_threshold(self):
        assert SemHash.DEFAULT_THRESHOLD == 0.90
