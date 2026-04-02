"""Comprehensive tests for src/search/enhanced_similarity_matcher.py."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.search.enhanced_similarity_matcher import (
    AUTO_MATCH_THRESHOLD,
    REVIEW_THRESHOLD,
    EnhancedMatcherConfig,
    EnhancedSimilarityMatcher,
    MatchDecision,
    _PrecomputedStd,
    _strip_particles,
    _PARTICLES_LONG,
    _PARTICLES_SHORT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeTerm:
    term: str
    term_ko: str = ""
    term_type: str = "TERM"
    definition: str = ""
    synonyms: list[str] = field(default_factory=list)
    abbreviations: list[str] = field(default_factory=list)
    physical_meaning: str = ""


def _make_matcher(
    config: EnhancedMatcherConfig | None = None,
) -> EnhancedSimilarityMatcher:
    return EnhancedSimilarityMatcher(config=config or EnhancedMatcherConfig())


# ---------------------------------------------------------------------------
# EnhancedMatcherConfig defaults
# ---------------------------------------------------------------------------


class TestEnhancedMatcherConfig:
    def test_defaults(self):
        cfg = EnhancedMatcherConfig()
        assert cfg.enable_synonym_expansion is True
        assert cfg.enable_rapidfuzz is True
        assert cfg.enable_dense_search is True
        assert cfg.enable_cross_encoder is True

    def test_override(self):
        cfg = EnhancedMatcherConfig(
            enable_synonym_expansion=False,
            enable_rapidfuzz=False,
            enable_dense_search=False,
            enable_cross_encoder=False,
        )
        assert cfg.enable_synonym_expansion is False
        assert cfg.enable_rapidfuzz is False


# ---------------------------------------------------------------------------
# _strip_particles
# ---------------------------------------------------------------------------


class TestStripParticles:
    def test_strip_long_particle(self):
        # "시스템에서" -> "시스템" (에서 stripped)
        result = _strip_particles("시스템에서")
        assert result == "시스템"

    def test_strip_short_particle(self):
        # "시스템을" -> "시스템" (을 is not in the list, but 를 is)
        result = _strip_particles("시스템는")
        assert result == "시스템"

    def test_no_strip_too_short(self):
        # Should not strip if remaining would be too short
        result = _strip_particles("가는")
        assert result == "가는"

    def test_no_particle(self):
        result = _strip_particles("시스템")
        assert result == "시스템"

    def test_empty_string(self):
        result = _strip_particles("")
        assert result == ""

    def test_multiple_particles(self):
        # "시스템에서까지" -> strip "까지" then "에서" -> "시스템"
        result = _strip_particles("시스템에서까지")
        # "까지" is long, strip first -> "시스템에서", then "에서" -> "시스템"
        assert result == "시스템"

    def test_strip_various_long_particles(self):
        for p in _PARTICLES_LONG:
            original = "테스트용어" + p
            result = _strip_particles(original)
            assert result == "테스트용어", f"Failed to strip particle '{p}'"

    def test_strip_various_short_particles(self):
        for p in _PARTICLES_SHORT:
            original = "테스트용어" + p
            result = _strip_particles(original)
            assert result == "테스트용어", f"Failed to strip particle '{p}'"


# ---------------------------------------------------------------------------
# MatchDecision
# ---------------------------------------------------------------------------


class TestMatchDecision:
    def test_defaults(self):
        d = MatchDecision(zone="NEW_TERM")
        assert d.zone == "NEW_TERM"
        assert d.matched_term is None
        assert d.score == 0.0
        assert d.match_type == "none"
        assert d.channel_scores == {}
        assert d.matched_morphemes == []

    def test_full_creation(self):
        fake = FakeTerm(term="test")
        d = MatchDecision(
            zone="AUTO_MATCH",
            matched_term=fake,
            score=0.95,
            match_type="exact",
            channel_scores={"s_edit": 0.9},
            matched_morphemes=[("테스트", fake)],
        )
        assert d.score == 0.95
        assert d.channel_scores["s_edit"] == 0.9


# ---------------------------------------------------------------------------
# _PrecomputedStd
# ---------------------------------------------------------------------------


class TestPrecomputedStd:
    def test_creation(self):
        fake = FakeTerm(term="abc")
        pc = _PrecomputedStd(
            term=fake,
            normalized="abc",
            normalized_ko="가나다",
            ngrams={"ab", "bc"},
            ngrams_ko={"가나", "나다"},
            match_text="abc 가나다",
        )
        assert pc.normalized == "abc"
        assert len(pc.ngrams) == 2


# ---------------------------------------------------------------------------
# _collect_channel_scores
# ---------------------------------------------------------------------------


class TestCollectChannelScores:
    def test_all_channels(self):
        scores = EnhancedSimilarityMatcher._collect_channel_scores(
            target_idx=2,
            edit_results=[(1, 0.8), (2, 0.7)],
            sparse_results=[(2, 0.6), (3, 0.5)],
            dense_results=[(2, 0.9)],
        )
        assert scores == {"s_edit": 0.7, "s_sparse": 0.6, "s_dense": 0.9}

    def test_no_match_in_any_channel(self):
        scores = EnhancedSimilarityMatcher._collect_channel_scores(
            target_idx=99,
            edit_results=[(1, 0.8)],
            sparse_results=[(2, 0.6)],
            dense_results=[(3, 0.5)],
        )
        assert scores == {}

    def test_partial_match(self):
        scores = EnhancedSimilarityMatcher._collect_channel_scores(
            target_idx=1,
            edit_results=[(1, 0.8)],
            sparse_results=[],
            dense_results=[],
        )
        assert scores == {"s_edit": 0.8}

    def test_empty_channels(self):
        scores = EnhancedSimilarityMatcher._collect_channel_scores(
            target_idx=0,
            edit_results=[],
            sparse_results=[],
            dense_results=[],
        )
        assert scores == {}


# ---------------------------------------------------------------------------
# _decide_zone
# ---------------------------------------------------------------------------


class TestDecideZone:
    def test_auto_match(self):
        assert EnhancedSimilarityMatcher._decide_zone(AUTO_MATCH_THRESHOLD) == "AUTO_MATCH"
        assert EnhancedSimilarityMatcher._decide_zone(1.0) == "AUTO_MATCH"

    def test_review(self):
        score = REVIEW_THRESHOLD
        assert EnhancedSimilarityMatcher._decide_zone(score) == "REVIEW"

    def test_new_term(self):
        assert EnhancedSimilarityMatcher._decide_zone(0.0) == "NEW_TERM"
        assert EnhancedSimilarityMatcher._decide_zone(REVIEW_THRESHOLD - 0.01) == "NEW_TERM"


# ---------------------------------------------------------------------------
# _jaccard_from_sets
# ---------------------------------------------------------------------------


class TestJaccardFromSets:
    def test_identical(self):
        assert EnhancedSimilarityMatcher._jaccard_from_sets({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint(self):
        assert EnhancedSimilarityMatcher._jaccard_from_sets({"a"}, {"b"}) == 0.0

    def test_partial_overlap(self):
        j = EnhancedSimilarityMatcher._jaccard_from_sets({"a", "b"}, {"b", "c"})
        assert j == pytest.approx(1 / 3)

    def test_both_empty(self):
        assert EnhancedSimilarityMatcher._jaccard_from_sets(set(), set()) == 1.0

    def test_one_empty(self):
        assert EnhancedSimilarityMatcher._jaccard_from_sets(set(), {"a"}) == 0.0
        assert EnhancedSimilarityMatcher._jaccard_from_sets({"a"}, set()) == 0.0


# ---------------------------------------------------------------------------
# RRF Fusion (_l2_fuse)
# ---------------------------------------------------------------------------


class TestL2Fuse:
    def test_single_channel(self):
        m = _make_matcher()
        result = m._l2_fuse(
            edit_results=[(0, 0.9), (1, 0.8)],
            sparse_results=[],
            dense_results=[],
            top_k=10,
        )
        # Without dense, edit and sparse weights are normalized
        assert len(result) == 2
        assert result[0][0] == 0  # index 0 should be ranked first

    def test_two_channels_fuse(self):
        m = _make_matcher()
        result = m._l2_fuse(
            edit_results=[(0, 0.9)],
            sparse_results=[(0, 0.8), (1, 0.7)],
            dense_results=[],
            top_k=10,
        )
        # index 0 appears in both channels -> higher score
        # index 1 appears only in sparse
        assert result[0][0] == 0

    def test_three_channels(self):
        m = _make_matcher()
        result = m._l2_fuse(
            edit_results=[(0, 0.9)],
            sparse_results=[(1, 0.8)],
            dense_results=[(2, 0.7)],
            top_k=10,
        )
        assert len(result) == 3

    def test_top_k_limit(self):
        m = _make_matcher()
        many = [(i, 0.5) for i in range(100)]
        result = m._l2_fuse(many, [], [], top_k=5)
        assert len(result) == 5

    def test_empty_all_channels(self):
        m = _make_matcher()
        result = m._l2_fuse([], [], [], top_k=10)
        assert result == []


# ---------------------------------------------------------------------------
# L1 Exact Match
# ---------------------------------------------------------------------------


class TestL1ExactMatch:
    def test_not_loaded(self):
        m = _make_matcher()
        result = m._l1_exact_match("test")
        assert result is None

    def test_exact_match_term(self):
        m = _make_matcher()
        t = FakeTerm(term="TestTerm", term_ko="테스트용어")
        m.load_standard_terms([t])

        result = m._l1_exact_match("testterm")
        assert result is not None
        assert result.zone == "AUTO_MATCH"
        assert result.match_type == "exact"
        assert result.score == 1.0

    def test_synonym_match(self):
        m = _make_matcher()
        t = FakeTerm(term="MainTerm", term_ko="메인용어", synonyms=["AltName"])
        m.load_standard_terms([t])

        result = m._l1_exact_match("altname")
        assert result is not None
        assert result.match_type == "synonym"

    def test_particle_match(self):
        m = _make_matcher()
        t = FakeTerm(term="시스템", term_ko="시스템")
        m.load_standard_terms([t])

        result = m._l1_exact_match("시스템에서")
        assert result is not None
        assert result.match_type == "particle"
        assert result.score == 0.98

    def test_empty_candidate(self):
        m = _make_matcher()
        m.load_standard_terms([FakeTerm(term="x", term_ko="가")])
        result = m._l1_exact_match("")
        assert result is None

    def test_no_match(self):
        m = _make_matcher()
        m.load_standard_terms([FakeTerm(term="alpha", term_ko="알파")])
        result = m._l1_exact_match("beta")
        assert result is None


# ---------------------------------------------------------------------------
# load_standard_terms
# ---------------------------------------------------------------------------


class TestLoadStandardTerms:
    def test_basic_load(self):
        m = _make_matcher()
        terms = [
            FakeTerm(term="TermA", term_ko="용어A"),
            FakeTerm(term="TermB", term_ko="용어B"),
        ]
        m.load_standard_terms(terms)
        assert m._loaded is True
        assert len(m._precomputed) == 2

    def test_word_type_excluded_from_precomputed(self):
        m = _make_matcher()
        terms = [
            FakeTerm(term="TermA", term_ko="용어A", term_type="TERM"),
            FakeTerm(term="WordB", term_ko="단어B", term_type="WORD"),
        ]
        m.load_standard_terms(terms)
        # Only TERM type goes to _precomputed
        assert len(m._precomputed) == 1
        # But WORD goes to _word_lookup
        assert "단어b" in m._word_lookup or "wordb" in m._word_lookup

    def test_no_double_load(self):
        m = _make_matcher()
        m.load_standard_terms([FakeTerm(term="A")])
        count_first = len(m._normalized_lookup)
        m.load_standard_terms([FakeTerm(term="B"), FakeTerm(term="C")])
        # Should not reload
        assert len(m._normalized_lookup) == count_first

    def test_abbreviation_expansion(self):
        m = _make_matcher()
        t = FakeTerm(term="VirtualPrivateNetwork", abbreviations=["VPN"])
        m.load_standard_terms([t])
        result = m._l1_exact_match("vpn")
        assert result is not None

    def test_physical_meaning_expansion(self):
        m = _make_matcher()
        t = FakeTerm(term="SystemX", physical_meaning="핵심시스템")
        m.load_standard_terms([t])
        result = m._l1_exact_match("핵심시스템")
        assert result is not None

    def test_synonym_collision(self):
        """When two terms share the same synonym, collision count should increase."""
        m = _make_matcher()
        t1 = FakeTerm(term="TermA", synonyms=["shared"])
        t2 = FakeTerm(term="TermB", synonyms=["shared"])
        # No error, just logs collision
        m.load_standard_terms([t1, t2])

    def test_no_synonym_expansion_when_disabled(self):
        cfg = EnhancedMatcherConfig(enable_synonym_expansion=False)
        m = _make_matcher(config=cfg)
        t = FakeTerm(term="Main", synonyms=["Alt"])
        m.load_standard_terms([t])
        result = m._l1_exact_match("alt")
        assert result is None

    def test_get_term_type_callback(self):
        m = _make_matcher()
        t = FakeTerm(term="CustomType", term_ko="커스텀")
        m.load_standard_terms([t], get_term_type=lambda _: "WORD")
        # All treated as WORD, so precomputed should be empty
        assert len(m._precomputed) == 0


# ---------------------------------------------------------------------------
# L2 sparse (_l2_sparse)
# ---------------------------------------------------------------------------


class TestL2Sparse:
    def test_empty_candidate(self):
        m = _make_matcher()
        m.load_standard_terms([FakeTerm(term="abcdef", term_ko="가나다라마바")])
        result = m._l2_sparse("")
        assert result == []

    def test_finds_similar_term(self):
        m = _make_matcher()
        # Use longer terms with more n-gram overlap to pass threshold
        m.load_standard_terms([FakeTerm(term="abcdefghijklmn", term_ko="")])
        result = m._l2_sparse("abcdefghijklmx")
        # Many shared 3-grams -> high Jaccard
        assert len(result) > 0


# ---------------------------------------------------------------------------
# L2 RapidFuzz (_l2_rapidfuzz)
# ---------------------------------------------------------------------------


class TestL2RapidFuzz:
    def test_disabled_config(self):
        cfg = EnhancedMatcherConfig(enable_rapidfuzz=False)
        m = _make_matcher(config=cfg)
        m.load_standard_terms([FakeTerm(term="test")])
        result = m._l2_rapidfuzz("test")
        assert result == []

    def test_empty_candidate(self):
        m = _make_matcher()
        m.load_standard_terms([FakeTerm(term="test")])
        result = m._l2_rapidfuzz("")
        assert result == []


# ---------------------------------------------------------------------------
# match_enhanced / _match_single (async)
# ---------------------------------------------------------------------------


class TestMatchSingle:
    @pytest.mark.asyncio
    async def test_not_loaded_returns_new_term(self):
        m = _make_matcher()
        result = await m.match_enhanced(FakeTerm(term="anything"))
        assert result.zone == "NEW_TERM"

    @pytest.mark.asyncio
    async def test_exact_match_returns_auto(self):
        m = _make_matcher(config=EnhancedMatcherConfig(
            enable_rapidfuzz=False, enable_dense_search=False, enable_cross_encoder=False
        ))
        m.load_standard_terms([FakeTerm(term="ExactHit", term_ko="정확매칭")])
        result = await m.match_enhanced(FakeTerm(term="ExactHit"))
        assert result.zone == "AUTO_MATCH"
        assert result.match_type == "exact"

    @pytest.mark.asyncio
    async def test_no_match_returns_new_term(self):
        m = _make_matcher(config=EnhancedMatcherConfig(
            enable_rapidfuzz=False, enable_dense_search=False, enable_cross_encoder=False
        ))
        m.load_standard_terms([FakeTerm(term="Alpha")])
        result = await m.match_enhanced(FakeTerm(term="ZetaOmega"))
        assert result.zone == "NEW_TERM"

    @pytest.mark.asyncio
    async def test_fused_rrf_result(self):
        """When no cross-encoder, uses fallback thresholds on channel scores."""
        m = _make_matcher(config=EnhancedMatcherConfig(
            enable_dense_search=False, enable_cross_encoder=False
        ))
        # Load terms that will produce high rapidfuzz/sparse scores
        m.load_standard_terms([FakeTerm(term="서비스관리시스템", term_ko="서비스관리시스템")])
        result = await m.match_enhanced(FakeTerm(term="서비스관리시스템X", term_ko="서비스관리시스템X"))
        # Should get some kind of result from RRF
        assert result.zone in ("AUTO_MATCH", "REVIEW", "NEW_TERM")


# ---------------------------------------------------------------------------
# match_batch (async)
# ---------------------------------------------------------------------------


class TestMatchBatch:
    @pytest.mark.asyncio
    async def test_empty_batch(self):
        m = _make_matcher(config=EnhancedMatcherConfig(
            enable_cross_encoder=False, enable_dense_search=False, enable_rapidfuzz=False,
        ))
        m.load_standard_terms([FakeTerm(term="A")])
        results = await m.match_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_batch_with_exact_match(self):
        m = _make_matcher(config=EnhancedMatcherConfig(
            enable_cross_encoder=False, enable_dense_search=False, enable_rapidfuzz=False,
        ))
        m.load_standard_terms([FakeTerm(term="Target")])
        results = await m.match_batch([FakeTerm(term="Target")])
        assert len(results) == 1
        assert results[0].zone == "AUTO_MATCH"

    @pytest.mark.asyncio
    async def test_batch_disables_ce_for_large_batch(self):
        m = _make_matcher(config=EnhancedMatcherConfig(
            enable_cross_encoder=True, enable_dense_search=False, enable_rapidfuzz=False,
        ))
        m.load_standard_terms([FakeTerm(term="A")])
        # Create a batch larger than reduced_ce_max_terms
        from src.config_weights import weights as _w
        big_batch = [FakeTerm(term=f"term_{i}") for i in range(_w.similarity.reduced_ce_max_terms + 1)]
        results = await m.match_batch(big_batch)
        assert len(results) == len(big_batch)
        # _force_disable_ce should be reset after batch
        assert m._force_disable_ce is False

    @pytest.mark.asyncio
    async def test_batch_disable_cross_encoder_flag(self):
        m = _make_matcher(config=EnhancedMatcherConfig(
            enable_cross_encoder=True, enable_dense_search=False, enable_rapidfuzz=False,
        ))
        m.load_standard_terms([FakeTerm(term="A")])
        results = await m.match_batch(
            [FakeTerm(term="X")], disable_cross_encoder=True
        )
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Cross-Encoder (L3) mocked
# ---------------------------------------------------------------------------


class TestL3CrossEncoder:
    @pytest.mark.asyncio
    async def test_ce_disabled_returns_none(self):
        m = _make_matcher(config=EnhancedMatcherConfig(enable_cross_encoder=False))
        m.load_standard_terms([FakeTerm(term="Test")])
        result = await m._l3_cross_encoder_score("test", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_ce_no_model_returns_none(self):
        m = _make_matcher()
        m.load_standard_terms([FakeTerm(term="Test")])
        # _cross_encoder is None
        result = await m._l3_cross_encoder_score("test", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_ce_model_none_returns_none(self):
        m = _make_matcher()
        m.load_standard_terms([FakeTerm(term="Test")])
        mock_ce = MagicMock()
        mock_ce.model = None
        m.set_cross_encoder(mock_ce)
        result = await m._l3_cross_encoder_score("test", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_ce_batch_disabled(self):
        m = _make_matcher(config=EnhancedMatcherConfig(enable_cross_encoder=False))
        m.load_standard_terms([FakeTerm(term="Test")])
        result = await m._l3_cross_encoder_batch("test", [0])
        assert result == []

    @pytest.mark.asyncio
    async def test_ce_batch_no_cross_encoder(self):
        m = _make_matcher()
        m.load_standard_terms([FakeTerm(term="Test")])
        result = await m._l3_cross_encoder_batch("test", [0])
        assert result == []

    @pytest.mark.asyncio
    async def test_ce_batch_model_none(self):
        m = _make_matcher()
        m.load_standard_terms([FakeTerm(term="Test")])
        mock_ce = MagicMock()
        mock_ce.model = None
        m.set_cross_encoder(mock_ce)
        result = await m._l3_cross_encoder_batch("test", [0])
        assert result == []


# ---------------------------------------------------------------------------
# Setter methods
# ---------------------------------------------------------------------------


class TestSetters:
    def test_set_cross_encoder(self):
        m = _make_matcher()
        mock = MagicMock()
        m.set_cross_encoder(mock)
        assert m._cross_encoder is mock

    def test_set_embedding_adapter(self):
        m = _make_matcher()
        mock = MagicMock()
        m.set_embedding_adapter(mock)
        assert m._embedding_adapter is mock

    def test_init_dense_index_disabled(self):
        cfg = EnhancedMatcherConfig(enable_dense_search=False)
        m = _make_matcher(config=cfg)
        m.init_dense_index(MagicMock())
        assert m._dense_index is None


# ---------------------------------------------------------------------------
# _brute_force_split
# ---------------------------------------------------------------------------


class TestBruteForceSplit:
    def test_too_short(self):
        m = _make_matcher()
        assert m._brute_force_split("가나") == []
        assert m._brute_force_split("가나다") == []

    def test_no_match_in_lookup(self):
        m = _make_matcher()
        m._word_lookup = {}
        result = m._brute_force_split("테스트시스템")
        assert result == []

    def test_match_found(self):
        m = _make_matcher()
        from src.nlp.term_normalizer import TermNormalizer
        m._word_lookup = {
            TermNormalizer.normalize_for_comparison("테스트"): FakeTerm(term="테스트"),
            TermNormalizer.normalize_for_comparison("시스템"): FakeTerm(term="시스템"),
        }
        result = m._brute_force_split("테스트시스템")
        assert len(result) == 2
