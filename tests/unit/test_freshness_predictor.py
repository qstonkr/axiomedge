"""Unit tests for FreshnessPredictor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.search.freshness_predictor import DOC_TYPE_LIFESPAN, FreshnessPredictor


def _predictor() -> FreshnessPredictor:
    return FreshnessPredictor()


class TestFreshnessPredictor:

    def test_fresh_document_score_near_one(self):
        """A just-created document should have score == 1.0."""
        fp = _predictor()
        now = datetime.now(timezone.utc)
        score = fp.score(now, "general", reference_date=now)
        assert score == 1.0

    def test_stale_document_score_near_zero(self):
        """A document older than its lifespan should have score == 0.0."""
        fp = _predictor()
        now = datetime.now(timezone.utc)
        lifespan = DOC_TYPE_LIFESPAN["general"]
        old = now - timedelta(days=lifespan + 1)
        score = fp.score(old, "general", reference_date=now)
        assert score == 0.0

    def test_doc_type_specific_lifespan(self):
        """Different doc types have different lifespans affecting decay."""
        fp = _predictor()
        now = datetime.now(timezone.utc)

        # SOP lifespan = 365 days; at 100 days it should still be 1.0 (within decay_start)
        sop_score = fp.score(now - timedelta(days=100), "sop", reference_date=now)
        assert sop_score == 1.0  # 100 < 365*0.5 = 182.5

        # FAQ lifespan = 120 days; at 100 days it should be decaying
        faq_score = fp.score(now - timedelta(days=100), "faq", reference_date=now)
        assert faq_score < 1.0  # 100 > 120*0.5 = 60

        # meeting_note lifespan = 30 days; at 100 days it should be 0.0
        meeting_score = fp.score(now - timedelta(days=100), "meeting_note", reference_date=now)
        assert meeting_score == 0.0

    def test_predict_stale_date(self):
        """predict_stale_date should return a date when score drops below threshold."""
        fp = _predictor()
        now = datetime.now(timezone.utc)
        stale_date = fp.predict_stale_date(now, "general")

        # Stale date should be in the future
        assert stale_date > now

        # Score at stale_date should be at or just below threshold
        score_at_stale = fp.score(now, "general", reference_date=stale_date)
        assert score_at_stale < fp.STALE_SCORE_THRESHOLD

        # Score one day before should be at or above threshold
        score_before = fp.score(now, "general", reference_date=stale_date - timedelta(days=1))
        assert score_before >= fp.STALE_SCORE_THRESHOLD

    def test_evaluate_returns_freshness_score(self):
        """evaluate() should return a full FreshnessScore dataclass."""
        fp = _predictor()
        now = datetime.now(timezone.utc)
        result = fp.evaluate("doc-1", now, "general", reference_date=now)

        assert result.document_id == "doc-1"
        assert result.doc_type == "general"
        assert result.score == 1.0
        assert result.days_since_modified == 0
        assert result.expected_lifespan_days == DOC_TYPE_LIFESPAN["general"]
        assert result.predicted_stale_date > now
