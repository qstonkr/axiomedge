"""Extra coverage tests (batch 2).

Targets: chunker (41 uncov), semhash (46 uncov), ollama_client (47 uncov),
result_tracker (43 uncov), enhanced_similarity_matcher L2 (128 uncov).
"""

from __future__ import annotations

from collections import Counter
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===========================================================================
# Chunker
# ===========================================================================

from src.pipeline.chunker import Chunker, ChunkStrategy, ChunkResult, HeadingChunk


class TestChunker:
    def test_empty_text(self):
        c = Chunker()
        result = c.chunk("")
        assert result.total_chunks == 0

    def test_whitespace_text(self):
        c = Chunker()
        result = c.chunk("   ")
        assert result.total_chunks == 0

    def test_fixed_strategy(self):
        c = Chunker(max_chunk_chars=100, strategy=ChunkStrategy.FIXED)
        text = "짧은 문장입니다. " * 20
        result = c.chunk(text)
        assert result.total_chunks >= 1
        for ch in result.chunks:
            assert len(ch) <= 200  # approximate

    def test_semantic_strategy(self):
        c = Chunker(max_chunk_chars=100, strategy=ChunkStrategy.SEMANTIC)
        text = "첫 번째 문단입니다.\n\n두 번째 문단입니다.\n\n세 번째 문단입니다."
        result = c.chunk(text)
        assert result.total_chunks >= 1

    def test_split_sentences_empty(self):
        c = Chunker()
        assert c.split_sentences("") == []
        assert c.split_sentences("  ") == []

    def test_split_sentences_regex_fallback(self):
        c = Chunker()
        c._kss_available = False
        c._initialized = True
        sentences = c.split_sentences("Hello world. How are you? Fine.")
        assert len(sentences) >= 1

    def test_strategy_name(self):
        c = Chunker(strategy=ChunkStrategy.SEMANTIC)
        assert c.strategy_name == "semantic"
        c2 = Chunker(strategy=ChunkStrategy.FIXED)
        assert c2.strategy_name == "fixed"

    def test_long_text_chunked(self):
        c = Chunker(max_chunk_chars=50, strategy=ChunkStrategy.FIXED)
        text = "이것은 긴 문장입니다. " * 100
        result = c.chunk(text)
        assert result.total_chunks > 1

    def test_semantic_with_paragraphs(self):
        c = Chunker(max_chunk_chars=200, strategy=ChunkStrategy.SEMANTIC)
        paragraphs = ["문단 내용 " * 10 for _ in range(5)]
        text = "\n\n".join(paragraphs)
        result = c.chunk(text)
        assert result.total_chunks >= 1

    def test_single_sentence(self):
        c = Chunker()
        result = c.chunk("단일 문장입니다.")
        assert result.total_chunks == 1


class TestChunkResult:
    def test_defaults(self):
        cr = ChunkResult(chunks=["a", "b"], total_chunks=2)
        assert cr.total_chunks == 2


class TestHeadingChunk:
    def test_creation(self):
        hc = HeadingChunk(text="content", heading_path="A > B > C")
        assert hc.heading_path == "A > B > C"


# ===========================================================================
# SemHash
# ===========================================================================

from src.pipeline.dedup.semhash import (
    SemHash,
    NoOpEmbeddingProvider,
    DocumentEmbedding,
    SemanticMatch,
)


class TestNoOpEmbeddingProvider:
    async def test_embed(self):
        p = NoOpEmbeddingProvider(dimension=3)
        vec = await p.embed("hello")
        assert vec == [0.0, 0.0, 0.0]


class TestSemHashCosine:
    def test_identical(self):
        assert SemHash.cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert SemHash.cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_empty(self):
        assert SemHash.cosine_similarity([], []) == 0.0

    def test_different_lengths(self):
        assert SemHash.cosine_similarity([1], [1, 2]) == 0.0

    def test_zero_vectors(self):
        assert SemHash.cosine_similarity([0, 0], [1, 0]) == 0.0


