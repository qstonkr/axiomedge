"""Coverage backfill — CompositeReranker helper methods.

Tests entity_bonus, keyword_bonus, graph_distance_bonus, section_bonus,
and edge cases not covered by existing test_composite_reranker.py.
"""

from src.search.composite_reranker import CompositeReranker
from src.core.models import SearchChunk


def _make_chunk(
    chunk_id: str = "c1",
    content: str = "test content",
    score: float = 0.5,
    metadata: dict | None = None,
) -> SearchChunk:
    return SearchChunk(
        chunk_id=chunk_id,
        content=content,
        score=score,
        kb_id="test-kb",
        kb_name="Test KB",
        metadata=metadata or {},
    )


class TestEntityBonus:
    """Tests for _compute_entity_bonus (store/person name matching)."""

    def test_store_name_match(self) -> None:
        chunk = _make_chunk(metadata={"document_name": "강남점 운영 가이드"})
        bonus = CompositeReranker._compute_entity_bonus("강남점 매출", chunk)
        assert bonus == 0.12

    def test_store_name_no_match(self) -> None:
        chunk = _make_chunk(metadata={"document_name": "서울본사 가이드"})
        bonus = CompositeReranker._compute_entity_bonus("강남점 매출", chunk)
        assert bonus == 0.0

    def test_empty_query(self) -> None:
        chunk = _make_chunk(metadata={"document_name": "강남점 가이드"})
        assert CompositeReranker._compute_entity_bonus("", chunk) == 0.0

    def test_no_document_name(self) -> None:
        chunk = _make_chunk(metadata={})
        assert CompositeReranker._compute_entity_bonus("강남점", chunk) == 0.0

    def test_none_document_name(self) -> None:
        chunk = _make_chunk(metadata={"document_name": None})
        assert CompositeReranker._compute_entity_bonus("강남점", chunk) == 0.0


class TestKeywordBonus:
    """Tests for _compute_keyword_bonus (exact token presence)."""

    def test_all_tokens_match(self) -> None:
        chunk = _make_chunk(content="kubernetes pod restart guide")
        bonus = CompositeReranker._compute_keyword_bonus("pod restart", chunk)
        assert abs(bonus - 0.1) < 0.01  # All 2 tokens match → 0.1

    def test_partial_match(self) -> None:
        chunk = _make_chunk(content="kubernetes pod guide")
        bonus = CompositeReranker._compute_keyword_bonus("pod restart", chunk)
        assert 0.04 < bonus < 0.06  # 1/2 match → 0.05

    def test_no_match(self) -> None:
        chunk = _make_chunk(content="database backup")
        bonus = CompositeReranker._compute_keyword_bonus("pod restart", chunk)
        assert bonus == 0.0

    def test_empty_query(self) -> None:
        chunk = _make_chunk(content="something")
        assert CompositeReranker._compute_keyword_bonus("", chunk) == 0.0

    def test_short_tokens_ignored(self) -> None:
        """Tokens < 2 chars should be ignored."""
        chunk = _make_chunk(content="a b c test")
        bonus = CompositeReranker._compute_keyword_bonus("a b test", chunk)
        # Only "test" qualifies (≥2 chars), and it matches
        assert abs(bonus - 0.1) < 0.01


class TestGraphDistanceBonus:
    """Tests for _compute_graph_distance_bonus."""

    def test_no_graph_distance(self) -> None:
        reranker = CompositeReranker()
        chunk = _make_chunk(metadata={})
        assert reranker._compute_graph_distance_bonus(chunk) == 0.0

    def test_distance_one(self) -> None:
        reranker = CompositeReranker()
        chunk = _make_chunk(metadata={"graph_distance": 1})
        bonus = reranker._compute_graph_distance_bonus(chunk)
        # Distance 1 → graph_score = 1.0 / (1 + 0) = 1.0
        assert bonus > 0.0

    def test_distance_increases_decay(self) -> None:
        reranker = CompositeReranker()
        c1 = _make_chunk(metadata={"graph_distance": 1})
        c2 = _make_chunk(metadata={"graph_distance": 3})
        b1 = reranker._compute_graph_distance_bonus(c1)
        b2 = reranker._compute_graph_distance_bonus(c2)
        assert b1 > b2  # Closer = higher bonus

    def test_invalid_distance(self) -> None:
        reranker = CompositeReranker()
        chunk = _make_chunk(metadata={"graph_distance": "not_a_number"})
        assert reranker._compute_graph_distance_bonus(chunk) == 0.0

    def test_zero_distance(self) -> None:
        reranker = CompositeReranker()
        chunk = _make_chunk(metadata={"graph_distance": 0})
        assert reranker._compute_graph_distance_bonus(chunk) == 0.0

    def test_graph_weight_zero_disables(self) -> None:
        reranker = CompositeReranker()
        reranker._graph_distance_weight = 0.0
        chunk = _make_chunk(metadata={"graph_distance": 1})
        assert reranker._compute_graph_distance_bonus(chunk) == 0.0


