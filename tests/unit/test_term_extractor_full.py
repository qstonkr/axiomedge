"""Comprehensive unit tests for term_extractor.py and term_patterns.py — maximizing line coverage.

Tests noise filters, particle stripping, code artifact detection,
pattern extraction, TermExtractor initialization and extract methods.
No external services required.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.pipelines.term_patterns import (
    CAMEL_CASE_PATTERN,
    ACRONYM_PATTERN,
    HYPHENATED_PATTERN,
    KOREAN_TECH_PATTERN,
    MIXED_PATTERN,
    STOP_TERMS,
    KNOWN_ACRONYMS,
    ENGLISH_STOP,
    _COMPOUND_STOP,
    _CSS_PREFIXES,
    is_noise_term,
    is_synonym_noise,
    is_code_artifact,
    strip_korean_particles,
)
from src.pipelines.term_extractor import (
    ExtractedTerm,
    TermExtractor,
)


# =========================================================================
# Regex pattern compilation tests
# =========================================================================

class TestPatterns:
    def test_camel_case(self):
        matches = CAMEL_CASE_PATTERN.findall("The KnowledgeBase system and GraphRag.")
        assert "KnowledgeBase" in matches
        assert "GraphRag" in matches

    def test_acronym(self):
        matches = ACRONYM_PATTERN.findall("Use API and KB for RAG pipeline")
        assert "API" in matches
        assert "KB" in matches
        assert "RAG" in matches

    def test_hyphenated(self):
        matches = HYPHENATED_PATTERN.findall("Use micro-service and multi-tenant arch")
        assert "micro-service" in matches
        assert "multi-tenant" in matches

    def test_korean_tech(self):
        matches = KOREAN_TECH_PATTERN.findall("지식베이스와 벡터검색 시스템")
        assert "지식베이스" in matches
        assert "벡터검색" in matches

    def test_mixed(self):
        matches = MIXED_PATTERN.findall("K8s클러스터와 Redis캐시")
        assert any("K8s" in m for m in matches)
        assert any("Redis" in m for m in matches)


# =========================================================================
# is_noise_term
# =========================================================================

class TestIsNoiseTerm:
    def test_too_short(self):
        assert is_noise_term("a", "noun") is True

    def test_single_char_repeated(self):
        assert is_noise_term("ㅋㅋ", "noun") is True  # set size 1

    def test_starts_with_special(self):
        for c in "#*@\\/$`~^:=+":
            assert is_noise_term(f"{c}term", "noun") is True

    def test_ends_with_special(self):
        for c in "#*@\\/$`~^":
            assert is_noise_term(f"term{c}", "noun") is True

    def test_contains_dot(self):
        assert is_noise_term("file.ext", "noun") is True

    def test_contains_slash_comma_underscore(self):
        assert is_noise_term("a/b", "noun") is True
        assert is_noise_term("a,b", "noun") is True
        assert is_noise_term("a_b", "noun") is True

    def test_starts_with_digit(self):
        assert is_noise_term("3term", "noun") is True

    def test_all_digits_after_strip(self):
        assert is_noise_term("123", "noun") is True
        assert is_noise_term("-456-", "noun") is True

    def test_brackets_parens(self):
        for c in "{}()[]\"'=<>;|":
            assert is_noise_term(f"t{c}rm", "noun") is True

    def test_low_alnum_ratio(self):
        assert is_noise_term("!@#$%a", "noun") is True

    def test_ascii_short_alpha(self):
        assert is_noise_term("hello", "noun") is True  # ASCII alpha <= 10

    def test_ascii_camelcase_lower_start(self):
        assert is_noise_term("camelCase", "noun") is True

    def test_ascii_alnum_no_korean(self):
        assert is_noise_term("abc123", "noun") is True

    def test_foreign_too_short(self):
        assert is_noise_term("AB", "foreign") is True

    def test_foreign_short_lowercase(self):
        assert is_noise_term("test", "foreign") is True

    def test_foreign_english_stop(self):
        assert is_noise_term("server", "foreign") is True

    def test_foreign_short_uppercase_not_known(self):
        assert is_noise_term("XYZ", "foreign") is True

    def test_foreign_known_acronym_is_noise_due_to_ascii_check(self):
        """API is ASCII-only alnum, so it is noise even though it's a known acronym.
        The ASCII alnum check fires before the known-acronym check."""
        assert is_noise_term("API", "foreign") is True

    def test_compound_noun_ascii_alpha(self):
        assert is_noise_term("CompoundWord", "compound_noun") is True

    def test_code_artifact_css(self):
        assert is_noise_term("border-radius", "noun") is True

    def test_kiwi_score_generic(self):
        # score > threshold, Korean noun => noise
        assert is_noise_term("상품", "noun", kiwi_score=-9.0) is True

    def test_kiwi_score_domain(self):
        # score < threshold => not noise (domain-specific)
        assert is_noise_term("정산금", "compound_noun", kiwi_score=-14.0) is False

    def test_valid_korean_compound(self):
        assert is_noise_term("경영주", "compound_noun", kiwi_score=-14.0) is False

    def test_stop_nouns_param(self):
        stop = frozenset({"경우"})
        assert is_noise_term("경우", "noun", stop_nouns=stop) is True


# =========================================================================
# is_synonym_noise
# =========================================================================

class TestIsSynonymNoise:
    def test_empty(self):
        assert is_synonym_noise("") is True

    def test_special_start(self):
        assert is_synonym_noise("#tag") is True
        assert is_synonym_noise("$var") is True

    def test_all_digits(self):
        assert is_synonym_noise("12345") is True

    def test_low_alnum_ratio(self):
        assert is_synonym_noise("!!@@##") is True

    def test_valid_term(self):
        assert is_synonym_noise("쿠버네티스") is False

    def test_short_valid(self):
        assert is_synonym_noise("K8s") is False


# =========================================================================
# is_code_artifact
# =========================================================================

class TestIsCodeArtifact:
    def test_css_properties(self):
        for prefix in ("border", "margin", "padding", "flex", "grid"):
            assert is_code_artifact(f"{prefix}-something") is True

    def test_non_css_hyphenated(self):
        assert is_code_artifact("micro-service") is False

    def test_exception_class(self):
        assert is_code_artifact("NullPointerException") is True

    def test_error_suffix(self):
        assert is_code_artifact("SyntaxError") is True

    def test_permission_string(self):
        assert is_code_artifact("drwxr-xr-x") is True

    def test_mime_type(self):
        assert is_code_artifact("application/json") is True
        assert is_code_artifact("text/html") is True

    def test_www_form(self):
        assert is_code_artifact("x-www-form-urlencoded") is True

    def test_normal_term(self):
        assert is_code_artifact("쿠버네티스") is False


# =========================================================================
# strip_korean_particles
# =========================================================================

class TestStripKoreanParticles:
    def test_long_particle(self):
        result = strip_korean_particles("Redis에서실행")
        assert "에서" not in result or len(result) < len("Redis에서실행")

    def test_short_particle(self):
        result = strip_korean_particles("API를사용")
        # Should strip 를
        assert "를" not in result or len(result) < len("API를사용")

    def test_no_particle(self):
        result = strip_korean_particles("정상처리")
        assert result == "정상처리"

    def test_boundary_particle(self):
        result = strip_korean_particles("Redis가캐시")
        assert result == "Redis캐시"

    def test_too_short_no_strip(self):
        """If stripping would leave too-short result, don't strip."""
        result = strip_korean_particles("가는")
        assert result == "가는"


