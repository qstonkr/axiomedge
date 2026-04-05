"""Unit tests for GraphSearchExpander."""

from __future__ import annotations

import pytest

from src.search.graph_expander import (
    GraphSearchExpander,
    ExpandedSearchResult,
    NoOpGraphSearchExpander,
    _split_compound_words,
)


# ---------------------------------------------------------------------------
# Fake graph repository
# ---------------------------------------------------------------------------

class FakeGraphRepo:
    """Fake IGraphRepository for testing."""

    def __init__(
        self,
        related_chunks: set[str] | None = None,
        entities: list[dict] | None = None,
    ):
        self._related = related_chunks or set()
        self._entities = entities or []
        self.find_related_calls: list[dict] = []
        self.search_entities_calls: list[dict] = []

    async def find_related_chunks(
        self,
        entity_names: list[str],
        *,
        max_hops: int = 2,
        max_results: int = 50,
        scope_kb_ids: list[str] | None = None,
    ) -> set[str]:
        self.find_related_calls.append({
            "entity_names": entity_names,
            "max_hops": max_hops,
            "scope_kb_ids": scope_kb_ids,
        })
        return set(self._related)

    async def search_entities(
        self,
        keywords: list[str],
        *,
        max_facts: int = 20,
    ) -> list[dict]:
        self.search_entities_calls.append({"keywords": keywords})
        return list(self._entities)


class FailingGraphRepo(FakeGraphRepo):
    """Graph repo that raises on find_related_chunks."""

    async def find_related_chunks(self, *args, **kwargs) -> set[str]:
        raise RuntimeError("Neo4j unavailable")


# ---------------------------------------------------------------------------
# _split_compound_words
# ---------------------------------------------------------------------------

class TestSplitCompoundWords:
    def test_korean_english_boundary(self) -> None:
        result = _split_compound_words("K8S담당자")
        assert result == ["K8S", "담당자"]

    def test_mixed_slash(self) -> None:
        result = _split_compound_words("CI/CD 파이프라인 관리자")
        assert "CI" in result
        assert "CD" in result
        assert "파이프라인" in result
        assert "관리자" in result

    def test_pure_korean(self) -> None:
        result = _split_compound_words("장애처리 담당자")
        assert "장애처리" in result
        assert "담당자" in result

    def test_single_char_kept_by_regex(self) -> None:
        # Single ascii char matches [a-zA-Z0-9]+ pattern
        result = _split_compound_words("a")
        assert result == ["a"]

    def test_empty_query(self) -> None:
        result = _split_compound_words("")
        assert result == []


# ---------------------------------------------------------------------------
# GraphSearchExpander.expand
# ---------------------------------------------------------------------------

class TestGraphSearchExpander:
    @pytest.fixture
    def repo(self) -> FakeGraphRepo:
        return FakeGraphRepo(related_chunks={"doc-new-1", "doc-new-2"})

    @pytest.fixture
    def expander(self, repo: FakeGraphRepo) -> GraphSearchExpander:
        return GraphSearchExpander(graph_repo=repo, max_hops=2, max_expansion=20, graph_boost=0.1)

    @pytest.mark.asyncio
    async def test_expand_empty_chunks(self, expander: GraphSearchExpander) -> None:
        result = await expander.expand("K8S 장애", [])
        assert result.original_chunks == []
        assert result.expanded_source_uris == set()
        assert result.graph_related_count == 0

    @pytest.mark.asyncio
    async def test_expand_no_entity_tokens(self, expander: GraphSearchExpander) -> None:
        """Query with only single-char tokens yields no expansion."""
        result = await expander.expand("a b c", [{"source_uri": "x", "score": 0.5}])
        assert result.graph_related_count == 0

    @pytest.mark.asyncio
    async def test_expand_returns_new_uris(self, expander: GraphSearchExpander) -> None:
        chunks = [{"source_uri": "existing-doc", "score": 0.8}]
        result = await expander.expand("K8S 장애처리", chunks)
        assert "doc-new-1" in result.expanded_source_uris
        assert "doc-new-2" in result.expanded_source_uris
        assert result.graph_related_count >= 2

    @pytest.mark.asyncio
    async def test_expand_excludes_existing_uris(self) -> None:
        repo = FakeGraphRepo(related_chunks={"already-here"})
        expander = GraphSearchExpander(graph_repo=repo, graph_boost=0.1)
        chunks = [{"source_uri": "already-here", "score": 0.8}]
        result = await expander.expand("장애처리 담당", chunks)
        # "already-here" is already in chunks, so should not appear as new
        assert "already-here" not in result.expanded_source_uris

    @pytest.mark.asyncio
    async def test_expand_handles_graph_failure(self) -> None:
        repo = FailingGraphRepo()
        expander = GraphSearchExpander(graph_repo=repo, graph_boost=0.1)
        chunks = [{"source_uri": "doc1", "score": 0.9}]
        result = await expander.expand("K8S 장애", chunks)
        # Should not raise, returns empty expansion
        assert result.graph_related_count == 0


