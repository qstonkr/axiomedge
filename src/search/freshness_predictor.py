"""Freshness Predictor

Document-type-aware freshness scoring and stale-date prediction.
Mirrors oreo-ecosystem FreshnessPredictor exactly.

Scoring curve:
- score = 1.0 while age < lifespan * 0.5 (DECAY_START_RATIO)
- Cosine decay from 1.0 -> 0.0 in the remaining window.

Created: 2026-03-25
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import ClassVar

from src.config.weights import weights as _w

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Expected lifespan per document type (days).
DOC_TYPE_LIFESPAN: dict[str, int] = {
    "sop": 365,
    "policy": 365,
    "architecture": 270,
    "guide": 180,
    "general": 180,
    "faq": 120,
    "troubleshooting": 90,
    "release_note": 60,
    "meeting_note": 30,
}

_DECAY_START_RATIO = _w.trust_score.freshness_decay_start_ratio


@dataclass(frozen=True)
class FreshnessScore:
    """Immutable freshness scoring result."""

    document_id: str
    doc_type: str
    score: float
    days_since_modified: int
    expected_lifespan_days: int
    predicted_stale_date: datetime


class FreshnessPredictor:
    """Document-type-aware freshness scoring and stale-date prediction.

    Scoring curve:
    - score = 1.0 while age < lifespan * DECAY_START_RATIO
    - Cosine decay from 1.0 -> 0.0 in the remaining window.

    Example for general (lifespan=180, start_ratio=0.5):
    - Days 0-89: score = 1.0
    - Day 90: score ~ 1.0
    - Day 135: score ~ 0.5
    - Day 180: score = 0.0
    """

    STALE_SCORE_THRESHOLD: ClassVar[float] = _w.trust_score.freshness_stale_threshold

    def __init__(
        self,
        custom_lifespans: dict[str, int] | None = None,
    ) -> None:
        self._lifespans = {**DOC_TYPE_LIFESPAN}
        if custom_lifespans:
            self._lifespans.update(custom_lifespans)

    def score(
        self,
        last_modified: datetime,
        doc_type: str = "general",
        *,
        reference_date: datetime | None = None,
    ) -> float:
        """Calculate freshness score for a document.

        Returns:
            Freshness score between 0.0 and 1.0.
        """
        now = reference_date or _utc_now()
        lifespan = self._lifespans.get(doc_type, self._lifespans["general"])
        age_days = max(0, (now - last_modified).days)

        decay_start = int(lifespan * _DECAY_START_RATIO)

        if age_days <= decay_start:
            return 1.0

        if age_days >= lifespan:
            return 0.0

        # Cosine decay in [decay_start, lifespan]
        decay_window = lifespan - decay_start
        progress = (age_days - decay_start) / decay_window
        return round(0.5 * (1.0 + math.cos(math.pi * progress)), 4)

    def predict_stale_date(
        self,
        last_modified: datetime,
        doc_type: str = "general",
    ) -> datetime:
        """Predict when a document will reach the stale threshold.

        Uses binary search for efficiency.
        """
        lifespan = self._lifespans.get(doc_type, self._lifespans["general"])

        lo, hi = 0, lifespan
        while lo < hi:
            mid = (lo + hi) // 2
            ref = last_modified + timedelta(days=mid)
            s = self.score(last_modified, doc_type, reference_date=ref)
            if s >= self.STALE_SCORE_THRESHOLD:
                lo = mid + 1
            else:
                hi = mid

        return last_modified + timedelta(days=lo)

    def predict_freshness(
        self,
        doc_type: str,
        created_at: datetime,
        updated_at: datetime | None = None,
    ) -> float:
        """Convenience method: predict freshness from doc_type and timestamps.

        Args:
            doc_type: Document type (see DOC_TYPE_LIFESPAN).
            created_at: Document creation time.
            updated_at: Last modification time (falls back to created_at).

        Returns:
            Freshness score between 0.0 and 1.0.
        """
        last_modified = updated_at or created_at
        return self.score(last_modified, doc_type)

    def evaluate(
        self,
        document_id: str,
        last_modified: datetime,
        doc_type: str = "general",
        *,
        reference_date: datetime | None = None,
    ) -> FreshnessScore:
        """Compute a full freshness evaluation for a document."""
        now = reference_date or _utc_now()
        lifespan = self._lifespans.get(doc_type, self._lifespans["general"])
        age_days = max(0, (now - last_modified).days)
        freshness = self.score(last_modified, doc_type, reference_date=now)
        stale_date = self.predict_stale_date(last_modified, doc_type)

        return FreshnessScore(
            document_id=document_id,
            doc_type=doc_type,
            score=freshness,
            days_since_modified=age_days,
            expected_lifespan_days=lifespan,
            predicted_stale_date=stale_date,
        )

    def get_lifespan(self, doc_type: str) -> int:
        return self._lifespans.get(doc_type, self._lifespans["general"])