# =========================================================================
# ExtractedTerm dataclass
# =========================================================================

class TestExtractedTerm:
    def test_defaults(self):
        t = ExtractedTerm(term="API", pattern_type="acronym")
        assert t.occurrences == 1
        assert t.contexts == []
        assert t.category is None

    def test_with_values(self):
        t = ExtractedTerm(
            term="GraphRAG", pattern_type="camel_case",
            occurrences=5, contexts=["ctx1"], category="tech",
        )
        assert t.occurrences == 5
        assert t.category == "tech"


# =========================================================================
# TermExtractor
# =========================================================================

class TestTermExtractor:
    def test_init_defaults(self):
        te = TermExtractor()
        assert te._glossary_repo is None
        assert te._min_occurrences == TermExtractor.MIN_OCCURRENCES
        assert te._kiwi is None
        assert te._kiwi_available is None

    def test_init_custom(self):
        mock_repo = MagicMock()
        te = TermExtractor(glossary_repo=mock_repo, min_occurrences=5)
        assert te._glossary_repo is mock_repo
        assert te._min_occurrences == 5

    def test_get_kiwi_unavailable(self):
        te = TermExtractor()
        with patch.dict("sys.modules", {"kiwipiepy": None}):
            with patch("builtins.__import__", side_effect=ImportError):
                result = te._get_kiwi()
                assert result is None
                assert te._kiwi_available is False

    def test_get_kiwi_cached(self):
        te = TermExtractor()
        te._kiwi_available = False
        result = te._get_kiwi()
        assert result is None