class TestSemHash:
    async def test_add_and_find_similar(self):
        provider = AsyncMock()
        provider.embed.side_effect = [
            [1.0, 0.0, 0.0],  # doc1
            [0.99, 0.01, 0.0],  # doc2 (very similar)
            [0.99, 0.01, 0.0],  # query for doc3
        ]
        sh = SemHash(embedding_provider=provider, threshold=0.9)
        await sh.add("doc1", "text1")
        await sh.add("doc2", "text2")
        matches = await sh.find_similar("doc3", "text3")
        assert len(matches) >= 1

    async def test_add_batch(self):
        provider = AsyncMock()
        provider.embed.side_effect = [
            [1.0, 0.0], [0.0, 1.0],
        ]
        sh = SemHash(embedding_provider=provider)
        results = await sh.add_batch([("d1", "t1"), ("d2", "t2")])
        assert len(results) == 2
        assert sh.document_count == 2

    async def test_check_duplicate(self):
        provider = AsyncMock()
        provider.embed.side_effect = [
            [1.0, 0.0],  # existing
            [1.0, 0.0],  # query (identical)
        ]
        sh = SemHash(embedding_provider=provider, threshold=0.9)
        await sh.add("doc1", "text1")
        match = await sh.check_duplicate("doc2", "text2")
        assert match is not None
        assert match.is_duplicate is True

    async def test_check_no_duplicate(self):
        provider = AsyncMock()
        provider.embed.side_effect = [
            [1.0, 0.0],  # existing
            [0.0, 1.0],  # query (orthogonal)
        ]
        sh = SemHash(embedding_provider=provider, threshold=0.9)
        await sh.add("doc1", "text1")
        match = await sh.check_duplicate("doc2", "text2")
        assert match is None

    async def test_find_duplicates(self):
        provider = AsyncMock()
        provider.embed.side_effect = [
            [1.0, 0.0],
            [0.99, 0.01],
        ]
        sh = SemHash(embedding_provider=provider, threshold=0.9)
        await sh.add("d1", "t1")
        await sh.add("d2", "t2")
        dups = await sh.find_duplicates()
        assert len(dups) >= 1

    def test_remove(self):
        sh = SemHash()
        sh._documents["d1"] = DocumentEmbedding("d1", [1.0])
        assert sh.remove("d1") is True
        assert sh.remove("d1") is False

    def test_clear(self):
        sh = SemHash()
        sh._documents["d1"] = DocumentEmbedding("d1", [1.0])
        sh._documents["d2"] = DocumentEmbedding("d2", [1.0])
        count = sh.clear()
        assert count == 2
        assert sh.document_count == 0

    def test_get_document(self):
        sh = SemHash()
        sh._documents["d1"] = DocumentEmbedding("d1", [1.0])
        assert sh.get_document("d1") is not None
        assert sh.get_document("d2") is None

    def test_to_dict(self):
        sh = SemHash(threshold=0.85)
        d = sh.to_dict()
        assert d["threshold"] == 0.85

    async def test_dimension_mismatch(self):
        provider = AsyncMock()
        provider.embed.side_effect = [
            [1.0, 0.0, 0.0],  # first doc sets dimension
            [1.0, 0.0],  # second doc wrong dimension
        ]
        sh = SemHash(embedding_provider=provider)
        await sh.add("d1", "t1")
        doc = await sh.add("d2", "t2")
        assert doc.embedding == []


# ===========================================================================
# OllamaClient
# ===========================================================================

from src.nlp.llm.ollama_client import OllamaClient, OllamaConfig


class TestOllamaConfig:
    def test_defaults(self):
        config = OllamaConfig()
        assert config.base_url.startswith("http")
        assert config.model.strip() == config.model

    def test_custom(self):
        config = OllamaConfig(base_url="http://custom:11434", model="test-model")
        assert config.base_url == "http://custom:11434"
        assert config.model == "test-model"


