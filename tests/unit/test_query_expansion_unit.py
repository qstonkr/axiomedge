"""Unit tests for src/search/query_expansion.py (no external services)."""

import asyncio

import pytest

from src.search.query_expansion import (
    ExpandedQuery,
    NoOpGlossaryRepository,
    QueryExpansionDecision,
    QueryExpansionService,
    SearchQueryExpander,
    TermExpansion,
)


class TestExpandedQuery:
    """Test ExpandedQuery dataclass."""

    def test_basic_fields(self) -> None:
        eq = ExpandedQuery(original_query="K8S", expanded_query="K8S OR Kubernetes")
        assert eq.original_query == "K8S"
        assert eq.expanded_query == "K8S OR Kubernetes"
        assert eq.expansion_terms == []
        assert eq.matched_glossary_ids == []

    def test_to_dict(self) -> None:
        eq = ExpandedQuery(
            original_query="VPN",
            expanded_query="VPN OR 가상사설망",
            expansion_terms=["VPN", "가상사설망"],
            matched_glossary_ids=["g1"],
        )
        d = eq.to_dict()
        assert d["original_query"] == "VPN"
        assert "가상사설망" in d["expansion_terms"]
        assert d["matched_glossary_ids"] == ["g1"]


class TestTermExpansion:
    """Test TermExpansion dataclass."""

    def test_defaults(self) -> None:
        te = TermExpansion(original_term="test", expanded_terms=["test"])
        assert te.glossary_term_id is None
        assert te.source == "glossary"


class TestQueryExpansionDecision:
    """Test QueryExpansionDecision frozen dataclass."""

    def test_was_expanded_true(self) -> None:
        d = QueryExpansionDecision(
            original_query="K8S담당자",
            expanded_query="K8S 담당자",
            method="compound_split",
        )
        assert d.was_expanded is True

    def test_was_expanded_false(self) -> None:
        d = QueryExpansionDecision(
            original_query="hello",
            expanded_query="hello",
            method="none",
        )
        assert d.was_expanded is False

    def test_frozen(self) -> None:
        d = QueryExpansionDecision(original_query="a", expanded_query="b", method="x")
        with pytest.raises(AttributeError):
            d.original_query = "changed"  # type: ignore[misc]


class TestNoOpGlossaryRepository:
    """Test no-op glossary returns empty."""

    def test_search_returns_empty(self) -> None:
        repo = NoOpGlossaryRepository()
        result = asyncio.get_event_loop().run_until_complete(repo.search("kb", "term"))
        assert result == []


class TestQueryExpansionServiceTokenize:
    """Test tokenization without external services."""

    def setup_method(self) -> None:
        self.svc = QueryExpansionService()

    def test_tokenize_filters_stopwords(self) -> None:
        tokens = self.svc.tokenize("VPN에 대해 알려주세요")
        assert "대해" not in tokens
        assert "알려" not in tokens
        assert "VPN" in tokens

    def test_tokenize_english_stopwords(self) -> None:
        tokens = self.svc.tokenize("what is the VPN policy")
        assert "what" not in tokens
        assert "is" not in tokens
        assert "the" not in tokens
        assert "VPN" in tokens
        assert "policy" in tokens

    def test_tokenize_empty(self) -> None:
        assert self.svc.tokenize("") == []


class TestQueryExpansionServiceNoGlossary:
    """Test expand_query with NoOp glossary (returns original terms)."""

    def setup_method(self) -> None:
        self.svc = QueryExpansionService(
            glossary_repository=NoOpGlossaryRepository(),
            enable_semantic_fallback=False,
        )

    def test_expand_returns_original_terms(self) -> None:
        result = asyncio.get_event_loop().run_until_complete(
            self.svc.expand_query("test-kb", "VPN 접속")
        )
        assert result.original_query == "VPN 접속"
        # With no glossary matches and no semantic fallback, each token maps to itself
        assert "VPN" in result.expanded_query
        assert "접속" in result.expanded_query

    def test_expand_empty_query(self) -> None:
        result = asyncio.get_event_loop().run_until_complete(
            self.svc.expand_query("kb", "")
        )
        assert result.expanded_query == ""


class TestSearchQueryExpander:
    """Test SearchQueryExpander orchestrator."""

    def test_no_expansion_service_compound_split(self) -> None:
        expander = SearchQueryExpander(query_expansion_service=None)
        result = asyncio.get_event_loop().run_until_complete(
            expander.expand_query("K8S담당자")
        )
        # Should compound-split at English/Hangul boundary
        assert result == "K8S 담당자"

    def test_no_expansion_no_split_needed(self) -> None:
        expander = SearchQueryExpander(query_expansion_service=None)
        result = asyncio.get_event_loop().run_until_complete(
            expander.expand_query("순수 한글 질의")
        )
        # Pure Hangul with spaces: compound split produces same tokens
        # but spaces differ -> check it doesn't crash
        assert isinstance(result, str)

    def test_expand_with_metadata(self) -> None:
        expander = SearchQueryExpander(query_expansion_service=None)
        decision = asyncio.get_event_loop().run_until_complete(
            expander.expand_query_with_metadata("POS장애처리")
        )
        assert isinstance(decision, QueryExpansionDecision)
        assert decision.expanded_query == "POS 장애처리"
        assert decision.method == "compound_split"

    def test_recall_probe_disabled_by_default(self) -> None:
        expander = SearchQueryExpander(recall_probe_rate=0.0)
        decision = QueryExpansionDecision(
            original_query="a", expanded_query="b", method="glossary"
        )
        assert expander.should_run_recall_probe(
            user_id="u1",
            organization_id="o1",
            query="a",
            expansion_decision=decision,
        ) is False

    def test_recall_probe_not_expanded(self) -> None:
        expander = SearchQueryExpander(recall_probe_rate=1.0)
        decision = QueryExpansionDecision(
            original_query="same", expanded_query="same", method="none"
        )
        # Not expanded -> no probe
        assert expander.should_run_recall_probe(
            user_id="u1",
            organization_id="o1",
            query="same",
            expansion_decision=decision,
        ) is False

    def test_recall_probe_always_when_rate_1(self) -> None:
        expander = SearchQueryExpander(recall_probe_rate=1.0)
        decision = QueryExpansionDecision(
            original_query="a", expanded_query="b", method="glossary"
        )
        assert expander.should_run_recall_probe(
            user_id="u1",
            organization_id="o1",
            query="a",
            expansion_decision=decision,
        ) is True
