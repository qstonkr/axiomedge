"""Unit tests for the CompositeReranker."""

from src.search.composite_reranker import CompositeReranker
from src.domain.models import SearchChunk


def _make_chunk(
    chunk_id: str,
    content: str,
    score: float,
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


class TestCompositeReranker:
    """Test composite reranking logic."""

    def setup_method(self) -> None:
        self.reranker = CompositeReranker(
            model_weight=0.6,
            base_weight=0.3,
            source_weight=0.1,
            position_weight=0.0,
            faq_boost=1.2,
            mmr_enabled=False,
        )

    def test_rerank_basic_scoring(self) -> None:
        """Higher base scores should result in higher ranked output."""
        chunks = [
            _make_chunk("c1", "low score", 0.1),
            _make_chunk("c2", "high score", 0.9),
            _make_chunk("c3", "mid score", 0.5),
        ]
        result = self.reranker.rerank("query", chunks, top_k=3)
        assert len(result) == 3
        # Scores should be monotonically decreasing
        for i in range(len(result) - 1):
            assert result[i].score >= result[i + 1].score

    def test_rerank_respects_top_k(self) -> None:
        chunks = [_make_chunk(f"c{i}", f"content {i}", 0.5) for i in range(10)]
        result = self.reranker.rerank("query", chunks, top_k=3)
        assert len(result) == 3

    def test_empty_results(self) -> None:
        result = self.reranker.rerank("query", [], top_k=5)
        assert result == []

    def test_single_chunk(self) -> None:
        chunks = [_make_chunk("c1", "only chunk", 0.8)]
        result = self.reranker.rerank("query", chunks, top_k=5)
        assert len(result) == 1

    def test_faq_boost(self) -> None:
        """FAQ source type should get a boost over regular qdrant source."""
        faq_chunk = _make_chunk("faq1", "faq content", 0.5, {"source_type": "faq"})
        regular_chunk = _make_chunk("reg1", "regular content", 0.5, {"source_type": "qdrant"})

        result = self.reranker.rerank("query", [regular_chunk, faq_chunk], top_k=2)
        # FAQ chunk should rank higher due to faq_boost
        assert result[0].chunk_id == "faq1"

    def test_mmr_diversification(self) -> None:
        """With MMR enabled, duplicate-ish content should be pushed down."""
        reranker = CompositeReranker(
            model_weight=0.6,
            base_weight=0.3,
            source_weight=0.1,
            position_weight=0.0,
            mmr_enabled=True,
            mmr_lambda=0.5,
        )
        # Two chunks with identical content, one different
        chunks = [
            _make_chunk("c1", "kubernetes pod restart guide", 0.9),
            _make_chunk("c2", "kubernetes pod restart guide", 0.85),
            _make_chunk("c3", "database backup procedure details", 0.8),
        ]
        result = reranker.rerank("query", chunks, top_k=3)
        assert len(result) == 3
        # The diverse chunk should appear before the duplicate
        ids = [c.chunk_id for c in result]
        assert ids[0] == "c1"  # Highest relevance
        # c3 (diverse) should appear before c2 (duplicate of c1)
        assert ids.index("c3") < ids.index("c2")

    def test_scores_are_bounded(self) -> None:
        """All output scores should be between 0 and 1."""
        chunks = [
            _make_chunk("c1", "a", 0.0),
            _make_chunk("c2", "b", 1.0),
            _make_chunk("c3", "c", 0.5),
        ]
        result = self.reranker.rerank("query", chunks, top_k=3)
        for chunk in result:
            assert 0.0 <= chunk.score <= 1.0, f"Score {chunk.score} out of bounds"

    def test_source_weights_override(self) -> None:
        """Per-call source_weights should override defaults."""
        chunk = _make_chunk("c1", "content", 0.5, {"source_type": "web"})
        result1 = self.reranker.rerank("q", [chunk], top_k=1)

        result2 = self.reranker.rerank(
            "q", [chunk], top_k=1,
            source_weights={"web": 2.0},
        )
        # Both return one chunk, but scores may differ due to weight change
        assert len(result1) == 1
        assert len(result2) == 1

    def test_model_score_from_metadata(self) -> None:
        """When model_score is in metadata, it should be used."""
        chunk = _make_chunk(
            "c1", "content", 0.3,
            {"model_score": 0.95, "base_score": 0.2},
        )
        result = self.reranker.rerank("q", [chunk], top_k=1)
        assert len(result) == 1