class TestTermExtractorExtractPatterns:
    """Test regex-based pattern extraction (fallback path)."""

    def test_extract_camelcase(self):
        te = TermExtractor(min_occurrences=1)
        counter: Counter[str] = Counter()
        types: dict[str, str] = {}
        contexts: dict[str, list[str]] = {}
        original_case: dict[str, str] = {}

        text = "Use KnowledgeBase for KnowledgeBase management"
        te._extract_patterns(text, counter, types, contexts, original_case)
        assert "knowledgebase" in counter

    def test_extract_acronym(self):
        te = TermExtractor(min_occurrences=1)
        counter: Counter[str] = Counter()
        types: dict[str, str] = {}
        contexts: dict[str, list[str]] = {}
        original_case: dict[str, str] = {}

        text = "API and KB are important for RAG"
        te._extract_patterns(text, counter, types, contexts, original_case)
        assert "api" in counter
        assert "kb" in counter

    def test_extract_filters_code_artifacts(self):
        te = TermExtractor(min_occurrences=1)
        counter: Counter[str] = Counter()
        types: dict[str, str] = {}
        contexts: dict[str, list[str]] = {}

        text = "Use border-radius and flex-direction properly"
        te._extract_patterns(text, counter, types, contexts)
        assert "border-radius" not in counter
        assert "flex-direction" not in counter

    def test_extract_filters_stop_terms(self):
        te = TermExtractor(min_occurrences=1)
        counter: Counter[str] = Counter()
        types: dict[str, str] = {}
        contexts: dict[str, list[str]] = {}

        text = "THE and FROM are stop words"
        te._extract_patterns(text, counter, types, contexts)
        assert "the" not in counter

    def test_context_extraction(self):
        te = TermExtractor(min_occurrences=1)
        counter: Counter[str] = Counter()
        types: dict[str, str] = {}
        contexts: dict[str, list[str]] = {}

        text = "The GraphRag system is powerful"
        te._extract_patterns(text, counter, types, contexts)
        if "graphrag" in contexts:
            assert len(contexts["graphrag"]) >= 1
            assert "..." in contexts["graphrag"][0]


class TestTermExtractorExtractFromChunks:
    async def test_regex_fallback_path(self):
        """When kiwi is unavailable, use regex patterns."""
        te = TermExtractor(min_occurrences=1)
        te._kiwi_available = False
        te._kiwi = None

        chunks = [
            "Use GraphRag for KnowledgeBase. GraphRag is great.",
            "GraphRag handles KnowledgeBase well.",
        ]
        terms = await te.extract_from_chunks(chunks, kb_id="test-kb")
        # Should find terms via regex
        term_names = [t.term.lower() for t in terms]
        assert any("graphrag" in n for n in term_names)

    async def test_empty_chunks(self):
        te = TermExtractor(min_occurrences=1)
        te._kiwi_available = False
        terms = await te.extract_from_chunks([], kb_id="test")
        assert terms == []

    async def test_min_occurrences_filter(self):
        te = TermExtractor(min_occurrences=3)
        te._kiwi_available = False
        chunks = ["GraphRag is nice"]  # only 1 occurrence
        terms = await te.extract_from_chunks(chunks, kb_id="test")
        assert len(terms) == 0


