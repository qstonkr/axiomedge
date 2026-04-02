"""Additional unit tests to improve search module coverage:
query_expansion, dense_term_index, graph_expander, passage_cleaner, cross_encoder_reranker."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# QueryExpansionService
# ===========================================================================
class TestQueryExpansionService:
    def test_tokenize(self):
        from src.search.query_expansion import QueryExpansionService
        svc = QueryExpansionService()
        tokens = svc.tokenize("서버 관리 절차에 대해서")
        assert "서버" in tokens
        assert "관리" in tokens
        # "절차에" is a single Hangul block in regex
        assert any("절차" in t for t in tokens)
        # Stopwords removed
        assert "대해서" not in tokens

    def test_tokenize_english(self):
        from src.search.query_expansion import QueryExpansionService
        svc = QueryExpansionService()
        tokens = svc.tokenize("how to manage the server")
        assert "server" in tokens
        assert "manage" in tokens
        assert "the" not in tokens

    def test_expand_term_no_glossary_match(self):
        from src.search.query_expansion import QueryExpansionService, NoOpGlossaryRepository
        svc = QueryExpansionService(glossary_repository=NoOpGlossaryRepository())

        async def _go():
            result = await svc.expand_term("test-kb", "서버")
            assert result.original_term == "서버"
            assert result.source == "original"

        _run(_go())

    def test_expand_term_with_glossary_match_term_type(self):
        from src.search.query_expansion import QueryExpansionService
        repo = AsyncMock()
        repo.search = AsyncMock(return_value=[{
            "id": "g1", "term": "SRV", "term_ko": "서버", "term_type": "term",
            "synonyms": ["server", "srv"], "abbreviations": ["SVR"],
        }])
        svc = QueryExpansionService(glossary_repository=repo)

        async def _go():
            result = await svc.expand_term("kb1", "SRV")
            assert result.source == "glossary"
            assert "server" in result.expanded_terms
            assert "서버" in result.expanded_terms

        _run(_go())

    def test_expand_term_with_glossary_match_word_type(self):
        from src.search.query_expansion import QueryExpansionService
        repo = AsyncMock()
        repo.search = AsyncMock(return_value=[{
            "id": "g1", "term": "SRV", "term_ko": "서버", "term_type": "word",
            "synonyms": ["server"], "abbreviations": [],
        }])
        svc = QueryExpansionService(glossary_repository=repo)

        async def _go():
            result = await svc.expand_term("kb1", "SRV")
            assert result.source == "glossary"
            # Word type: only term + term_ko (no synonyms)
            assert "SRV" in result.expanded_terms
            assert "서버" in result.expanded_terms
            assert "server" not in result.expanded_terms

        _run(_go())

    def test_expand_term_with_decomposition(self):
        from src.search.query_expansion import QueryExpansionService
        decomp = MagicMock()
        decomp.is_loaded = True
        decomp.expand_for_query = MagicMock(return_value=["서버", "이름"])
        svc = QueryExpansionService(decomposition_service=decomp)

        async def _go():
            result = await svc.expand_term("kb1", "서버이름")
            assert result.source == "decomposition"
            assert len(result.expanded_terms) == 2

        _run(_go())

    def test_expand_term_semantic_fallback(self):
        from src.search.query_expansion import QueryExpansionService
        llm = AsyncMock()
        expanded_result = MagicMock()
        expanded_result.rewrite_query = "server management"
        expanded_result.preprocess_query = "서버 관리"
        expanded_result.variations = []
        llm.expand = AsyncMock(return_value=expanded_result)
        svc = QueryExpansionService(llm_expander=llm, enable_semantic_fallback=True)

        async def _go():
            result = await svc.expand_term("kb1", "서버관리")
            assert result.source == "semantic_fallback"
            assert len(result.expanded_terms) > 1

        _run(_go())

    def test_expand_term_semantic_fallback_failure(self):
        from src.search.query_expansion import QueryExpansionService
        llm = AsyncMock()
        llm.expand = AsyncMock(side_effect=RuntimeError("LLM error"))
        svc = QueryExpansionService(llm_expander=llm, enable_semantic_fallback=True)

        async def _go():
            result = await svc.expand_term("kb1", "test")
            assert result.source == "original"

        _run(_go())

    def test_expand_term_max_expansions(self):
        from src.search.query_expansion import QueryExpansionService
        repo = AsyncMock()
        repo.search = AsyncMock(return_value=[{
            "id": "g1", "term": "SRV", "term_ko": "서버", "term_type": "term",
            "synonyms": ["s1", "s2", "s3", "s4", "s5", "s6"],
            "abbreviations": ["a1", "a2"],
        }])
        svc = QueryExpansionService(glossary_repository=repo, max_expansions_per_term=3)

        async def _go():
            result = await svc.expand_term("kb1", "SRV")
            assert len(result.expanded_terms) <= 3

        _run(_go())


class TestExpandedQuery:
    def test_to_dict(self):
        from src.search.query_expansion import ExpandedQuery
        eq = ExpandedQuery(
            original_query="서버",
            expanded_query="서버 OR server",
            expansion_terms=["server"],
            matched_glossary_ids=["g1"],
        )
        d = eq.to_dict()
        assert d["original_query"] == "서버"
        assert d["expansion_terms"] == ["server"]


class TestQueryExpansionDecision:
    def test_was_expanded(self):
        from src.search.query_expansion import QueryExpansionDecision
        d1 = QueryExpansionDecision("hello", "hello OR world", "glossary")
        assert d1.was_expanded is True
        d2 = QueryExpansionDecision("hello", "hello", "original")
        assert d2.was_expanded is False


class TestNoOpGlossaryRepository:
    def test_search(self):
        from src.search.query_expansion import NoOpGlossaryRepository
        repo = NoOpGlossaryRepository()

        async def _go():
            result = await repo.search("kb1", "test")
            assert result == []

        _run(_go())


# ===========================================================================
# DenseTermIndex
# ===========================================================================
class TestDenseTermIndex:
    def test_not_ready_by_default(self):
        from src.search.dense_term_index import DenseTermIndex
        provider = MagicMock()
        idx = DenseTermIndex(provider)
        assert idx.is_ready is False

    def test_search_empty(self):
        from src.search.dense_term_index import DenseTermIndex
        provider = MagicMock()
        idx = DenseTermIndex(provider)
        assert idx.search("test") == []

    def test_search_batch_empty(self):
        from src.search.dense_term_index import DenseTermIndex
        provider = MagicMock()
        idx = DenseTermIndex(provider)
        assert idx.search_batch(["a", "b"]) == [[], []]

    def test_build_and_search(self):
        from src.search.dense_term_index import DenseTermIndex
        provider = MagicMock()
        provider.is_ready.return_value = True

        dim = 4  # small for testing
        vecs = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.5, 0.5, 0.0, 0.0]]
        provider.encode = MagicMock(return_value={"dense_vecs": vecs})

        # Mock precomputed terms
        terms = []
        for i, name in enumerate(["서버", "네트워크", "데이터"]):
            t = MagicMock()
            t.term = MagicMock()
            t.term.term = name
            t.term.term_ko = ""
            t.term.definition = ""
            terms.append(t)

        with patch("src.search.dense_term_index._w") as mock_w:
            mock_w.embedding.dimension = dim
            mock_w.search.term_build_batch_size = 100
            mock_w.search.term_search_top_k = 5
            idx = DenseTermIndex(provider)
            idx.build(terms)
            assert idx.is_ready is True

            # Search
            provider.encode = MagicMock(return_value={"dense_vecs": [[1.0, 0.0, 0.0, 0.0]]})
            results = idx.search("서버", top_k=2)
            assert len(results) <= 2
            assert all(isinstance(r, tuple) and len(r) == 2 for r in results)

    def test_build_provider_not_ready(self):
        from src.search.dense_term_index import DenseTermIndex
        provider = MagicMock()
        provider.is_ready.return_value = False
        idx = DenseTermIndex(provider)
        idx.build([])
        assert idx.is_ready is False

    def test_search_encode_error(self):
        from src.search.dense_term_index import DenseTermIndex
        provider = MagicMock()
        idx = DenseTermIndex(provider)
        # Manually set ready state
        idx._matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        idx._term_indices = [0, 1]
        provider.encode = MagicMock(side_effect=RuntimeError("err"))
        assert idx.search("test") == []


# ===========================================================================
# GraphSearchExpander
# ===========================================================================
class TestGraphSearchExpander:
    def test_expand_empty_chunks(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        expander = GraphSearchExpander(graph_repo=repo)

        async def _go():
            result = await expander.expand("query", [])
            assert result.graph_related_count == 0

        _run(_go())

    def test_expand_no_entity_names(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        expander = GraphSearchExpander(graph_repo=repo)

        async def _go():
            result = await expander.expand("a", [{"content": "test"}])
            assert result.graph_related_count == 0

        _run(_go())

    def test_expand_success(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        repo.find_related_chunks = AsyncMock(side_effect=[
            {"doc_b.pdf"},   # scoped
            {"doc_c.pdf"},   # cross-KB
        ])
        expander = GraphSearchExpander(graph_repo=repo)

        async def _go():
            chunks = [{"content": "test", "source_uri": "doc_a.pdf"}]
            result = await expander.expand("서버 관리", chunks, scope_kb_ids=["kb1"])
            assert "doc_b.pdf" in result.expanded_source_uris
            assert "doc_c.pdf" in result.expanded_source_uris
            assert result.graph_related_count == 2

        _run(_go())

    def test_expand_error(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        repo.find_related_chunks = AsyncMock(side_effect=RuntimeError("neo4j down"))
        expander = GraphSearchExpander(graph_repo=repo)

        async def _go():
            result = await expander.expand("서버 관리", [{"content": "test"}])
            assert result.graph_related_count == 0

        _run(_go())

    def test_boost_chunks(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        expander = GraphSearchExpander(graph_repo=repo, graph_boost=0.1)

        chunks = [
            {"source_uri": "a.pdf", "score": 0.5},
            {"source_uri": "b.pdf", "score": 0.4},
        ]
        boosted = expander.boost_chunks(chunks, {"a.pdf"}, graph_distances={"a.pdf": 1})
        assert boosted[0]["graph_boosted"] is True
        assert boosted[0]["score"] > 0.5
        # b.pdf not boosted
        assert "graph_boosted" not in boosted[1]

    def test_boost_chunks_empty(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        expander = GraphSearchExpander(graph_repo=repo)
        chunks = [{"source_uri": "a.pdf", "score": 0.5}]
        assert expander.boost_chunks(chunks, set()) == chunks

    def test_boost_distance_tiers(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        expander = GraphSearchExpander(graph_repo=repo, graph_boost=0.1)

        # Distance 1, 2, 3
        chunks = [
            {"source_uri": "d1.pdf", "score": 0.0},
            {"source_uri": "d2.pdf", "score": 0.0},
            {"source_uri": "d3.pdf", "score": 0.0},
        ]
        uris = {"d1.pdf", "d2.pdf", "d3.pdf"}
        distances = {"d1.pdf": 1, "d2.pdf": 2, "d3.pdf": 3}
        boosted = expander.boost_chunks(chunks, uris, graph_distances=distances)
        # d1 (1-hop) > d2 (2-hop) > d3 (3-hop)
        assert boosted[0]["score"] > boosted[1]["score"]
        assert boosted[1]["score"] > boosted[2]["score"]


class TestNoOpGraphSearchExpander:
    def test_expand(self):
        from src.search.graph_expander import NoOpGraphSearchExpander
        exp = NoOpGraphSearchExpander()

        async def _go():
            result = await exp.expand("query", [{"c": 1}])
            assert result.original_chunks == [{"c": 1}]
            assert result.graph_related_count == 0

        _run(_go())

    def test_boost_chunks(self):
        from src.search.graph_expander import NoOpGraphSearchExpander
        exp = NoOpGraphSearchExpander()
        chunks = [{"score": 0.5}]
        assert exp.boost_chunks(chunks, {"uri"}) == chunks


class TestSplitCompoundWords:
    def test_basic(self):
        from src.search.graph_expander import _split_compound_words
        assert _split_compound_words("K8S담당자") == ["K8S", "담당자"]
        assert _split_compound_words("POS장애처리") == ["POS", "장애처리"]

    def test_plain_text(self):
        from src.search.graph_expander import _split_compound_words
        tokens = _split_compound_words("서버 네트워크")
        assert "서버" in tokens
        assert "네트워크" in tokens


# ===========================================================================
# passage_cleaner
# ===========================================================================
class TestPassageCleaner:
    def test_clean_passage_whitespace(self):
        from src.search.passage_cleaner import clean_passage
        result = clean_passage("hello   world\n\n\n\nmore text here")
        assert "   " not in result
        assert "\n\n\n" not in result

    def test_clean_passage_dedup(self):
        from src.search.passage_cleaner import clean_passage
        result = clean_passage("hello world\nhello world\ndifferent line")
        assert result.count("hello world") == 1

    def test_clean_passage_short(self):
        from src.search.passage_cleaner import clean_passage
        assert clean_passage("hi") == "hi"

    def test_clean_passage_empty(self):
        from src.search.passage_cleaner import clean_passage
        assert clean_passage("") == ""
        assert clean_passage(None) is None

    def test_clean_chunks(self):
        from src.search.passage_cleaner import clean_chunks
        chunks = [
            {"content": "This is a valid passage with enough content.", "id": 1},
            {"content": "", "id": 2},
            {"content": "short", "id": 3},
        ]
        result = clean_chunks(chunks)
        assert len(result) == 1
        assert result[0]["id"] == 1


# ===========================================================================
# cross_encoder_reranker
# ===========================================================================
class TestCrossEncoderReranker:
    def test_sigmoid(self):
        from src.search.cross_encoder_reranker import _sigmoid
        assert 0 < _sigmoid(0) < 1
        assert _sigmoid(0) == pytest.approx(0.5, abs=0.01)
        assert _sigmoid(100) > 0.99
        assert _sigmoid(-100) < 0.01

    def test_rerank_no_model(self):
        from src.search.cross_encoder_reranker import rerank_with_cross_encoder
        chunks = [{"content": "a"}, {"content": "b"}]
        with patch("src.search.cross_encoder_reranker._model", None):
            result = rerank_with_cross_encoder("query", chunks, top_k=5)
            assert result == chunks

    def test_rerank_empty_chunks(self):
        from src.search.cross_encoder_reranker import rerank_with_cross_encoder
        with patch("src.search.cross_encoder_reranker._model", MagicMock()):
            result = rerank_with_cross_encoder("query", [])
            assert result == []

    def test_async_rerank_no_model(self):
        from src.search.cross_encoder_reranker import async_rerank_with_cross_encoder
        chunks = [{"content": "a"}]

        async def _go():
            with patch("src.search.cross_encoder_reranker._model", None):
                result = await async_rerank_with_cross_encoder("query", chunks)
                assert result == chunks

        _run(_go())

    def test_warmup_already_loaded(self):
        from src.search.cross_encoder_reranker import warmup
        with patch("src.search.cross_encoder_reranker._load_attempted", True):
            warmup()  # Should not submit new task

    def test_warmup_already_loading(self):
        from src.search.cross_encoder_reranker import warmup
        with patch("src.search.cross_encoder_reranker._load_attempted", False), \
             patch("src.search.cross_encoder_reranker._loading", True):
            warmup()  # Should not submit new task
