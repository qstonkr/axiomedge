"""Unit tests for TrustScoreCalculator (KTS computation)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from src.search.trust_score_service import (
    KTS_WEIGHTS,
    SOURCE_CREDIBILITY,
    TrustScoreCalculator,
)


def _calc() -> TrustScoreCalculator:
    return TrustScoreCalculator()


class TestComputeSourceCredibility:
    def test_compute_source_credibility(self):
        calc = _calc()
        assert calc.compute_source_credibility("confluence_official") == 1.0
        assert calc.compute_source_credibility("git_docs") == 0.95
        assert calc.compute_source_credibility("teams_chat") == 0.50
        assert calc.compute_source_credibility("auto_extracted") == 0.20
        assert calc.compute_source_credibility("unknown_source") == 0.0


class TestComputeFreshnessScore:
    def test_compute_freshness_score_decay(self):
        """Recently updated documents should score higher than older ones."""
        calc = _calc()
        now = datetime.now(timezone.utc)

        # Fresh document (just updated)
        fresh_score = calc.compute_freshness_score(now, "general")
        assert fresh_score > 0.95

        # 90-day-old document (half of general half-life=180)
        old_90 = now - timedelta(days=90)
        score_90 = calc.compute_freshness_score(old_90, "general")
        assert 0.5 < score_90 < 1.0

        # Decay ordering
        assert fresh_score > score_90

    def test_compute_freshness_score_zero_for_very_old(self):
        """Very old documents should hit the minimum floor (0.1)."""
        calc = _calc()
        very_old = datetime.now(timezone.utc) - timedelta(days=3650)  # 10 years
        score = calc.compute_freshness_score(very_old, "general")
        assert score == 0.1  # floor


class TestComputeUserValidation:
    def test_compute_user_validation_wilson(self):
        """Wilson lower bound should produce reasonable scores."""
        calc = _calc()

        # No votes -> default 0.5
        score_none = calc.compute_user_validation_score(0, 0, 0, 0)
        assert score_none == 0.5

        # Only upvotes -> high score
        score_up = calc.compute_user_validation_score(100, 0, 0, 0)
        assert score_up > 0.9

        # Mixed votes -> moderate score
        score_mixed = calc.compute_user_validation_score(50, 50, 0, 0)
        assert 0.3 < score_mixed < 0.6

        # Expert reviews (5x weight) boost the score
        score_expert = calc.compute_user_validation_score(0, 0, 10, 0)
        assert score_expert > 0.8


class TestComputeUsageScore:
    def test_compute_usage_score_log_normalized(self):
        """Usage score should increase with views/citations but cap at 1.0."""
        calc = _calc()

        # Zero usage
        score_zero = calc.compute_usage_score(0, 0, 0.0, 0)
        assert score_zero == 0.0

        # Moderate usage
        score_moderate = calc.compute_usage_score(100, 10, 0.5, 20)
        assert 0.0 < score_moderate < 1.0

        # High usage
        score_high = calc.compute_usage_score(10000, 500, 1.0, 1000)
        assert score_high > score_moderate
        assert score_high <= 1.0


class TestKTSWeightedCombination:
    def test_kts_weighted_combination(self):
        """compute_kts should produce a weighted combination scaled to 0-100."""
        calc = _calc()

        # Perfect scores for all signals
        score_data = {
            "source_type": "confluence_official",
            "freshness_domain": "general",
            "upvotes": 100,
            "downvotes": 0,
            "expert_reviews": 5,
            "open_error_reports": 0,
            "view_count": 5000,
            "citation_count": 200,
            "bookmark_count": 500,
            "hallucination_score": 1.0,
            "consistency_score": 1.0,
        }
        now = datetime.now(timezone.utc)
        result = calc.compute_kts(score_data, content_updated_at=now)

        kts = result["kts_score"]
        assert 0 <= kts <= 100
        assert kts > 70  # high-quality doc should score well
        assert result["confidence_tier"] in ("high", "medium")

    def test_kts_low_quality_document(self):
        """Low-quality signals should produce a low KTS."""
        calc = _calc()

        score_data = {
            "source_type": "auto_extracted",
            "freshness_domain": "general",
            "upvotes": 0,
            "downvotes": 10,
            "expert_reviews": 0,
            "open_error_reports": 5,
            "view_count": 0,
            "citation_count": 0,
            "bookmark_count": 0,
            "hallucination_score": 0.2,
            "consistency_score": 0.3,
        }
        very_old = datetime.now(timezone.utc) - timedelta(days=3650)
        result = calc.compute_kts(score_data, content_updated_at=very_old)

        kts = result["kts_score"]
        assert kts < 50