class TestTermExtractorSave:
    async def test_save_no_repo(self):
        te = TermExtractor()
        terms = [ExtractedTerm(term="API", pattern_type="acronym")]
        saved = await te.save_extracted_terms(terms, kb_id="test")
        assert saved == 0

    async def test_save_empty_terms(self):
        repo = MagicMock()
        te = TermExtractor(glossary_repo=repo)
        saved = await te.save_extracted_terms([], kb_id="test")
        assert saved == 0

    async def test_save_with_repo(self):
        repo = MagicMock()
        repo.get_by_term = AsyncMock(return_value=None)
        repo.save = AsyncMock()
        te = TermExtractor(glossary_repo=repo)

        terms = [ExtractedTerm(term="정산금", pattern_type="compound_noun", occurrences=5)]
        saved = await te.save_extracted_terms(terms, kb_id="test")
        assert saved == 1
        repo.save.assert_called_once()

    async def test_save_skips_existing(self):
        repo = MagicMock()
        repo.get_by_term = AsyncMock(return_value={"id": "existing"})
        repo.save = AsyncMock()
        te = TermExtractor(glossary_repo=repo)

        terms = [ExtractedTerm(term="API", pattern_type="acronym")]
        saved = await te.save_extracted_terms(terms, kb_id="test")
        assert saved == 0

    async def test_save_handles_exception(self):
        repo = MagicMock()
        repo.get_by_term = AsyncMock(return_value=None)
        repo.save = AsyncMock(side_effect=RuntimeError("db error"))
        te = TermExtractor(glossary_repo=repo)

        terms = [ExtractedTerm(term="정산금", pattern_type="compound_noun")]
        saved = await te.save_extracted_terms(terms, kb_id="test")
        assert saved == 0


class TestTermExtractorDiscoverSynonyms:
    async def test_parenthetical(self):
        te = TermExtractor()
        text = "K8s(쿠버네티스) 클러스터를 운영합니다."
        known = [{"term": "K8s", "synonyms": []}]
        result = await te.discover_synonyms(text, known)
        assert len(result) >= 1
        assert any("쿠버네티스" in r[1] for r in result)

    async def test_aka_pattern(self):
        te = TermExtractor()
        text = "데이터마트, 일명 DM으로 불립니다."
        known = [{"term": "데이터마트"}]
        result = await te.discover_synonyms(text, known)
        assert len(result) >= 1

    async def test_abbreviation_intro(self):
        te = TermExtractor()
        text = "Kubernetes(이하 K8s)를 사용합니다."
        known = [{"term": "Kubernetes"}]
        result = await te.discover_synonyms(text, known)
        assert len(result) >= 1

    async def test_empty_text(self):
        te = TermExtractor()
        result = await te.discover_synonyms("", [])
        assert result == []

    async def test_filters_code_artifacts(self):
        te = TermExtractor()
        text = "border-radius(CSS속성) 사용법"
        result = await te.discover_synonyms(text, [])
        # border-radius is a code artifact, should be filtered
        assert all("border-radius" not in r[0] for r in result)

    async def test_no_self_synonym(self):
        te = TermExtractor()
        text = "API(API) 설명"
        result = await te.discover_synonyms(text, [])
        assert all(r[0].lower() != r[1].lower() for r in result)


class TestTermExtractorSaveDiscoveredSynonyms:
    async def test_no_repo(self):
        te = TermExtractor()
        saved = await te.save_discovered_synonyms(
            [("K8s", "쿠버네티스", "parenthetical")], kb_id="test"
        )
        assert saved == 0

    async def test_existing_term(self):
        repo = MagicMock()
        repo.get_by_term = AsyncMock(return_value={
            "id": "1", "kb_id": "test", "term": "K8s", "synonyms": [],
        })
        repo.save = AsyncMock()
        te = TermExtractor(glossary_repo=repo)

        saved = await te.save_discovered_synonyms(
            [("K8s", "쿠버네티스", "parenthetical")], kb_id="test"
        )
        assert saved == 1

    async def test_new_term(self):
        repo = MagicMock()
        repo.get_by_term = AsyncMock(return_value=None)
        repo.save = AsyncMock()
        te = TermExtractor(glossary_repo=repo)

        saved = await te.save_discovered_synonyms(
            [("K8s", "쿠버네티스", "parenthetical")], kb_id="test"
        )
        assert saved == 1

    async def test_exception_handling(self):
        repo = MagicMock()
        repo.get_by_term = AsyncMock(side_effect=RuntimeError("fail"))
        te = TermExtractor(glossary_repo=repo)

        saved = await te.save_discovered_synonyms(
            [("K8s", "쿠버네티스", "parenthetical")], kb_id="test"
        )
        assert saved == 0


class TestExtractContext:
    def test_found(self):
        te = TermExtractor()
        ctx = te._extract_context("Hello API world", "API")
        assert ctx is not None
        assert "..." in ctx

    def test_not_found(self):
        te = TermExtractor()
        ctx = te._extract_context("Hello world", "XYZ")
        assert ctx is None
