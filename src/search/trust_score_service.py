"""Trust Score Service

KTS computation and management service for knowledge-local.
Mirrors oreo-ecosystem TrustScoreService + TrustScoreCalculator.

6 signals:
    KTS = 0.20 * SourceCredibility
        + 0.20 * FreshnessScore
        + 0.25 * UserValidationScore
        + 0.10 * UsageScore
        + 0.15 * HallucinationScore
        + 0.10 * ConsistencyScore

Created: 2026-03-25
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants (SSOT from oreo-ecosystem entity)
# ---------------------------------------------------------------------------

# Source credibility mapping
SOURCE_CREDIBILITY: dict[str, float] = {
    "confluence_official": 1.0,
    "git_docs": 0.95,
    "confluence_personal": 0.75,
    "teams_chat": 0.50,
    "user_contribution_unverified": 0.30,
    "user_contribution_verified": 0.80,
    "auto_extracted": 0.20,
}

# Freshness half-life in days (exponential decay)
FRESHNESS_HALF_LIFE: dict[str, int] = {
    "policy": 90,
    "technical": 60,
    "faq": 120,
    "general": 180,
}

# KTS weight constants
KTS_WEIGHTS = {
    "source_credibility": 0.20,
    "freshness": 0.20,
    "user_validation": 0.25,
    "usage": 0.10,
    "hallucination": 0.15,
    "consistency": 0.10,
}

# Confidence tier boundaries
_TIER_HIGH = 85
_TIER_MEDIUM = 70
_TIER_LOW = 50


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _tier_from_score(score: float) -> str:
    if score >= _TIER_HIGH:
        return "high"
    if score >= _TIER_MEDIUM:
        return "medium"
    if score >= _TIER_LOW:
        return "low"
    return "uncertain"


# ---------------------------------------------------------------------------
# Calculator (stateless math, no I/O)
# ---------------------------------------------------------------------------

class TrustScoreCalculator:
    """Stateless calculator for KTS sub-components.

    All methods are pure functions. Mirrors oreo-ecosystem
    ``TrustScoreCalculator`` exactly.
    """

    _MAX_EXPECTED_VIEWS: int = 10_000
    _MAX_EXPECTED_CITATIONS: int = 500
    _MAX_EXPECTED_BOOKMARKS: int = 1_000

    _DEFAULT_HALF_LIFE_DAYS: int = 180
    _FRESHNESS_MIN_SCORE: float = 0.1

    _USAGE_WEIGHT_VIEWS: float = 0.2
    _USAGE_WEIGHT_CITATIONS: float = 0.3
    _USAGE_WEIGHT_CTR: float = 0.3
    _USAGE_WEIGHT_BOOKMARKS: float = 0.2

    def compute_source_credibility(self, source_type: str) -> float:
        return SOURCE_CREDIBILITY.get(source_type, 0.0)

    def compute_freshness_score(
        self,
        content_updated_at: datetime,
        domain: str,
    ) -> float:
        """Exponential decay: score = max(0.1, 2 ** (-age_days / half_life))."""
        half_life = FRESHNESS_HALF_LIFE.get(domain, self._DEFAULT_HALF_LIFE_DAYS)

        if content_updated_at.tzinfo is None:
            content_updated_at = content_updated_at.replace(tzinfo=timezone.utc)

        now = _utc_now()
        age_days = max((now - content_updated_at).total_seconds() / 86_400, 0.0)

        score = 2.0 ** (-age_days / half_life)
        return max(self._FRESHNESS_MIN_SCORE, score)

    def compute_user_validation_score(
        self,
        upvotes: int,
        downvotes: int,
        expert_reviews: int,
        open_error_reports: int,
    ) -> float:
        """Wilson score interval lower bound (95% CI).

        Expert reviews count as 5x upvote weight.
        Open error reports count as 3x downvote weight.
        """
        positive = float(upvotes) + 5.0 * float(expert_reviews)
        negative = float(downvotes) + 3.0 * float(open_error_reports)
        return self._wilson_lower_bound(positive, negative)

    def compute_usage_score(
        self,
        views: int,
        citations: int,
        ctr: float,
        bookmarks: int,
    ) -> float:
        """Log-normalised usage score."""
        norm_views = math.log1p(views) / math.log1p(self._MAX_EXPECTED_VIEWS)
        norm_citations = math.log1p(citations) / math.log1p(self._MAX_EXPECTED_CITATIONS)
        norm_ctr = max(0.0, min(float(ctr), 1.0))
        norm_bookmarks = math.log1p(bookmarks) / math.log1p(self._MAX_EXPECTED_BOOKMARKS)

        score = (
            self._USAGE_WEIGHT_VIEWS * norm_views
            + self._USAGE_WEIGHT_CITATIONS * norm_citations
            + self._USAGE_WEIGHT_CTR * norm_ctr
            + self._USAGE_WEIGHT_BOOKMARKS * norm_bookmarks
        )
        return max(0.0, min(score, 1.0))

    def compute_kts(
        self,
        score_data: dict[str, Any],
        content_updated_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Orchestrate all sub-score computations and return updated data.

        Mutates ``score_data`` in place and returns it.
        """
        source_type = score_data.get("source_type", "auto_extracted")
        score_data["source_credibility"] = self.compute_source_credibility(source_type)

        if content_updated_at is not None:
            domain = score_data.get("freshness_domain", "general")
            score_data["freshness_score"] = self.compute_freshness_score(
                content_updated_at, domain,
            )

        score_data["user_validation_score"] = self.compute_user_validation_score(
            upvotes=score_data.get("upvotes", 0),
            downvotes=score_data.get("downvotes", 0),
            expert_reviews=score_data.get("expert_reviews", 0),
            open_error_reports=score_data.get("open_error_reports", 0),
        )

        score_data["usage_score"] = self.compute_usage_score(
            views=score_data.get("view_count", 0),
            citations=score_data.get("citation_count", 0),
            ctr=0.0,
            bookmarks=score_data.get("bookmark_count", 0),
        )

        # Weighted aggregation
        raw = (
            KTS_WEIGHTS["source_credibility"] * score_data.get("source_credibility", 0.0)
            + KTS_WEIGHTS["freshness"] * score_data.get("freshness_score", 1.0)
            + KTS_WEIGHTS["user_validation"] * score_data.get("user_validation_score", 0.5)
            + KTS_WEIGHTS["usage"] * score_data.get("usage_score", 0.0)
            + KTS_WEIGHTS["hallucination"] * score_data.get("hallucination_score", 1.0)
            + KTS_WEIGHTS["consistency"] * score_data.get("consistency_score", 1.0)
        )
        kts = round(min(max(raw * 100, 0), 100), 2)
        score_data["kts_score"] = kts
        score_data["confidence_tier"] = _tier_from_score(kts)
        score_data["last_evaluated_at"] = _utc_now()
        score_data["updated_at"] = _utc_now()

        return score_data

    @staticmethod
    def _wilson_lower_bound(
        positive: float,
        negative: float,
        z: float = 1.96,
    ) -> float:
        """Wilson score interval lower bound (95% CI)."""
        n = positive + negative
        if n == 0:
            return 0.5
        p_hat = positive / n
        denominator = 1 + z**2 / n
        centre_adjusted_probability = p_hat + z**2 / (2 * n)
        adjusted_standard_deviation = z * math.sqrt(
            (p_hat * (1 - p_hat) + z**2 / (4 * n)) / n
        )
        return (centre_adjusted_probability - adjusted_standard_deviation) / denominator