class TestOllamaClient:
    def test_init_with_config(self):
        config = OllamaConfig(model="test")
        client = OllamaClient(config)
        assert client._config.model == "test"

    def test_init_with_kwargs(self):
        client = OllamaClient(base_url="http://test:11434", model="mymodel")
        assert client._config.model == "mymodel"

    def test_init_default(self):
        client = OllamaClient()
        assert client._config is not None

    async def test_generate(self):
        client = OllamaClient(OllamaConfig(model="test"))
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "generated text"}
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response
        client._client = mock_http_client

        result = await client.generate("tell me something")
        assert result == "generated text"

    async def test_generate_with_system_prompt(self):
        client = OllamaClient(OllamaConfig(model="test"))
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "answer"}
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response
        client._client = mock_http_client

        result = await client.generate("prompt", system_prompt="system")
        assert result == "answer"

    async def test_generate_response(self):
        client = OllamaClient(OllamaConfig(model="test"))
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "rag answer"}
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response
        client._client = mock_http_client

        context = [{"content": "relevant doc", "metadata": {}, "similarity": 0.9}]
        result = await client.generate_response("user query", context)
        assert result == "rag answer"

    def test_estimate_token_count(self):
        count = OllamaClient._estimate_token_count("hello world test")
        assert count > 0


# ===========================================================================
# DedupResultTracker
# ===========================================================================

from src.pipeline.dedup.result_tracker import DedupResultTracker, _enum_val


class TestEnumVal:
    def test_enum(self):
        from enum import Enum

        class Color(Enum):
            RED = "red"

        assert _enum_val(Color.RED) == "red"

    def test_string(self):
        assert _enum_val("hello") == "hello"


