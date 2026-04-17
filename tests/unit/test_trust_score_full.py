"""Unit tests for src/search/trust_score_service.py -- TrustScoreCalculator & TrustScoreService."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.search.trust_score_service import (
    FRESHNESS_HALF_LIFE,
    KTS_WEIGHTS,
    SOURCE_CREDIBILITY,
    TrustScoreCalculator,
    TrustScoreService,
    _tier_from_score,
    _utc_now,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def calc() -> TrustScoreCalculator:
    return TrustScoreCalculator()


@pytest.fixture()
def mock_trust_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_entry = AsyncMock(return_value=None)
    repo.save = AsyncMock()
    repo.get_by_kb = AsyncMock(return_value=[])
    repo.get_stale_entries = AsyncMock(return_value=[])
    repo.get_needs_review = AsyncMock(return_value=[])
    return repo


@pytest.fixture()
def mock_feedback_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_votes_for_entry = AsyncMock(return_value=(5, 1))
    return repo


@pytest.fixture()
def service(mock_trust_repo, mock_feedback_repo) -> TrustScoreService:
    return TrustScoreService(
        trust_score_repo=mock_trust_repo,
        feedback_repo=mock_feedback_repo,
    )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_utc_now(self):
        now = _utc_now()
        assert now.tzinfo is not None

    def test_tier_from_score_high(self):
        assert _tier_from_score(90.0) == "high"

    def test_tier_from_score_medium(self):
        assert _tier_from_score(75.0) == "medium"

    def test_tier_from_score_low(self):
        assert _tier_from_score(55.0) == "low"

    def test_tier_from_score_uncertain(self):
        assert _tier_from_score(10.0) == "uncertain"


# ---------------------------------------------------------------------------
# TrustScoreCalculator
# ---------------------------------------------------------------------------

class TestComputeSourceCredibility:
    def test_known_source_types(self, calc: TrustScoreCalculator):
        assert calc.compute_source_credibility("confluence_official") == 1.0
        assert calc.compute_source_credibility("git_docs") == SOURCE_CREDIBILITY["git_docs"]

    def test_unknown_source_type(self, calc: TrustScoreCalculator):
        assert calc.compute_source_credibility("unknown_type") == 0.0


class TestComputeFreshnessScore:
    def test_recent_document(self, calc: TrustScoreCalculator):
        now = _utc_now()
        score = calc.compute_freshness_score(now, "general")
        assert score == pytest.approx(1.0, abs=0.05)

    def test_old_document(self, calc: TrustScoreCalculator):
        old = _utc_now() - timedelta(days=365)
        score = calc.compute_freshness_score(old, "general")
        assert 0.1 <= score < 0.5

    def test_minimum_score(self, calc: TrustScoreCalculator):
        very_old = _utc_now() - timedelta(days=3650)
        score = calc.compute_freshness_score(very_old, "general")
        assert score >= 0.1

    def test_different_domains(self, calc: TrustScoreCalculator):
        old = _utc_now() - timedelta(days=90)
        policy_score = calc.compute_freshness_score(old, "policy")
        faq_score = calc.compute_freshness_score(old, "faq")
        # Policy has shorter half-life, decays faster
        assert policy_score < faq_score

    def test_naive_datetime_handled(self, calc: TrustScoreCalculator):
        naive_dt = datetime(2024, 1, 1)  # no tzinfo
        score = calc.compute_freshness_score(naive_dt, "general")
        assert 0.1 <= score <= 1.0


class TestComputeUserValidationScore:
    def test_no_votes(self, calc: TrustScoreCalculator):
        score = calc.compute_user_validation_score(0, 0, 0, 0)
        assert score == 0.5

    def test_all_upvotes(self, calc: TrustScoreCalculator):
        score = calc.compute_user_validation_score(100, 0, 0, 0)
        assert score > 0.9

    def test_all_downvotes(self, calc: TrustScoreCalculator):
        score = calc.compute_user_validation_score(0, 100, 0, 0)
        assert score < 0.1

    def test_expert_reviews_weight(self, calc: TrustScoreCalculator):
        no_expert = calc.compute_user_validation_score(5, 2, 0, 0)
        with_expert = calc.compute_user_validation_score(5, 2, 2, 0)
        assert with_expert > no_expert

    def test_error_reports_weight(self, calc: TrustScoreCalculator):
        no_errors = calc.compute_user_validation_score(5, 0, 0, 0)
        with_errors = calc.compute_user_validation_score(5, 0, 0, 3)
        assert with_errors < no_errors


class TestComputeUsageScore:
    def test_zero_usage(self, calc: TrustScoreCalculator):
        score = calc.compute_usage_score(0, 0, 0.0, 0)
        assert score == 0.0

    def test_high_usage(self, calc: TrustScoreCalculator):
        score = calc.compute_usage_score(10000, 500, 0.8, 1000)
        assert 0.5 <= score <= 1.0

    def test_ctr_clamped(self, calc: TrustScoreCalculator):
        score1 = calc.compute_usage_score(0, 0, -0.5, 0)
        score2 = calc.compute_usage_score(0, 0, 1.5, 0)
        assert score1 >= 0.0
        assert score2 <= 1.0

    def test_score_clamped_to_unit(self, calc: TrustScoreCalculator):
        score = calc.compute_usage_score(999999, 999999, 1.0, 999999)
        assert 0.0 <= score <= 1.0


class TestComputeKts:
    def test_basic_compute(self, calc: TrustScoreCalculator):
        score_data = {
            "source_type": "confluence_official",
            "freshness_domain": "general",
            "upvotes": 10,
            "downvotes": 1,
            "expert_reviews": 0,
            "open_error_reports": 0,
            "view_count": 100,
            "citation_count": 5,
            "bookmark_count": 10,
            "hallucination_score": 1.0,
            "consistency_score": 1.0,
        }
        now = _utc_now()
        result = calc.compute_kts(score_data, content_updated_at=now)
        assert "kts_score" in result
        assert 0 <= result["kts_score"] <= 100
        assert result["confidence_tier"] in ("high", "medium", "low", "uncertain")
        assert result["last_evaluated_at"] is not None

    def test_compute_without_content_date(self, calc: TrustScoreCalculator):
        score_data = {
            "source_type": "auto_extracted",
            "freshness_score": 0.8,  # pre-set
            "upvotes": 0,
            "downvotes": 0,
            "expert_reviews": 0,
            "open_error_reports": 0,
            "view_count": 0,
            "citation_count": 0,
            "bookmark_count": 0,
            "hallucination_score": 1.0,
            "consistency_score": 1.0,
        }
        result = calc.compute_kts(score_data)
        assert "kts_score" in result
        # freshness_score should remain 0.8 since no content_updated_at
        assert result.get("freshness_score") == 0.8

    def test_compute_mutates_in_place(self, calc: TrustScoreCalculator):
        score_data = {
            "source_type": "git_docs",
            "upvotes": 0,
            "downvotes": 0,
            "expert_reviews": 0,
            "open_error_reports": 0,
            "view_count": 0,
            "citation_count": 0,
            "bookmark_count": 0,
        }
        returned = calc.compute_kts(score_data)
        assert returned is score_data


class TestWilsonLowerBound:
    def test_no_votes(self):
        assert TrustScoreCalculator._wilson_lower_bound(0, 0) == 0.5

    def test_all_positive(self):
        score = TrustScoreCalculator._wilson_lower_bound(100, 0)
        assert score > 0.95

    def test_all_negative(self):
        score = TrustScoreCalculator._wilson_lower_bound(0, 100)
        assert score < 0.05

    def test_balanced(self):
        score = TrustScoreCalculator._wilson_lower_bound(50, 50)
        assert 0.3 <= score <= 0.5


# ---------------------------------------------------------------------------
# TrustScoreService
# ---------------------------------------------------------------------------

class TestGetOrCreateScore:
    async def test_returns_existing(self, service: TrustScoreService, mock_trust_repo):
        existing = {"entry_id": "e1", "kb_id": "kb1", "kts_score": 75.0}
        mock_trust_repo.get_by_entry = AsyncMock(return_value=existing)

        result = await service.get_or_create_score("e1", "kb1")
        assert result["kts_score"] == 75.0
        mock_trust_repo.save.assert_not_awaited()

    async def test_creates_new(self, service: TrustScoreService, mock_trust_repo):
        mock_trust_repo.get_by_entry = AsyncMock(return_value=None)

        result = await service.get_or_create_score("e2", "kb1", source_type="git_docs")
        assert result["entry_id"] == "e2"
        assert result["kb_id"] == "kb1"
        assert result["kts_score"] > 0
        mock_trust_repo.save.assert_awaited_once()


class TestGetScore:
    async def test_get_score(self, service: TrustScoreService, mock_trust_repo):
        mock_trust_repo.get_by_entry = AsyncMock(return_value={"kts_score": 50.0})
        result = await service.get_score("e1", "kb1")
        assert result["kts_score"] == 50.0


class TestComputeKtsService:
    async def test_recompute_with_feedback(self, service: TrustScoreService, mock_trust_repo, mock_feedback_repo):
        mock_trust_repo.get_by_entry = AsyncMock(return_value={
            "entry_id": "e1",
            "kb_id": "kb1",
            "source_type": "confluence_official",
            "freshness_domain": "general",
            "upvotes": 0,
            "downvotes": 0,
            "expert_reviews": 0,
            "open_error_reports": 0,
            "view_count": 50,
            "citation_count": 5,
            "bookmark_count": 3,
            "hallucination_score": 1.0,
            "consistency_score": 1.0,
            "kts_score": 50.0,
        })

        result = await service.compute_kts("e1", "kb1")
        assert "kts_score" in result
        mock_trust_repo.save.assert_awaited()
        mock_feedback_repo.get_votes_for_entry.assert_awaited()

    async def test_recompute_creates_if_missing(self, service: TrustScoreService, mock_trust_repo):
        mock_trust_repo.get_by_entry = AsyncMock(return_value=None)

        result = await service.compute_kts("new_entry", "kb1")
        assert "kts_score" in result

    async def test_recompute_feedback_error_handled(self, service: TrustScoreService, mock_trust_repo, mock_feedback_repo):
        mock_trust_repo.get_by_entry = AsyncMock(return_value={
            "entry_id": "e1",
            "kb_id": "kb1",
            "source_type": "auto_extracted",
            "upvotes": 0,
            "downvotes": 0,
            "expert_reviews": 0,
            "open_error_reports": 0,
            "view_count": 0,
            "citation_count": 0,
            "bookmark_count": 0,
            "hallucination_score": 1.0,
            "consistency_score": 1.0,
            "kts_score": 40.0,
        })
        mock_feedback_repo.get_votes_for_entry = AsyncMock(side_effect=OSError("db error"))

        result = await service.compute_kts("e1", "kb1")
        assert "kts_score" in result  # should still succeed


class TestBatchRecompute:
    async def test_batch_recompute(self, service: TrustScoreService, mock_trust_repo, mock_feedback_repo):
        entries = [
            {
                "entry_id": f"e{i}", "kb_id": "kb1",
                "source_type": "auto_extracted",
                "upvotes": 0, "downvotes": 0, "expert_reviews": 0,
                "open_error_reports": 0, "view_count": 0,
                "citation_count": 0, "bookmark_count": 0,
                "hallucination_score": 1.0, "consistency_score": 1.0,
                "kts_score": 50.0,
            }
            for i in range(3)
        ]
        mock_trust_repo.get_by_kb = AsyncMock(return_value=entries)
        mock_trust_repo.get_by_entry = AsyncMock(side_effect=lambda eid, kid: next(
            (e for e in entries if e["entry_id"] == eid), None
        ))

        count = await service.batch_recompute("kb1")
        assert count == 3

    async def test_batch_recompute_partial_failure(self, service: TrustScoreService, mock_trust_repo, mock_feedback_repo):
        entries = [
            {"entry_id": "e1", "kb_id": "kb1"},
            {"entry_id": "e2", "kb_id": "kb1"},
        ]
        mock_trust_repo.get_by_kb = AsyncMock(return_value=entries)

        call_count = 0

        async def failing_get(eid, kid):
            nonlocal call_count
            call_count += 1
            if eid == "e1":
                raise ValueError("fail")
            return {
                "entry_id": eid, "kb_id": kid,
                "source_type": "auto_extracted",
                "upvotes": 0, "downvotes": 0, "expert_reviews": 0,
                "open_error_reports": 0, "view_count": 0,
                "citation_count": 0, "bookmark_count": 0,
                "hallucination_score": 1.0, "consistency_score": 1.0,
                "kts_score": 50.0,
            }

        mock_trust_repo.get_by_entry = AsyncMock(side_effect=failing_get)

        count = await service.batch_recompute("kb1")
        # e1 fails, e2 succeeds (e2 calls get_by_entry which creates then recomputes)
        assert count >= 1


class TestGetTopEntries:
    async def test_get_top_entries(self, service: TrustScoreService, mock_trust_repo):
        entries = [
            {"entry_id": "e1", "kts_score": 80},
            {"entry_id": "e2", "kts_score": 90},
            {"entry_id": "e3", "kts_score": 70},
        ]
        mock_trust_repo.get_by_kb = AsyncMock(return_value=entries)

        result = await service.get_top_entries("kb1", limit=2)
        assert len(result) == 2
        assert result[0]["kts_score"] == 90


class TestGetStaleEntries:
    async def test_get_stale_entries(self, service: TrustScoreService, mock_trust_repo):
        mock_trust_repo.get_stale_entries = AsyncMock(return_value=[{"entry_id": "stale1"}])
        result = await service.get_stale_entries("kb1")
        assert len(result) == 1


class TestGetNeedsReview:
    async def test_get_needs_review(self, service: TrustScoreService, mock_trust_repo):
        mock_trust_repo.get_needs_review = AsyncMock(return_value=[{"entry_id": "review1"}])
        result = await service.get_needs_review("kb1")
        assert len(result) == 1


class TestUpdateVote:
    async def test_upvote(self, service: TrustScoreService, mock_trust_repo):
        existing = {
            "entry_id": "e1", "kb_id": "kb1",
            "source_type": "auto_extracted",
            "upvotes": 5, "downvotes": 1,
            "expert_reviews": 0, "open_error_reports": 0,
            "view_count": 0, "citation_count": 0, "bookmark_count": 0,
            "hallucination_score": 1.0, "consistency_score": 1.0,
            "kts_score": 50.0,
        }
        mock_trust_repo.get_by_entry = AsyncMock(return_value=existing)

        result = await service.update_vote("e1", "kb1", "upvote")
        assert result["upvotes"] == 6
        mock_trust_repo.save.assert_awaited()

    async def test_downvote(self, service: TrustScoreService, mock_trust_repo):
        existing = {
            "entry_id": "e1", "kb_id": "kb1",
            "source_type": "auto_extracted",
            "upvotes": 5, "downvotes": 1,
            "expert_reviews": 0, "open_error_reports": 0,
            "view_count": 0, "citation_count": 0, "bookmark_count": 0,
            "hallucination_score": 1.0, "consistency_score": 1.0,
            "kts_score": 50.0,
        }
        mock_trust_repo.get_by_entry = AsyncMock(return_value=existing)

        result = await service.update_vote("e1", "kb1", "downvote")
        assert result["downvotes"] == 2

    async def test_vote_creates_if_missing(self, service: TrustScoreService, mock_trust_repo):
        mock_trust_repo.get_by_entry = AsyncMock(return_value=None)

        result = await service.update_vote("new", "kb1", "upvote")
        assert result["upvotes"] >= 1


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------

class TestConstants:
    def test_kts_weights_sum_to_one(self):
        total = sum(KTS_WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=0.01)

    def test_source_credibility_values(self):
        assert SOURCE_CREDIBILITY["confluence_official"] == 1.0
        for val in SOURCE_CREDIBILITY.values():
            assert 0.0 <= val <= 1.0

    def test_freshness_half_life_values(self):
        for domain, days in FRESHNESS_HALF_LIFE.items():
            assert days > 0
