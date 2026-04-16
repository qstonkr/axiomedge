"""Unit tests for MinHasher and LSHBloom."""

from __future__ import annotations

import pytest

from src.pipelines.dedup.lshbloom import LSHBloom, MinHashSignature, MinHasher, SimilarPair


class TestMinHasher:
    """Tests for MinHasher."""

    def test_compute_signature_returns_correct_length(self):
        mh = MinHasher(num_hashes=64, shingle_size=3, seed=42)
        sig = mh.compute_signature("doc1", "the quick brown fox jumps over the lazy dog")
        assert len(sig.signature) == 64
        assert sig.doc_id == "doc1"

    def test_compute_signature_deterministic(self):
        mh = MinHasher(num_hashes=64, shingle_size=3, seed=42)
        sig1 = mh.compute_signature("doc1", "hello world test")
        sig2 = mh.compute_signature("doc1", "hello world test")
        assert sig1.signature == sig2.signature

    def test_identical_texts_high_similarity(self):
        mh = MinHasher(num_hashes=128, shingle_size=3, seed=42)
        sig1 = mh.compute_signature("a", "the quick brown fox jumps over the lazy dog")
        sig2 = mh.compute_signature("b", "the quick brown fox jumps over the lazy dog")
        sim = MinHasher.estimate_similarity(sig1, sig2)
        assert sim == 1.0

    def test_different_texts_lower_similarity(self):
        mh = MinHasher(num_hashes=128, shingle_size=3, seed=42)
        sig1 = mh.compute_signature("a", "the quick brown fox jumps over the lazy dog")
        sig2 = mh.compute_signature("b", "a completely different text about something else entirely")
        sim = MinHasher.estimate_similarity(sig1, sig2)
        assert sim < 0.5

    def test_similar_texts_moderate_similarity(self):
        mh = MinHasher(num_hashes=128, shingle_size=3, seed=42)
        text1 = "the quick brown fox jumps over the lazy dog in the park today"
        text2 = "the quick brown fox leaps over the lazy dog in the garden today"
        sig1 = mh.compute_signature("a", text1)
        sig2 = mh.compute_signature("b", text2)
        sim = MinHasher.estimate_similarity(sig1, sig2)
        assert 0.1 < sim < 0.95

    def test_empty_text_signature(self):
        mh = MinHasher(num_hashes=64, shingle_size=3, seed=42)
        sig = mh.compute_signature("doc1", "")
        assert len(sig.signature) == 64

    def test_short_text_below_shingle_size(self):
        mh = MinHasher(num_hashes=64, shingle_size=3, seed=42)
        sig = mh.compute_signature("doc1", "hi")
        assert len(sig.signature) == 64

    def test_estimate_similarity_mismatched_lengths(self):
        sig1 = MinHashSignature(doc_id="a", signature=[1, 2, 3])
        sig2 = MinHashSignature(doc_id="b", signature=[1, 2])
        assert MinHasher.estimate_similarity(sig1, sig2) == 0.0

    def test_different_seeds_produce_different_signatures(self):
        mh1 = MinHasher(num_hashes=64, seed=1)
        mh2 = MinHasher(num_hashes=64, seed=2)
        text = "the quick brown fox"
        sig1 = mh1.compute_signature("d", text)
        sig2 = mh2.compute_signature("d", text)
        assert sig1.signature != sig2.signature

    def test_shingle_size_affects_granularity(self):
        mh_small = MinHasher(num_hashes=128, shingle_size=2, seed=42)
        mh_large = MinHasher(num_hashes=128, shingle_size=5, seed=42)
        text = "one two three four five six seven eight nine ten"
        sig_small = mh_small.compute_signature("a", text)
        sig_large = mh_large.compute_signature("a", text)
        assert sig_small.signature != sig_large.signature


