"""Unit tests for dashboard/components/constants.py — SSOT constants & icon maps."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make dashboard modules importable
_DASHBOARD_DIR = str(Path(__file__).resolve().parents[2] / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

from components.constants import (
    CONFIDENCE,
    DEDUP,
    ETC_RATIO_WARN,
    FRESHNESS_GOOD_PCT,
    FRESHNESS_WARN_PCT,
    GATE_FAIL_RATE_WARN,
    KB_STATUS_ICONS,
    KTS_SIGNALS,
    KTS_WEIGHTS,
    PASS_RATE_GOOD,
    PASS_RATE_WARN,
    PIPELINE_STEP_KEYS,
    PIPELINE_STEP_LABELS,
    PIPELINE_STEPS,
    RAGAS_FAITHFULNESS_WEIGHT,
    RAGAS_PRECISION_WEIGHT,
    RAGAS_RELEVANCY_WEIGHT,
    RUN_STATUS_ICONS,
    SOURCE_COVERAGE_GOOD,
    SOURCE_COVERAGE_WARN,
    STEP_STATUS_ICONS,
    TIER_ICONS,
)
from src.config.weights import ConfidenceConfig, DedupConfig, TrustScoreWeights


# ===========================================================================
# Backend SSOT consistency
# ===========================================================================

class TestSSOTConsistency:
    """Verify frontend constants match backend config_weights defaults."""

    def test_confidence_is_backend_instance(self):
        assert isinstance(CONFIDENCE, ConfidenceConfig)

    def test_confidence_high_matches(self):
        assert CONFIDENCE.high == ConfidenceConfig().high

    def test_confidence_medium_matches(self):
        assert CONFIDENCE.medium == ConfidenceConfig().medium

    def test_confidence_low_matches(self):
        assert CONFIDENCE.low == ConfidenceConfig().low

    def test_dedup_is_backend_instance(self):
        assert isinstance(DEDUP, DedupConfig)

    def test_dedup_near_dup_threshold(self):
        assert DEDUP.near_duplicate_threshold == DedupConfig().near_duplicate_threshold

    def test_kts_weights_is_backend_instance(self):
        assert isinstance(KTS_WEIGHTS, TrustScoreWeights)

    def test_kts_weights_sum_to_one(self):
        total = (
            KTS_WEIGHTS.hallucination_weight
            + KTS_WEIGHTS.source_credibility_weight
            + KTS_WEIGHTS.freshness_weight
            + KTS_WEIGHTS.consistency_weight
            + KTS_WEIGHTS.usage_weight
            + KTS_WEIGHTS.user_validation_weight
        )
        assert abs(total - 1.0) < 1e-9, f"KTS weights sum to {total}, expected 1.0"


# ===========================================================================
# RAGAS weights
# ===========================================================================

class TestRagasWeights:
    def test_faithfulness_weight(self):
        assert RAGAS_FAITHFULNESS_WEIGHT == 0.5

    def test_relevancy_weight(self):
        assert RAGAS_RELEVANCY_WEIGHT == 0.3

    def test_precision_weight(self):
        assert RAGAS_PRECISION_WEIGHT == 0.2

    def test_ragas_weights_sum_to_one(self):
        total = RAGAS_FAITHFULNESS_WEIGHT + RAGAS_RELEVANCY_WEIGHT + RAGAS_PRECISION_WEIGHT
        assert abs(total - 1.0) < 1e-9


# ===========================================================================
# Thresholds: ordering & valid ranges
# ===========================================================================

class TestThresholds:
    def test_pass_rate_good_gt_warn(self):
        assert PASS_RATE_GOOD > PASS_RATE_WARN

    def test_pass_rate_in_0_1(self):
        assert 0.0 < PASS_RATE_WARN < PASS_RATE_GOOD <= 1.0

    def test_gate_fail_rate_warn_positive(self):
        assert 0.0 < GATE_FAIL_RATE_WARN < 1.0

    def test_freshness_good_gt_warn(self):
        assert FRESHNESS_GOOD_PCT > FRESHNESS_WARN_PCT

    def test_freshness_in_0_100(self):
        assert 0.0 < FRESHNESS_WARN_PCT < FRESHNESS_GOOD_PCT <= 100.0

    def test_source_coverage_good_gt_warn(self):
        assert SOURCE_COVERAGE_GOOD > SOURCE_COVERAGE_WARN

    def test_source_coverage_in_0_1(self):
        assert 0.0 < SOURCE_COVERAGE_WARN < SOURCE_COVERAGE_GOOD <= 1.0

    def test_etc_ratio_warn_positive(self):
        assert 0.0 < ETC_RATIO_WARN < 1.0


# ===========================================================================
# Pipeline steps
# ===========================================================================

class TestPipelineSteps:
    def test_10_steps(self):
        assert len(PIPELINE_STEPS) == 10

    def test_keys_match_steps(self):
        assert PIPELINE_STEP_KEYS == [k for k, _ in PIPELINE_STEPS]

    def test_labels_dict_complete(self):
        for key in PIPELINE_STEP_KEYS:
            assert key in PIPELINE_STEP_LABELS
            assert isinstance(PIPELINE_STEP_LABELS[key], str)

    def test_first_step_is_preprocess(self):
        assert PIPELINE_STEP_KEYS[0] == "preprocess"

    def test_last_step_is_graph(self):
        assert PIPELINE_STEP_KEYS[-1] == "graph"


# ===========================================================================
# Icon mappings
# ===========================================================================

class TestIconMappings:
    def test_step_status_icons_cover_all_states(self):
        for state in ("completed", "running", "failed", "idle", "pending"):
            assert state in STEP_STATUS_ICONS

    def test_kb_status_icons_cover_both_cases(self):
        for status in ("ACTIVE", "active", "INACTIVE", "inactive", "ERROR", "error"):
            assert status in KB_STATUS_ICONS

    def test_tier_icons_cover_all_tiers(self):
        for tier in ("GLOBAL", "global", "BU", "bu", "TEAM", "team", "PERSONAL", "personal"):
            assert tier in TIER_ICONS

    def test_run_status_icons_cover_all(self):
        for status in ("PENDING", "pending", "RUNNING", "running", "COMPLETED", "completed", "FAILED", "failed"):
            assert status in RUN_STATUS_ICONS


# ===========================================================================
# KTS Signals
# ===========================================================================

class TestKTSSignals:
    def test_six_signals(self):
        assert len(KTS_SIGNALS) == 6

    def test_each_signal_has_required_keys(self):
        for name, sig in KTS_SIGNALS.items():
            assert "label" in sig, f"Signal {name} missing 'label'"
            assert "weight" in sig, f"Signal {name} missing 'weight'"
            assert "field" in sig, f"Signal {name} missing 'field'"

    def test_signal_weights_are_positive(self):
        for name, sig in KTS_SIGNALS.items():
            assert sig["weight"] > 0, f"Signal {name} weight should be > 0"

    def test_signal_weights_match_backend(self):
        """KTS_SIGNALS weights must match backend TrustScoreWeights."""
        assert KTS_SIGNALS["accuracy"]["weight"] == KTS_WEIGHTS.hallucination_weight
        assert KTS_SIGNALS["source_credibility"]["weight"] == KTS_WEIGHTS.source_credibility_weight
        assert KTS_SIGNALS["freshness"]["weight"] == KTS_WEIGHTS.freshness_weight