class TestSourceContribution:
    """Tests for _compute_source_contribution with various source types."""

    def test_faq_gets_boosted(self) -> None:
        reranker = CompositeReranker(source_weight=0.1, faq_boost=1.5)
        faq = _make_chunk(metadata={"source_type": "faq"})
        regular = _make_chunk(metadata={"source_type": "qdrant"})
        faq_score = reranker._compute_source_contribution(faq, {})
        reg_score = reranker._compute_source_contribution(regular, {})
        assert faq_score > reg_score

    def test_missing_source_type_defaults(self) -> None:
        reranker = CompositeReranker(source_weight=0.1)
        chunk = _make_chunk(metadata={})
        score = reranker._compute_source_contribution(chunk, {})
        assert score >= 0.0

    def test_custom_source_weights(self) -> None:
        reranker = CompositeReranker(source_weight=0.1)
        chunk = _make_chunk(metadata={"source_type": "web"})
        s1 = reranker._compute_source_contribution(chunk, {"web": 0.5})
        s2 = reranker._compute_source_contribution(chunk, {"web": 2.0})
        assert s2 > s1

    def test_knowledge_type_fallback(self) -> None:
        """When source_type is missing, knowledge_type should be used."""
        reranker = CompositeReranker(source_weight=0.1, faq_boost=1.5)
        chunk = _make_chunk(metadata={"knowledge_type": "faq"})
        score = reranker._compute_source_contribution(chunk, {})
        assert score > 0.0


class TestPositionDecay:
    """Tests for position-based score decay."""

    def test_first_position_gets_full_bonus(self) -> None:
        reranker = CompositeReranker(
            model_weight=0.0, base_weight=0.0,
            source_weight=0.0, position_weight=0.1,
            mmr_enabled=False,
        )
        chunks = [_make_chunk(f"c{i}", f"content{i}", 0.5) for i in range(5)]
        result = reranker.rerank("q", chunks, top_k=5)
        # First chunk should have higher score than last due to position decay
        assert result[0].score > result[-1].score

    def test_position_weight_zero_no_effect(self) -> None:
        reranker = CompositeReranker(
            model_weight=0.0, base_weight=1.0,
            source_weight=0.0, position_weight=0.0,
            mmr_enabled=False,
        )
        chunks = [_make_chunk("c1", "a", 0.5), _make_chunk("c2", "b", 0.5)]
        result = reranker.rerank("q", chunks, top_k=2)
        # Equal base scores, no position weight → equal final scores
        assert abs(result[0].score - result[1].score) < 0.05


class TestRerankerEdgeCases:
    """Edge cases for the full rerank pipeline."""

    def test_empty_metadata(self) -> None:
        """Chunks with empty metadata should not crash."""
        chunk = _make_chunk(metadata={})
        reranker = CompositeReranker(mmr_enabled=False)
        result = reranker.rerank("query", [chunk], top_k=1)
        assert len(result) == 1

    def test_empty_content(self) -> None:
        chunk = _make_chunk(content="")
        reranker = CompositeReranker(mmr_enabled=False)
        result = reranker.rerank("query", [chunk], top_k=1)
        assert len(result) == 1

    def test_very_large_base_score(self) -> None:
        """Score > 1.0 input should still produce clamped output."""
        chunk = _make_chunk(score=100.0)
        reranker = CompositeReranker(mmr_enabled=False)
        result = reranker.rerank("q", [chunk], top_k=1)
        assert result[0].score <= 1.0