class TestLSHBloom:
    """Tests for LSHBloom."""

    def test_add_and_document_count(self):
        lsh = LSHBloom(num_hashes=64, bands=8)
        lsh.add("doc1", "some text here for testing")
        lsh.add("doc2", "another text for comparison")
        assert lsh.document_count == 2

    def test_find_similar_identical_docs(self):
        lsh = LSHBloom(num_hashes=128, bands=16)
        text = "the quick brown fox jumps over the lazy dog in the park today"
        lsh.add("doc1", text)
        results = lsh.find_similar("doc2", text)
        assert len(results) >= 1
        assert results[0].doc_id_2 == "doc1"
        assert results[0].estimated_similarity == 1.0

    def test_find_similar_different_docs(self):
        lsh = LSHBloom(num_hashes=128, bands=16)
        lsh.add("doc1", "the quick brown fox jumps over the lazy dog")
        results = lsh.find_similar(
            "doc2", "a completely unrelated text about quantum physics and black holes"
        )
        for r in results:
            assert r.estimated_similarity < 0.8

    def test_find_similar_excludes_self(self):
        lsh = LSHBloom(num_hashes=128, bands=16)
        text = "test document content here"
        lsh.add("doc1", text)
        results = lsh.find_similar("doc1", text)
        doc_ids = [r.doc_id_2 for r in results]
        assert "doc1" not in doc_ids

    def test_find_duplicates_with_threshold(self):
        lsh = LSHBloom(num_hashes=128, bands=16)
        text = "the quick brown fox jumps over the lazy dog in the sunny park"
        lsh.add("doc1", text)
        lsh.add("doc2", text)
        lsh.add("doc3", "completely different text about something else entirely unrelated")
        dupes = lsh.find_duplicates(threshold=0.8)
        pair_ids = {(d.doc_id_1, d.doc_id_2) for d in dupes}
        assert ("doc1", "doc2") in pair_ids or ("doc2", "doc1") in pair_ids

    def test_find_duplicates_no_duplicates(self):
        lsh = LSHBloom(num_hashes=128, bands=16)
        lsh.add("doc1", "alpha beta gamma delta epsilon zeta eta theta iota")
        lsh.add("doc2", "one two three four five six seven eight nine ten")
        dupes = lsh.find_duplicates(threshold=0.8)
        assert len(dupes) == 0

    def test_find_duplicates_sorted_descending(self):
        lsh = LSHBloom(num_hashes=128, bands=16)
        base = "the quick brown fox jumps over the lazy dog in the park on a sunny day"
        lsh.add("doc1", base)
        lsh.add("doc2", base)
        lsh.add("doc3", base.replace("fox", "cat").replace("park", "garden"))
        dupes = lsh.find_duplicates(threshold=0.3)
        if len(dupes) >= 2:
            assert dupes[0].estimated_similarity >= dupes[1].estimated_similarity

    def test_clear(self):
        lsh = LSHBloom(num_hashes=64, bands=8)
        lsh.add("doc1", "test text")
        lsh.clear()
        assert lsh.document_count == 0

    def test_to_dict(self):
        lsh = LSHBloom(num_hashes=128, bands=16)
        lsh.add("doc1", "test")
        d = lsh.to_dict()
        assert d["num_hashes"] == 128
        assert d["bands"] == 16
        assert d["rows_per_band"] == 8
        assert d["document_count"] == 1

    def test_find_similar_returns_similar_pair_objects(self):
        lsh = LSHBloom(num_hashes=128, bands=16)
        lsh.add("doc1", "hello world this is a test document for similarity")
        results = lsh.find_similar("doc2", "hello world this is a test document for similarity")
        for r in results:
            assert isinstance(r, SimilarPair)
            assert r.doc_id_1 == "doc2"

    def test_rows_per_band_calculation(self):
        lsh = LSHBloom(num_hashes=100, bands=10)
        assert lsh._rows_per_band == 10

    def test_multiple_adds_same_doc_id(self):
        """Adding same doc_id twice should not duplicate in buckets."""
        lsh = LSHBloom(num_hashes=64, bands=8)
        lsh.add("doc1", "hello world test")
        lsh.add("doc1", "hello world test")
        assert lsh.document_count == 1
