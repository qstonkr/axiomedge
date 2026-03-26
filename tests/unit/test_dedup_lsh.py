"""Unit tests for LSHBloom (Stage 2 MinHash LSH)."""

from __future__ import annotations

from src.pipeline.dedup.lshbloom import LSHBloom, MinHasher


class TestMinHasher:
    """Tests for MinHasher similarity estimation."""

    def test_minhash_identical_texts_high_similarity(self):
        """Identical texts should have similarity == 1.0."""
        hasher = MinHasher(num_hashes=128, shingle_size=3, seed=42)
        text = "the quick brown fox jumps over the lazy dog and then some more words"

        sig1 = hasher.compute_signature("doc1", text)
        sig2 = hasher.compute_signature("doc2", text)

        similarity = MinHasher.estimate_similarity(sig1, sig2)
        assert similarity == 1.0

    def test_minhash_different_texts_low_similarity(self):
        """Completely different texts should have low similarity."""
        hasher = MinHasher(num_hashes=128, shingle_size=3, seed=42)

        sig1 = hasher.compute_signature(
            "doc1",
            "the quick brown fox jumps over the lazy dog repeatedly in the park"
        )
        sig2 = hasher.compute_signature(
            "doc2",
            "quantum computing leverages superposition and entanglement for parallel processing tasks"
        )

        similarity = MinHasher.estimate_similarity(sig1, sig2)
        assert similarity < 0.3, f"Expected low similarity, got {similarity}"


class TestLSHBloom:
    """Tests for LSHBloom."""

    def test_lsh_find_similar_returns_near_duplicates(self):
        """Adding identical documents and querying should find them as near-duplicates."""
        lsh = LSHBloom(num_hashes=128, bands=16, shingle_size=3, seed=42)

        # Use the same text so MinHash signatures match perfectly
        text = (
            "knowledge management system for enterprise document storage and retrieval "
            "using vector databases and graph databases for semantic search capabilities "
            "with support for multiple file formats and automatic chunking pipeline"
        )

        lsh.add("doc-original", text)

        results = lsh.find_similar("doc-similar", text)
        # Identical text should always be found
        assert len(results) >= 1
        assert results[0].doc_id_2 == "doc-original"
        assert results[0].estimated_similarity == 1.0

    def test_lsh_find_duplicates(self):
        """find_duplicates should return pairs above threshold."""
        lsh = LSHBloom(num_hashes=128, bands=16, shingle_size=3, seed=42)

        text = "the infrastructure team manages kubernetes clusters and deploys microservices regularly"
        lsh.add("doc-a", text)
        lsh.add("doc-b", text)  # exact duplicate

        duplicates = lsh.find_duplicates(threshold=0.8)
        assert len(duplicates) >= 1
        pair = duplicates[0]
        assert pair.estimated_similarity == 1.0
        assert {pair.doc_id_1, pair.doc_id_2} == {"doc-a", "doc-b"}

    def test_lsh_empty(self):
        """Empty LSH should return no results."""
        lsh = LSHBloom()

        results = lsh.find_similar("doc1", "some text about nothing important")
        assert results == []

        duplicates = lsh.find_duplicates()
        assert duplicates == []

        assert lsh.document_count == 0
