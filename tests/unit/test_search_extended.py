"""Extended unit tests for search: term_similarity_matcher + neo4j_loader."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.search.term_similarity_matcher import (
    SimilarityMatchResult,
    TermSimilarityMatcher,
    _strip_particles,
    _tokenize,
    _PrecomputedStd,
)
from src.pipelines.neo4j_loader import (
    Neo4jConfig,
    Neo4jKnowledgeLoader,
    SAFE_IDENTIFIER_PATTERN,
    ALLOWED_NODE_TYPES,
    ALLOWED_RELATION_TYPES,
)


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ===========================================================================
# term_similarity_matcher
# ===========================================================================


class TestStripParticles:
    def test_strip_long_particle(self):
        # "데이터베이스에서" = 7 chars, "에서" = 2 chars, 7 > 2+2=4 -> strips
        assert _strip_particles("데이터베이스에서") == "데이터베이스"

    def test_strip_short_particle(self):
        # "데이터베이스를" = 7 chars, "를" = 1 char, 7 > 1+2=3 -> strips
        assert _strip_particles("데이터베이스를") == "데이터베이스"

    def test_no_particle(self):
        assert _strip_particles("서버") == "서버"

    def test_strip_multiple(self):
        result = _strip_particles("데이터베이스에서는")
        # Strips "는" first, then "에서"
        assert result == "데이터베이스"

    def test_too_short_to_strip(self):
        # "서버를" = 3 chars, "를" = 1 char, 3 > 1+2=3 is false -> no strip
        result = _strip_particles("서버를")
        assert result == "서버를"


class TestTokenize:
    def test_english(self):
        tokens = _tokenize("hello world")
        assert "hello" in tokens
        assert "world" in tokens

    def test_korean(self):
        tokens = _tokenize("서버 관리")
        assert "서버" in tokens
        assert "관리" in tokens

    def test_mixed(self):
        tokens = _tokenize("API 서버")
        assert "api" in tokens
        assert "서버" in tokens

    def test_hyphen_split(self):
        tokens = _tokenize("data-mart")
        assert "data" in tokens
        assert "mart" in tokens


class TestSimilarityMatchResult:
    def test_defaults(self):
        r = SimilarityMatchResult(is_matched=False)
        assert r.match_type == "none"
        assert r.similarity_score == 0.0

    def test_matched(self):
        r = SimilarityMatchResult(is_matched=True, match_type="exact", similarity_score=1.0)
        assert r.is_matched
        assert r.match_type == "exact"


# ---------------------------------------------------------------------------
# TermSimilarityMatcher
# ---------------------------------------------------------------------------

@dataclass
class _FakeTerm:
    term: str
    term_ko: str = ""


class TestTermSimilarityMatcher:
    def _make_matcher(self, terms=None):
        if terms is None:
            terms = [
                _FakeTerm("API Gateway", "API 게이트웨이"),
                _FakeTerm("Database", "데이터베이스"),
                _FakeTerm("Server", "서버"),
                _FakeTerm("Kubernetes", "쿠버네티스"),
                _FakeTerm("Load Balancer", "로드 밸런서"),
            ]
        matcher = TermSimilarityMatcher()
        matcher.load_standard_terms(terms)
        return matcher

    def test_load_twice_noop(self):
        matcher = self._make_matcher()
        count_before = len(matcher._all_standard)
        matcher.load_standard_terms([_FakeTerm("Extra")])
        assert len(matcher._all_standard) == count_before

    def test_exact_match(self):
        matcher = self._make_matcher()
        result = matcher.match("API Gateway")
        assert result.is_matched
        assert result.match_type == "exact"
        assert result.similarity_score == 1.0

    def test_exact_match_ko(self):
        matcher = self._make_matcher()
        result = matcher.match("데이터베이스")
        assert result.is_matched
        assert result.match_type == "exact"

    def test_particle_stripped_match(self):
        matcher = self._make_matcher()
        # "데이터베이스에서" strips "에서" -> "데이터베이스" which matches
        result = matcher.match("데이터베이스에서")
        assert result.is_matched
        assert result.match_type in ("exact", "particle_stripped")

    def test_no_match(self):
        matcher = self._make_matcher()
        result = matcher.match("XYZ_UNRELATED_TERM_123")
        assert result.is_matched is False

    def test_empty_candidate(self):
        matcher = self._make_matcher()
        result = matcher.match("")
        assert result.is_matched is False

    def test_match_unloaded(self):
        matcher = TermSimilarityMatcher()
        result = matcher.match("test")
        assert result.is_matched is False

    def test_jaccard_from_sets(self):
        assert TermSimilarityMatcher._jaccard_from_sets(set(), set()) == 1.0
        assert TermSimilarityMatcher._jaccard_from_sets({"a"}, set()) == 0.0
        assert TermSimilarityMatcher._jaccard_from_sets({"a", "b"}, {"a", "b"}) == 1.0
        assert TermSimilarityMatcher._jaccard_from_sets({"a"}, {"b"}) == 0.0

    def test_get_candidates_empty(self):
        matcher = self._make_matcher()
        result = matcher._get_candidates(set())
        assert result == []

    def test_filter_terms(self):
        matcher = self._make_matcher()
        candidates = [
            _FakeTerm("API Gateway"),  # exact match
            _FakeTerm("NewTerm"),  # no match
            _FakeTerm("Database"),  # exact match
        ]
        new_terms, matched_terms = matcher.filter_terms(candidates)
        assert len(matched_terms) == 2
        assert len(new_terms) == 1
        assert new_terms[0].term == "NewTerm"

    def test_filter_terms_ko_match(self):
        matcher = self._make_matcher()
        candidates = [_FakeTerm("SomeEng", "서버")]
        new_terms, matched_terms = matcher.filter_terms(candidates)
        assert len(matched_terms) == 1


# ===========================================================================
# neo4j_loader
# ===========================================================================

class TestNeo4jConfig:
    def test_defaults(self):
        config = Neo4jConfig()
        assert config.uri == "bolt://localhost:7687"
        assert config.user == "neo4j"
        assert config.database == "neo4j"

    def test_custom(self):
        config = Neo4jConfig(uri="bolt://custom:7687", password="secret")
        assert config.uri == "bolt://custom:7687"


class TestSafeIdentifier:
    def test_valid(self):
        assert SAFE_IDENTIFIER_PATTERN.match("Person")
        assert SAFE_IDENTIFIER_PATTERN.match("RELATED_TO")

    def test_invalid(self):
        assert not SAFE_IDENTIFIER_PATTERN.match("123Invalid")
        assert not SAFE_IDENTIFIER_PATTERN.match("name; DROP TABLE")


class TestNeo4jKnowledgeLoader:
    def test_init(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)
        assert loader._driver is None

    def test_connect_no_driver(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)
        with patch("neo4j.AsyncGraphDatabase") as mock_gdb:
            mock_driver = AsyncMock()
            mock_gdb.driver = MagicMock(return_value=mock_driver)
            mock_session = AsyncMock()
            mock_driver.session = MagicMock(return_value=mock_session)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.run = AsyncMock()

            _run(loader.connect())
            assert loader._driver is not None

    def test_connect_import_error(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)
        with patch.dict("sys.modules", {"neo4j": None}):
            with patch("builtins.__import__", side_effect=ImportError):
                _run(loader.connect())
                assert loader._driver is None

    def test_close(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)
        loader._driver = AsyncMock()
        _run(loader.close())
        loader._driver.close.assert_called_once()

    def test_load_graph_no_driver(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)
        with patch.object(loader, "connect", new_callable=AsyncMock):
            result = _run(loader.load_graph({"nodes": [], "edges": []}))
            assert result == 0

    def test_load_graph_with_nodes(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)

        mock_session = AsyncMock()
        mock_session.run = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_driver = MagicMock()
        mock_driver.session = MagicMock(return_value=mock_session)
        loader._driver = mock_driver

        graph = {
            "nodes": [
                {"node_id": "n1", "node_type": "Person", "title": "Alice", "properties": {}},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation": "MEMBER_OF", "properties": {}},
            ],
        }
        result = _run(loader.load_graph(graph))
        assert result == 2  # 1 node + 1 edge

    def test_sanitize_label_whitelisted(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)
        assert loader._sanitize_label("Person", ALLOWED_NODE_TYPES, "Entity") == "Person"
        assert loader._sanitize_label("person", ALLOWED_NODE_TYPES, "Entity") == "Person"

    def test_sanitize_label_not_whitelisted(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)
        assert loader._sanitize_label("Hacker; DROP", ALLOWED_NODE_TYPES, "Entity") == "Entity"

    def test_sanitize_label_empty(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)
        assert loader._sanitize_label("", ALLOWED_NODE_TYPES, "Entity") == "Entity"

    def test_sanitize_relation(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)
        assert loader._sanitize_label("MEMBER_OF", ALLOWED_RELATION_TYPES, "RELATED_TO") == "MEMBER_OF"
        assert loader._sanitize_label("INJECT_ME", ALLOWED_RELATION_TYPES, "RELATED_TO") == "RELATED_TO"

    def test_load_nodes_batch_no_driver(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)
        with patch.object(loader, "connect", new_callable=AsyncMock):
            result = _run(loader.load_nodes_batch([{"node_id": "n1"}]))
            assert result == 0

    def test_load_nodes_batch_success(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)

        mock_session = AsyncMock()
        mock_session.run = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_driver = MagicMock()
        mock_driver.session = MagicMock(return_value=mock_session)
        loader._driver = mock_driver

        nodes = [
            {"node_id": "n1", "node_type": "Person", "title": "Alice", "properties": {}},
            {"node_id": "n2", "node_type": "System", "title": "Server", "properties": {}},
        ]
        result = _run(loader.load_nodes_batch(nodes, batch_size=10))
        assert result == 2

    def test_load_edges_batch_no_driver(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)
        with patch.object(loader, "connect", new_callable=AsyncMock):
            result = _run(loader.load_edges_batch([{"source": "s", "target": "t"}]))
            assert result == 0

    def test_load_edges_batch_success(self):
        config = Neo4jConfig()
        loader = Neo4jKnowledgeLoader(config)

        mock_session = AsyncMock()
        mock_session.run = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_driver = MagicMock()
        mock_driver.session = MagicMock(return_value=mock_session)
        loader._driver = mock_driver

        edges = [
            {"source": "n1", "target": "n2", "relation": "MEMBER_OF", "properties": {}},
        ]
        result = _run(loader.load_edges_batch(edges, batch_size=10))
        assert result == 1