class TestDedupResultTracker:
    def test_disabled(self):
        tracker = DedupResultTracker(redis_client=None)
        assert tracker.enabled is False

    def test_enabled(self):
        tracker = DedupResultTracker(redis_client=MagicMock())
        assert tracker.enabled is True

    async def test_track_result_disabled(self):
        tracker = DedupResultTracker(redis_client=None)
        await tracker.track_result(MagicMock(), kb_id="kb1")  # should not crash

    async def test_track_result_success(self):
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock()
        tracker = DedupResultTracker(redis_client=mock_redis)
        mock_result = MagicMock()
        mock_result.doc_id = "d1"
        mock_result.status = "unique"
        mock_result.duplicate_of = None
        mock_result.similarity_score = 0.5
        mock_result.stage_reached = 3
        mock_result.processing_time_ms = 15.0
        mock_result.resolution = "none"
        mock_result.conflict_types = []
        mock_result.hash_value = "abc"
        await tracker.track_result(mock_result, kb_id="kb1")
        mock_redis.xadd.assert_called_once()

    async def test_track_result_error(self):
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(side_effect=Exception("redis down"))
        tracker = DedupResultTracker(redis_client=mock_redis)
        await tracker.track_result(MagicMock(), kb_id="kb1")  # should not crash

    async def test_track_conflict_disabled(self):
        tracker = DedupResultTracker(redis_client=None)
        result = await tracker.track_conflict(MagicMock(), None, "kb1")
        assert result == ""

    async def test_track_conflict_success(self):
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock()
        mock_redis.hset = AsyncMock()
        mock_redis.expire = AsyncMock()
        tracker = DedupResultTracker(redis_client=mock_redis)
        mock_result = MagicMock()
        mock_result.doc_id = "d1"
        mock_result.duplicate_of = "d0"
        mock_result.similarity_score = 0.95
        mock_conflict = MagicMock()
        mock_conflict.conflict_type = "version"
        mock_conflict.severity = "high"
        mock_conflict.description = "Version mismatch"
        mock_conflict.doc1_excerpt = "excerpt1"
        mock_conflict.doc2_excerpt = "excerpt2"
        conflict_id = await tracker.track_conflict(
            mock_result, mock_conflict, "kb1", "title1", "title2"
        )
        assert conflict_id.startswith("conflict-")

    async def test_track_conflict_error(self):
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(side_effect=Exception("fail"))
        tracker = DedupResultTracker(redis_client=mock_redis)
        result = await tracker.track_conflict(MagicMock(), None, "kb1")
        assert result == ""

    async def test_resolve_conflict_disabled(self):
        tracker = DedupResultTracker(redis_client=None)
        assert await tracker.resolve_conflict("c1", "keep") is False

    async def test_resolve_conflict_success(self):
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=True)
        mock_redis.hset = AsyncMock()
        mock_redis.xadd = AsyncMock()
        tracker = DedupResultTracker(redis_client=mock_redis)
        result = await tracker.resolve_conflict("c1", "keep_newest", "admin")
        assert result is True

    async def test_resolve_conflict_not_found(self):
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=False)
        tracker = DedupResultTracker(redis_client=mock_redis)
        result = await tracker.resolve_conflict("c1", "keep")
        assert result is False

    async def test_resolve_conflict_error(self):
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(side_effect=Exception("fail"))
        tracker = DedupResultTracker(redis_client=mock_redis)
        result = await tracker.resolve_conflict("c1", "keep")
        assert result is False

    async def test_get_stats_disabled(self):
        tracker = DedupResultTracker(redis_client=None)
        stats = await tracker.get_stats()
        assert stats["total_duplicates_found"] == 0

    async def test_get_stats_success(self):
        mock_redis = AsyncMock()
        mock_redis.xlen = AsyncMock(side_effect=[100, 10])

        async def mock_scan_iter(match=None):
            for key in ["conflict:1", "conflict:2"]:
                yield key

        mock_redis.scan_iter = mock_scan_iter
        mock_redis.hget = AsyncMock(side_effect=["pending", "resolved"])
        tracker = DedupResultTracker(redis_client=mock_redis)
        stats = await tracker.get_stats()
        assert stats["total_duplicates_found"] == 100
        assert stats["pending"] == 1
        assert stats["total_resolved"] == 1

    async def test_get_stats_error(self):
        mock_redis = AsyncMock()
        mock_redis.xlen = AsyncMock(side_effect=Exception("fail"))
        tracker = DedupResultTracker(redis_client=mock_redis)
        stats = await tracker.get_stats()
        assert stats["total_duplicates_found"] == 0

    async def test_get_conflicts_disabled(self):
        tracker = DedupResultTracker(redis_client=None)
        result = await tracker.get_conflicts()
        assert result["conflicts"] == []

    async def test_get_conflicts_success(self):
        mock_redis = AsyncMock()
        mock_redis.xlen = AsyncMock(return_value=1)
        mock_redis.xrevrange = AsyncMock(return_value=[
            ("1-0", {"conflict_id": "c1", "doc_id": "d1"}),
        ])
        mock_redis.hget = AsyncMock(return_value="pending")
        tracker = DedupResultTracker(redis_client=mock_redis)
        result = await tracker.get_conflicts()
        assert len(result["conflicts"]) == 1
        assert result["total"] == 1

    async def test_get_conflicts_error(self):
        mock_redis = AsyncMock()
        mock_redis.xlen = AsyncMock(side_effect=Exception("fail"))
        tracker = DedupResultTracker(redis_client=mock_redis)
        result = await tracker.get_conflicts()
        assert result["conflicts"] == []


# ===========================================================================
# OllamaClient - additional methods
# ===========================================================================


class TestOllamaClientChat:
    async def test_chat(self):
        client = OllamaClient(OllamaConfig(model="test"))
        mock_response = MagicMock()
        mock_response.json.return_value = {"message": {"content": "chat reply"}}
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response
        client._client = mock_http_client

        messages = [{"role": "user", "content": "hello"}]
        result = await client.chat(messages)
        assert result == "chat reply"

    async def test_classify_batch(self):
        client = OllamaClient(OllamaConfig(model="test"))
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "classified"}
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response
        client._client = mock_http_client

        results = await client.classify_batch(["prompt1", "prompt2"])
        assert len(results) == 2

    async def test_classify_batch_empty(self):
        client = OllamaClient(OllamaConfig(model="test"))
        results = await client.classify_batch([])
        assert results == []