# ---------------------------------------------------------------------------
# boost_chunks
# ---------------------------------------------------------------------------

class TestBoostChunks:
    def setup_method(self) -> None:
        self.repo = FakeGraphRepo()
        self.expander = GraphSearchExpander(graph_repo=self.repo, graph_boost=0.1)

    def test_boost_matched_uri(self) -> None:
        chunks = [
            {"source_uri": "doc-a", "score": 0.5},
            {"source_uri": "doc-b", "score": 0.5},
        ]
        boosted = self.expander.boost_chunks(chunks, {"doc-a"})
        a = [c for c in boosted if c["source_uri"] == "doc-a"][0]
        b = [c for c in boosted if c["source_uri"] == "doc-b"][0]
        assert a["score"] > b["score"]
        assert a["graph_boosted"] is True
        assert "graph_boosted" not in b

    def test_boost_empty_uris(self) -> None:
        chunks = [{"source_uri": "doc-a", "score": 0.5}]
        result = self.expander.boost_chunks(chunks, set())
        assert result[0]["score"] == 0.5

    def test_boost_distance_1_higher_than_distance_3(self) -> None:
        chunks = [
            {"source_uri": "close", "score": 0.5},
            {"source_uri": "far", "score": 0.5},
        ]
        distances = {"close": 1, "far": 3}
        boosted = self.expander.boost_chunks(chunks, {"close", "far"}, distances)
        close_c = [c for c in boosted if c["source_uri"] == "close"][0]
        far_c = [c for c in boosted if c["source_uri"] == "far"][0]
        assert close_c["score"] > far_c["score"]

    def test_boost_by_document_name(self) -> None:
        chunks = [{"document_name": "my-doc", "score": 0.5}]
        boosted = self.expander.boost_chunks(chunks, {"my-doc"})
        assert boosted[0]["graph_boosted"] is True


# ---------------------------------------------------------------------------
# _extract_date_tokens
# ---------------------------------------------------------------------------

class TestExtractDateTokens:
    def test_year_month(self) -> None:
        tokens = GraphSearchExpander._extract_date_tokens("2024년 4월 보고서")
        assert "2024_04" in tokens
        assert "2024" in tokens
        assert "4월" in tokens

    def test_month_week(self) -> None:
        tokens = GraphSearchExpander._extract_date_tokens("4월 2주차 실적")
        assert "4월" in tokens

    def test_underscore_date(self) -> None:
        tokens = GraphSearchExpander._extract_date_tokens("report_2024_04_summary")
        assert "2024_04" in tokens

    def test_no_date(self) -> None:
        tokens = GraphSearchExpander._extract_date_tokens("장애 처리 절차")
        assert tokens == []


# ---------------------------------------------------------------------------
# NoOpGraphSearchExpander
# ---------------------------------------------------------------------------

class TestNoOpExpander:
    @pytest.mark.asyncio
    async def test_noop_expand(self) -> None:
        expander = NoOpGraphSearchExpander()
        chunks = [{"source_uri": "x", "score": 0.5}]
        result = await expander.expand("query", chunks)
        assert result.original_chunks == chunks
        assert result.graph_related_count == 0

    def test_noop_boost(self) -> None:
        expander = NoOpGraphSearchExpander()
        chunks = [{"source_uri": "x", "score": 0.5}]
        result = expander.boost_chunks(chunks, {"x"})
        assert result[0]["score"] == 0.5
