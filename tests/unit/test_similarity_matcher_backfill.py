"""Coverage backfill — EnhancedSimilarityMatcher L2/L3 pipeline paths.

Tests length penalty, zone decisions, graceful degradation, and MatchDecision.
"""

from dataclasses import dataclass
from typing import Any

from src.search.similarity.matcher import EnhancedSimilarityMatcher
from src.search.similarity.strategies import EnhancedMatcherConfig, MatchDecision
from src.search.similarity.utils import AUTO_MATCH_THRESHOLD, REVIEW_THRESHOLD


@dataclass
class _FakeTerm:
    """Fake standard term for testing."""
    term: str = ""
    term_ko: str = ""
    definition: str = ""
    synonyms: list[str] | None = None
    abbreviations: list[str] | None = None
    physical_meaning: str = ""
    term_type: str = "TERM"

    def __post_init__(self):
        if self.synonyms is None:
            self.synonyms = []
        if self.abbreviations is None:
            self.abbreviations = []


class TestLengthPenalty:
    """Tests for _apply_length_penalty (L2 RapidFuzz preprocessing)."""

    def test_equal_length_no_penalty(self) -> None:
        score = EnhancedSimilarityMatcher._apply_length_penalty(80.0, 5, "hello")
        assert score == 80.0

    def test_short_match_gets_penalty(self) -> None:
        # query_len=10, match_len=2 → ratio=0.2 < 0.5 threshold
        score = EnhancedSimilarityMatcher._apply_length_penalty(80.0, 10, "ab")
        assert score < 80.0

    def test_zero_length_no_crash(self) -> None:
        assert EnhancedSimilarityMatcher._apply_length_penalty(80.0, 0, "") == 80.0

    def test_empty_match_no_crash(self) -> None:
        assert EnhancedSimilarityMatcher._apply_length_penalty(80.0, 5, "") == 80.0

    def test_similar_length_no_penalty(self) -> None:
        # query_len=5, match_len=6 → ratio=5/6≈0.83 > 0.5 threshold
        score = EnhancedSimilarityMatcher._apply_length_penalty(80.0, 5, "abcdef")
        assert score == 80.0


class TestDecideZone:
    """Tests for zone determination from scores."""

    def test_high_score_auto_match(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        zone = matcher._decide_zone(0.90)
        assert zone == "AUTO_MATCH"

    def test_medium_score_review(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        zone = matcher._decide_zone(0.60)
        assert zone == "REVIEW"

    def test_low_score_new_term(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        zone = matcher._decide_zone(0.30)
        assert zone == "NEW_TERM"

    def test_exact_threshold_auto_match(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        zone = matcher._decide_zone(AUTO_MATCH_THRESHOLD)
        assert zone == "AUTO_MATCH"

    def test_exact_threshold_review(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        zone = matcher._decide_zone(REVIEW_THRESHOLD)
        assert zone == "REVIEW"


class TestMatcherInit:
    """Tests for matcher initialization and term loading."""

    def test_empty_init(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        assert matcher._config is not None

    def test_custom_config(self) -> None:
        config = EnhancedMatcherConfig(
            enable_rapidfuzz=False,
            enable_dense_search=False,
        )
        matcher = EnhancedSimilarityMatcher(config=config)
        assert matcher._config.enable_rapidfuzz is False

    def test_load_terms_builds_lookups(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        terms = [
            _FakeTerm(term="테스트", term_ko="테스트 용어", definition="정의"),
            _FakeTerm(term="검증", term_ko="검증 용어", definition="검증 정의"),
        ]
        matcher.load_standard_terms(terms)
        assert len(matcher._precomputed) == 2

    def test_load_terms_with_synonyms(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        terms = [
            _FakeTerm(
                term="POS",
                term_ko="포스",
                definition="판매시점정보관리",
                synonyms=["포스기", "POS시스템"],
            ),
        ]
        matcher.load_standard_terms(terms)
        # Normalized lookup should have entries
        assert len(matcher._normalized_lookup) > 0

    def test_load_idempotent(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        terms = [_FakeTerm(term="A", term_ko="에이")]
        matcher.load_standard_terms(terms)
        count_first = len(matcher._precomputed)
        matcher.load_standard_terms(terms)  # Second call should be no-op
        assert len(matcher._precomputed) == count_first


class TestL1ExactMatch:
    """Tests for L1 exact match path."""

    def test_exact_match_returns_auto(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        matcher.load_standard_terms([
            _FakeTerm(term="쿠버네티스", term_ko="쿠버네티스", definition="컨테이너 오케스트레이션"),
        ])
        result = matcher._l1_exact_match("쿠버네티스")
        assert result is not None
        assert result.zone == "AUTO_MATCH"
        assert result.score == 1.0

    def test_no_match_returns_none(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        matcher.load_standard_terms([
            _FakeTerm(term="쿠버네티스", term_ko="쿠버네티스", definition="컨테이너"),
        ])
        result = matcher._l1_exact_match("도커스웜")
        assert result is None

    def test_synonym_match(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        matcher.load_standard_terms([
            _FakeTerm(term="POS", term_ko="포스", synonyms=["포스기"]),
        ])
        result = matcher._l1_exact_match("포스기")
        assert result is not None
        assert result.zone == "AUTO_MATCH"


class TestGracefulDegradation:
    """Tests for CE degradation based on term count."""

    def test_small_batch_enables_ce(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        terms = [_FakeTerm(term=f"t{i}") for i in range(100)]
        top_k = matcher._resolve_ce_config(terms, disable_cross_encoder=False)
        assert top_k > 0  # CE enabled

    def test_large_batch_disables_ce(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        terms = [_FakeTerm(term=f"t{i}") for i in range(50000)]
        top_k = matcher._resolve_ce_config(terms, disable_cross_encoder=True)
        assert top_k == 0  # CE disabled

    def test_explicit_disable(self) -> None:
        matcher = EnhancedSimilarityMatcher()
        top_k = matcher._resolve_ce_config([], disable_cross_encoder=True)
        assert top_k == 0


class TestMatchDecision:
    """Tests for MatchDecision dataclass."""

    def test_auto_match(self) -> None:
        d = MatchDecision(
            zone="AUTO_MATCH",
            matched_term="테스트",
            score=0.95,
            match_type="exact",
        )
        assert d.zone == "AUTO_MATCH"
        assert d.matched_term == "테스트"

    def test_new_term(self) -> None:
        d = MatchDecision(
            zone="NEW_TERM",
            matched_term=None,
            score=0.0,
            match_type="none",
        )
        assert d.matched_term is None

    def test_review_with_channel_scores(self) -> None:
        d = MatchDecision(
            zone="REVIEW",
            matched_term="후보",
            score=0.65,
            match_type="rapidfuzz",
            channel_scores={"s_edit": 0.7, "s_sparse": 0.6},
        )
        assert d.channel_scores["s_edit"] == 0.7