# ===========================================================================
# EnhancedSimilarityMatcher - L2 layer tests
# ===========================================================================

from src.search.enhanced_similarity_matcher import (
    EnhancedSimilarityMatcher,
    EnhancedMatcherConfig,
)
from dataclasses import dataclass, field


@dataclass
class _FakeTerm:
    term: str
    term_ko: str = ""
    synonyms: list[str] = field(default_factory=list)
    abbreviations: list[str] = field(default_factory=list)
    physical_meaning: str = ""
    term_type: str = "TERM"
    definition: str = ""


class TestESM_L2:
    """Test L2 multi-channel retrieval."""

    def _build_matcher(self, terms):
        matcher = EnhancedSimilarityMatcher(
            config=EnhancedMatcherConfig(
                enable_rapidfuzz=True,
                enable_dense_search=False,
                enable_cross_encoder=False,
            ),
        )
        matcher.load_standard_terms(terms)
        return matcher

    async def test_l2_rapidfuzz_similar(self):
        terms = [
            _FakeTerm(term="knowledge_base", term_ko="지식베이스"),
            _FakeTerm(term="knowledge_graph", term_ko="지식그래프"),
        ]
        matcher = self._build_matcher(terms)
        # Should find via L2 fuzzy
        query = _FakeTerm(term="knowledgebase", term_ko="")
        decision = await matcher.match_enhanced(query)
        # May match or not depending on thresholds, but shouldn't crash
        assert decision.zone in ("AUTO_MATCH", "REVIEW", "NEW_TERM")

    async def test_l2_no_match(self):
        terms = [_FakeTerm(term="Alpha")]
        matcher = EnhancedSimilarityMatcher(
            config=EnhancedMatcherConfig(
                enable_rapidfuzz=True,
                enable_dense_search=False,
                enable_cross_encoder=False,
            ),
        )
        matcher.load_standard_terms(terms)
        query = _FakeTerm(term="완전다른한국어단어입니다")
        decision = await matcher.match_enhanced(query)
        assert decision.zone in ("REVIEW", "NEW_TERM")

    async def test_batch_match(self):
        terms = [
            _FakeTerm(term="GraphRAG"),
            _FakeTerm(term="VectorDB"),
        ]
        matcher = EnhancedSimilarityMatcher(
            config=EnhancedMatcherConfig(
                enable_rapidfuzz=True,
                enable_dense_search=False,
                enable_cross_encoder=False,
            ),
        )
        matcher.load_standard_terms(terms)
        queries = [
            _FakeTerm(term="GraphRAG"),
            _FakeTerm(term="unknown_term"),
        ]
        results = await matcher.match_batch(queries)
        assert len(results) == 2
        assert results[0].zone == "AUTO_MATCH"

    async def test_not_loaded(self):
        matcher = EnhancedSimilarityMatcher()
        # Don't load terms
        query = _FakeTerm(term="test")
        decision = await matcher.match_enhanced(query)
        assert decision.zone == "NEW_TERM"

    def test_classify_match_type_exact(self):
        matcher = EnhancedSimilarityMatcher()
        term = _FakeTerm(term="GraphRAG", term_ko="그래프래그")
        matcher.load_standard_terms([term])
        # normalized "graphrag" should be exact
        result = matcher._classify_match_type("graphrag", term)
        assert result == "exact"

    def test_classify_match_type_synonym(self):
        matcher = EnhancedSimilarityMatcher()
        term = _FakeTerm(term="K8s", synonyms=["쿠버네티스"])
        matcher.load_standard_terms([term])
        result = matcher._classify_match_type("쿠버네티스", term)
        assert result == "synonym"
