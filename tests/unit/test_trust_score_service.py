"""Unit tests for src/search/trust_score_service.py — TrustScoreService + TrustScoreCalculator."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.search.trust_score_service import (
    FRESHNESS_HALF_LIFE,
    KTS_WEIGHTS,
    SOURCE_CREDIBILITY,
    TrustScoreCalculator,
    TrustScoreService,
    _tier_from_score,
)


def _calc() -> TrustScoreCalculator:
    return TrustScoreCalculator()


def _mock_repo(existing: dict | None = None, kb_entries: list | None = None):
    repo = AsyncMock()
    repo.get_by_entry = AsyncMock(return_value=existing)
    repo.save = AsyncMock()
    repo.get_by_kb = AsyncMock(return_value=kb_entries or [])
    repo.get_stale_entries = AsyncMock(return_value=[])
    repo.get_needs_review = AsyncMock(return_value=[])
    return repo


# ---------------------------------------------------------------------------
# _tier_from_score
# ---------------------------------------------------------------------------


class TestTierFromScore:
    def test_high_tier(self):
        assert _tier_from_score(90.0) == "high"

    def test_medium_tier(self):
        assert _tier_from_score(75.0) == "medium"

    def test_low_tier(self):
        assert _tier_from_score(55.0) == "low"

    def test_uncertain_tier(self):
        assert _tier_from_score(30.0) == "uncertain"

    def test_boundary_high(self):
        assert _tier_from_score(85.0) == "high"


# ---------------------------------------------------------------------------
# TrustScoreCalculator — source credibility
# ---------------------------------------------------------------------------


class TestSourceCredibility:
    def test_known_sources(self):
        calc = _calc()
        assert calc.compute_source_credibility("confluence_official") == 1.0
        assert calc.compute_source_credibility("git_docs") == SOURCE_CREDIBILITY["git_docs"]
        assert calc.compute_source_credibility("auto_extracted") == SOURCE_CREDIBILITY["auto_extracted"]

    def test_unknown_source_returns_zero(self):
        calc = _calc()
        assert calc.compute_source_credibility("random_unknown") == 0.0


# ---------------------------------------------------------------------------
# TrustScoreCalculator — freshness
# ---------------------------------------------------------------------------


class TestFreshnessScore:
    def test_fresh_document_scores_high(self):
        calc = _calc()
        now = datetime.now(timezone.utc)
        score = calc.compute_freshness_score(now, "general")
        assert score > 0.95

    def test_old_document_decays(self):
        calc = _calc()
        now = datetime.now(timezone.utc)
        fresh = calc.compute_freshness_score(now, "general")
        old = calc.compute_freshness_score(now - timedelta(days=365), "general")
        assert fresh > old

    def test_very_old_hits_floor(self):
        calc = _calc()
        ancient = datetime.now(timezone.utc) - timedelta(days=3650)
        score = calc.compute_freshness_score(ancient, "general")
        assert score >= 0.1  # minimum floor

    def test_naive_datetime_treated_as_utc(self):
        calc = _calc()
        naive = datetime.now() - timedelta(days=1)
        score = calc.compute_freshness_score(naive, "general")
        assert 0.1 <= score <= 1.0

    def test_domain_half_life_affects_decay(self):
        calc = _calc()
        dt = datetime.now(timezone.utc) - timedelta(days=90)
        policy_score = calc.compute_freshness_score(dt, "policy")
        general_score = calc.compute_freshness_score(dt, "general")
        # policy half-life=90d, general=180d -> policy decays faster
        assert general_score > policy_score


# ---------------------------------------------------------------------------
# TrustScoreCalculator — user validation (Wilson score)
# ---------------------------------------------------------------------------


class TestUserValidation:
    def test_no_votes_returns_neutral(self):
        calc = _calc()
        score = calc.compute_user_validation_score(0, 0, 0, 0)
        assert score == 0.5

    def test_all_upvotes(self):
        calc = _calc()
        score = calc.compute_user_validation_score(100, 0, 0, 0)
        assert score > 0.9

    def test_all_downvotes(self):
        calc = _calc()
        score = calc.compute_user_validation_score(0, 100, 0, 0)
        assert score < 0.1

    def test_expert_reviews_boost(self):
        calc = _calc()
        no_expert = calc.compute_user_validation_score(10, 5, 0, 0)
        with_expert = calc.compute_user_validation_score(10, 5, 3, 0)
        assert with_expert > no_expert

    def test_error_reports_penalize(self):
        calc = _calc()
        no_errors = calc.compute_user_validation_score(10, 5, 0, 0)
        with_errors = calc.compute_user_validation_score(10, 5, 0, 3)
        assert no_errors > with_errors


# ---------------------------------------------------------------------------
# TrustScoreCalculator — usage score
# ---------------------------------------------------------------------------


class TestUsageScore:
    def test_zero_usage(self):
        calc = _calc()
        score = calc.compute_usage_score(0, 0, 0.0, 0)
        assert score == 0.0

    def test_high_usage(self):
        calc = _calc()
        score = calc.compute_usage_score(5000, 200, 0.8, 500)
        assert score > 0.5

    def test_ctr_clamped(self):
        calc = _calc()
        score = calc.compute_usage_score(0, 0, 2.0, 0)
        assert score <= 1.0

    def test_score_bounded(self):
        calc = _calc()
        score = calc.compute_usage_score(100000, 10000, 1.0, 50000)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# TrustScoreCalculator — compute_kts (orchestration)
# ---------------------------------------------------------------------------


class TestComputeKTS:
    def test_compute_kts_returns_score(self):
        calc = _calc()
        data = {
            "source_type": "confluence_official",
            "freshness_domain": "general",
            "upvotes": 10,
            "downvotes": 2,
            "expert_reviews": 1,
            "open_error_reports": 0,
            "view_count": 500,
            "citation_count": 20,
            "bookmark_count": 50,
            "freshness_score": 1.0,
            "hallucination_score": 1.0,
            "consistency_score": 1.0,
        }
        result = calc.compute_kts(data, content_updated_at=datetime.now(timezone.utc))
        assert "kts_score" in result
        assert 0.0 <= result["kts_score"] <= 100.0
        assert "confidence_tier" in result

    def test_compute_kts_without_updated_at(self):
        calc = _calc()
        data = {
            "source_type": "auto_extracted",
            "freshness_score": 0.5,
            "hallucination_score": 1.0,
            "consistency_score": 1.0,
        }
        result = calc.compute_kts(data)
        assert "kts_score" in result

    def test_kts_weights_sum_to_one(self):
        total = sum(KTS_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Wilson lower bound
# ---------------------------------------------------------------------------


class TestWilsonLowerBound:
    def test_zero_votes(self):
        assert TrustScoreCalculator._wilson_lower_bound(0, 0) == 0.5

    def test_all_positive(self):
        lb = TrustScoreCalculator._wilson_lower_bound(100, 0)
        assert lb > 0.9

    def test_all_negative(self):
        lb = TrustScoreCalculator._wilson_lower_bound(0, 100)
        assert lb < 0.1


# ---------------------------------------------------------------------------
# TrustScoreService — get_or_create_score
# ---------------------------------------------------------------------------


class TestServiceGetOrCreate:
    @pytest.mark.asyncio
    async def test_returns_existing(self):
        existing = {"entry_id": "e1", "kts_score": 80.0}
        repo = _mock_repo(existing=existing)
        svc = TrustScoreService(repo)
        result = await svc.get_or_create_score("e1", "kb1")
        assert result == existing
        repo.save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_new(self):
        repo = _mock_repo(existing=None)
        svc = TrustScoreService(repo)
        result = await svc.get_or_create_score("e1", "kb1", source_type="git_docs")
        assert result["entry_id"] == "e1"
        assert result["kb_id"] == "kb1"
        assert "kts_score" in result
        repo.save.assert_awaited_once()


# ---------------------------------------------------------------------------
# TrustScoreService — compute_kts (with feedback repo)
# ---------------------------------------------------------------------------


class TestServiceComputeKTS:
    @pytest.mark.asyncio
    async def test_recompute_with_feedback(self):
        existing = {
            "entry_id": "e1",
            "kb_id": "kb1",
            "kts_score": 50.0,
            "source_type": "git_docs",
            "freshness_domain": "general",
            "freshness_score": 0.8,
            "hallucination_score": 1.0,
            "consistency_score": 1.0,
            "upvotes": 0,
            "downvotes": 0,
            "expert_reviews": 0,
            "open_error_reports": 0,
            "view_count": 0,
            "citation_count": 0,
            "bookmark_count": 0,
        }
        repo = _mock_repo(existing=existing)
        feedback_repo = AsyncMock()
        feedback_repo.get_votes_for_entry = AsyncMock(return_value=(15, 3))
        svc = TrustScoreService(repo, feedback_repo=feedback_repo)
        result = await svc.compute_kts("e1", "kb1")
        assert result["upvotes"] == 15
        assert result["downvotes"] == 3
        repo.save.assert_awaited()

    @pytest.mark.asyncio
    async def test_recompute_without_feedback_repo(self):
        existing = {
            "entry_id": "e1",
            "kb_id": "kb1",
            "kts_score": 50.0,
            "source_type": "auto_extracted",
            "freshness_score": 0.5,
            "hallucination_score": 1.0,
            "consistency_score": 1.0,
            "upvotes": 0,
            "downvotes": 0,
            "expert_reviews": 0,
            "open_error_reports": 0,
            "view_count": 0,
            "citation_count": 0,
            "bookmark_count": 0,
        }
        repo = _mock_repo(existing=existing)
        svc = TrustScoreService(repo)
        result = await svc.compute_kts("e1", "kb1")
        assert "kts_score" in result


# ---------------------------------------------------------------------------
# TrustScoreService — update_vote
# ---------------------------------------------------------------------------


class TestServiceUpdateVote:
    @pytest.mark.asyncio
    async def test_upvote_increments(self):
        existing = {
            "entry_id": "e1",
            "kb_id": "kb1",
            "kts_score": 60.0,
            "source_type": "git_docs",
            "freshness_score": 0.9,
            "hallucination_score": 1.0,
            "consistency_score": 1.0,
            "upvotes": 5,
            "downvotes": 1,
            "expert_reviews": 0,
            "open_error_reports": 0,
            "view_count": 100,
            "citation_count": 10,
            "bookmark_count": 5,
        }
        repo = _mock_repo(existing=existing)
        svc = TrustScoreService(repo)
        result = await svc.update_vote("e1", "kb1", "upvote")
        assert result["upvotes"] == 6

    @pytest.mark.asyncio
    async def test_downvote_increments(self):
        existing = {
            "entry_id": "e1",
            "kb_id": "kb1",
            "kts_score": 60.0,
            "source_type": "git_docs",
            "freshness_score": 0.9,
            "hallucination_score": 1.0,
            "consistency_score": 1.0,
            "upvotes": 5,
            "downvotes": 1,
            "expert_reviews": 0,
            "open_error_reports": 0,
            "view_count": 100,
            "citation_count": 10,
            "bookmark_count": 5,
        }
        repo = _mock_repo(existing=existing)
        svc = TrustScoreService(repo)
        result = await svc.update_vote("e1", "kb1", "downvote")
        assert result["downvotes"] == 2


# ---------------------------------------------------------------------------
# TrustScoreService — batch and query methods
# ---------------------------------------------------------------------------


class TestServiceBatchAndQueries:
    @pytest.mark.asyncio
    async def test_batch_recompute(self):
        entries = [
            {
                "entry_id": f"e{i}",
                "kb_id": "kb1",
                "kts_score": 50.0,
                "source_type": "auto_extracted",
                "freshness_score": 0.5,
                "hallucination_score": 1.0,
                "consistency_score": 1.0,
                "upvotes": 0,
                "downvotes": 0,
                "expert_reviews": 0,
                "open_error_reports": 0,
                "view_count": 0,
                "citation_count": 0,
                "bookmark_count": 0,
            }
            for i in range(3)
        ]
        repo = _mock_repo(existing=entries[0], kb_entries=entries)
        svc = TrustScoreService(repo)
        count = await svc.batch_recompute("kb1")
        assert count == 3

    @pytest.mark.asyncio
    async def test_get_top_entries(self):
        entries = [
            {"entry_id": "e1", "kts_score": 90.0},
            {"entry_id": "e2", "kts_score": 60.0},
            {"entry_id": "e3", "kts_score": 80.0},
        ]
        repo = _mock_repo(kb_entries=entries)
        svc = TrustScoreService(repo)
        top = await svc.get_top_entries("kb1", limit=2)
        assert len(top) == 2
        assert top[0]["kts_score"] >= top[1]["kts_score"]

    @pytest.mark.asyncio
    async def test_get_stale_entries(self):
        repo = _mock_repo()
        svc = TrustScoreService(repo)
        await svc.get_stale_entries("kb1", max_freshness=0.3)
        repo.get_stale_entries.assert_awaited_once_with("kb1", 0.3)

    @pytest.mark.asyncio
    async def test_get_needs_review(self):
        repo = _mock_repo()
        svc = TrustScoreService(repo)
        await svc.get_needs_review("kb1")
        repo.get_needs_review.assert_awaited_once_with("kb1")
