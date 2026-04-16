"""Unit tests for src/search/confidence_thresholds.py."""

import pytest

from src.config.weights import weights
from src.search.confidence_thresholds import (
    KnowledgeConfidenceThresholds,
    clamp_unit_interval,
    read_env_unit_interval,
)


class TestKnowledgeConfidenceThresholds:
    """Test threshold values match config_weights."""

    def test_high_matches_config(self) -> None:
        assert KnowledgeConfidenceThresholds.HIGH == weights.confidence.high

    def test_medium_matches_config(self) -> None:
        assert KnowledgeConfidenceThresholds.MEDIUM == weights.confidence.medium

    def test_low_matches_config(self) -> None:
        assert KnowledgeConfidenceThresholds.LOW == weights.confidence.low

    def test_retrieval_correct_matches(self) -> None:
        assert KnowledgeConfidenceThresholds.RETRIEVAL_CORRECT == weights.confidence.retrieval_correct

    def test_retrieval_ambiguous_matches(self) -> None:
        assert KnowledgeConfidenceThresholds.RETRIEVAL_AMBIGUOUS == weights.confidence.retrieval_ambiguous

    def test_crag_correct_matches(self) -> None:
        assert KnowledgeConfidenceThresholds.CRAG_CORRECT == weights.confidence.crag_correct

    def test_crag_weakness_matches(self) -> None:
        assert KnowledgeConfidenceThresholds.CRAG_WEAKNESS == weights.confidence.crag_weakness

    def test_factual_min_matches(self) -> None:
        assert KnowledgeConfidenceThresholds.FACTUAL_RESPONSE_MIN == weights.confidence.factual_min

    def test_quality_gate_faithfulness(self) -> None:
        assert (
            KnowledgeConfidenceThresholds.QUALITY_GATE_FAITHFULNESS_MIN
            == weights.confidence.quality_gate_faithfulness
        )

    def test_ordering_high_gt_medium_gt_low(self) -> None:
        assert (
            KnowledgeConfidenceThresholds.HIGH
            > KnowledgeConfidenceThresholds.MEDIUM
            > KnowledgeConfidenceThresholds.LOW
        )

    def test_all_thresholds_in_unit_interval(self) -> None:
        for attr in [
            "HIGH", "MEDIUM", "LOW",
            "RETRIEVAL_CORRECT", "RETRIEVAL_AMBIGUOUS",
            "CRAG_CORRECT", "CRAG_WEAKNESS",
            "FACTUAL_RESPONSE_MIN", "ANALYTICAL_RESPONSE_MIN",
            "ADVISORY_RESPONSE_MIN", "MULTI_HOP_RESPONSE_MIN",
        ]:
            val = getattr(KnowledgeConfidenceThresholds, attr)
            assert 0.0 <= val <= 1.0, f"{attr}={val} out of [0,1]"


class TestClampUnitInterval:
    """Test clamp_unit_interval utility."""

    def test_normal_value(self) -> None:
        assert clamp_unit_interval(0.5, 0.7) == 0.5

    def test_clamp_above_one(self) -> None:
        assert clamp_unit_interval(1.5, 0.7) == 1.0

    def test_clamp_below_zero(self) -> None:
        assert clamp_unit_interval(-0.3, 0.7) == 0.0

    def test_exact_boundaries(self) -> None:
        assert clamp_unit_interval(0.0, 0.5) == 0.0
        assert clamp_unit_interval(1.0, 0.5) == 1.0

    def test_invalid_string_uses_default(self) -> None:
        assert clamp_unit_interval("not_a_number", 0.42) == 0.42  # type: ignore[arg-type]

    def test_none_uses_default(self) -> None:
        assert clamp_unit_interval(None, 0.33) == 0.33  # type: ignore[arg-type]

    def test_numeric_string_parsed(self) -> None:
        assert clamp_unit_interval("0.75", 0.5) == 0.75  # type: ignore[arg-type]


class TestReadEnvUnitInterval:
    """Test read_env_unit_interval from environment."""

    def test_missing_env_uses_default(self) -> None:
        # Unlikely env var name
        result = read_env_unit_interval("__TEST_NONEXISTENT_VAR_12345__", 0.77)
        assert result == 0.77

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("__TEST_THRESHOLD__", "0.55")
        result = read_env_unit_interval("__TEST_THRESHOLD__", 0.99)
        assert result == 0.55

    def test_env_invalid_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("__TEST_BAD__", "abc")
        result = read_env_unit_interval("__TEST_BAD__", 0.60)
        assert result == 0.60