# ---------------------------------------------------------------------------
# Service (I/O via repositories)
# ---------------------------------------------------------------------------

class TrustScoreService:
    """Knowledge Trust Score management service.

    SRP:
    - KTS creation/retrieval
    - Signal aggregation and KTS recomputation
    - Stale/top entry queries
    """

    def __init__(
        self,
        trust_score_repo: Any,
        feedback_repo: Any | None = None,
        calculator: TrustScoreCalculator | None = None,
    ):
        self._trust_score_repo = trust_score_repo
        self._feedback_repo = feedback_repo
        self._calculator = calculator or TrustScoreCalculator()

    async def get_or_create_score(
        self,
        entry_id: str,
        kb_id: str,
        source_type: str = "auto_extracted",
        freshness_domain: str = "general",
    ) -> dict[str, Any]:
        """Get existing KTS or create a new one."""
        existing = await self._trust_score_repo.get_by_entry(entry_id, kb_id)
        if existing:
            return existing

        now = _utc_now()
        score_data: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "entry_id": entry_id,
            "kb_id": kb_id,
            "kts_score": 0.0,
            "confidence_tier": "uncertain",
            "source_credibility": SOURCE_CREDIBILITY.get(source_type, 0.0),
            "freshness_score": 1.0,
            "user_validation_score": 0.5,
            "usage_score": 0.0,
            "hallucination_score": 1.0,
            "consistency_score": 1.0,
            "source_type": source_type,
            "freshness_domain": freshness_domain,
            "upvotes": 0,
            "downvotes": 0,
            "expert_reviews": 0,
            "open_error_reports": 0,
            "view_count": 0,
            "citation_count": 0,
            "bookmark_count": 0,
            "last_evaluated_at": now,
            "created_at": now,
            "updated_at": now,
        }

        # Compute initial KTS
        raw = (
            KTS_WEIGHTS["source_credibility"] * score_data["source_credibility"]
            + KTS_WEIGHTS["freshness"] * score_data["freshness_score"]
            + KTS_WEIGHTS["user_validation"] * score_data["user_validation_score"]
            + KTS_WEIGHTS["usage"] * score_data["usage_score"]
            + KTS_WEIGHTS["hallucination"] * score_data["hallucination_score"]
            + KTS_WEIGHTS["consistency"] * score_data["consistency_score"]
        )
        score_data["kts_score"] = round(min(max(raw * 100, 0), 100), 2)
        score_data["confidence_tier"] = _tier_from_score(score_data["kts_score"])

        await self._trust_score_repo.save(score_data)
        logger.info(
            "Created new KTS",
            extra={
                "entry_id": entry_id,
                "kb_id": kb_id,
                "source_type": source_type,
                "kts_score": score_data["kts_score"],
            },
        )
        return score_data

    async def get_score(self, entry_id: str, kb_id: str) -> dict[str, Any] | None:
        return await self._trust_score_repo.get_by_entry(entry_id, kb_id)

    async def compute_kts(
        self,
        entry_id: str,
        kb_id: str,
        content_updated_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Recompute KTS for an entry using all signals.

        Flow:
        1. Load existing KTS (or create)
        2. Aggregate votes from feedback_repo
        3. Recompute all sub-scores via calculator
        4. Save
        """
        score_data = await self._trust_score_repo.get_by_entry(entry_id, kb_id)
        if not score_data:
            score_data = await self.get_or_create_score(entry_id, kb_id)

        # Aggregate votes from feedback
        if self._feedback_repo:
            try:
                upvotes, downvotes = await self._feedback_repo.get_votes_for_entry(
                    entry_id, kb_id
                )
                score_data["upvotes"] = upvotes
                score_data["downvotes"] = downvotes
            except Exception as exc:
                logger.warning("Failed to aggregate votes: %s", exc)

        old_kts = score_data.get("kts_score", 0.0)
        self._calculator.compute_kts(score_data, content_updated_at)
        await self._trust_score_repo.save(score_data)

        logger.info(
            "Recalculated KTS",
            extra={
                "entry_id": entry_id,
                "kb_id": kb_id,
                "old_kts": old_kts,
                "new_kts": score_data["kts_score"],
                "confidence_tier": score_data["confidence_tier"],
            },
        )
        return score_data

    async def batch_recompute(
        self,
        kb_id: str,
        content_updated_at: datetime | None = None,
    ) -> int:
        """Recompute KTS for all entries in a KB. Returns count of updated entries."""
        entries = await self._trust_score_repo.get_by_kb(kb_id, min_score=0.0, limit=10000)
        count = 0
        for entry in entries:
            try:
                await self.compute_kts(
                    entry["entry_id"],
                    entry["kb_id"],
                    content_updated_at=content_updated_at,
                )
                count += 1
            except Exception as exc:
                logger.warning(
                    "batch_recompute failed for entry %s: %s",
                    entry.get("entry_id"),
                    exc,
                )
        logger.info("batch_recompute: kb_id=%s updated=%d", kb_id, count)
        return count

    async def get_top_entries(
        self, kb_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        entries = await self._trust_score_repo.get_by_kb(
            kb_id, min_score=0.0, limit=limit
        )
        entries.sort(key=lambda e: e.get("kts_score", 0.0), reverse=True)
        return entries[:limit]

    async def get_stale_entries(
        self, kb_id: str, max_freshness: float = 0.3
    ) -> list[dict[str, Any]]:
        return await self._trust_score_repo.get_stale_entries(kb_id, max_freshness)

    async def get_needs_review(self, kb_id: str | None = None) -> list[dict[str, Any]]:
        return await self._trust_score_repo.get_needs_review(kb_id)

    async def update_vote(
        self,
        entry_id: str,
        kb_id: str,
        vote_type: str,
    ) -> dict[str, Any]:
        """Update user validation signal and recompute KTS.

        Args:
            entry_id: Knowledge entry ID
            kb_id: Knowledge base ID
            vote_type: "upvote" or "downvote"
        """
        score_data = await self._trust_score_repo.get_by_entry(entry_id, kb_id)
        if not score_data:
            score_data = await self.get_or_create_score(entry_id, kb_id)

        if vote_type == "upvote":
            score_data["upvotes"] = score_data.get("upvotes", 0) + 1
        elif vote_type == "downvote":
            score_data["downvotes"] = score_data.get("downvotes", 0) + 1

        self._calculator.compute_kts(score_data)
        await self._trust_score_repo.save(score_data)
        return score_data
