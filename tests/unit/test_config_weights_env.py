"""Unit tests for config_weights: _env_float, _env_int, frozen dataclass, cache version."""

from __future__ import annotations

import dataclasses

import pytest

from src.config_weights import (
    CacheConfig,
    ChunkingConfig,
    ConfidenceConfig,
    DedupConfig,
    EmbeddingConfig,
    HybridSearchWeights,
    LLMConfig,
    OCRConfig,
    PipelineConfig,
    PreprocessorConfig,
    QualityConfig,
    RerankerWeights,
    ResponseConfig,
    SearchDefaults,
    SimilarityThresholds,
    TimeoutConfig,
    TrustScoreWeights,
    Weights,
    _coerce_value,
    _compute_cache_version,
    _env_float,
    _env_int,
)


# ---------------------------------------------------------------------------
# _env_float / _env_int
# ---------------------------------------------------------------------------


class TestEnvFloat:
    def test_returns_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_ENV_FLOAT_XYZ", raising=False)
        assert _env_float("TEST_ENV_FLOAT_XYZ", 3.14) == 3.14

    def test_reads_valid_float_from_env(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_FLOAT_XYZ", "2.71")
        assert _env_float("TEST_ENV_FLOAT_XYZ", 0.0) == 2.71

    def test_returns_default_on_invalid_value(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_FLOAT_XYZ", "not-a-number")
        assert _env_float("TEST_ENV_FLOAT_XYZ", 9.9) == 9.9

    def test_handles_empty_string(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_FLOAT_XYZ", "")
        assert _env_float("TEST_ENV_FLOAT_XYZ", 1.0) == 1.0

    def test_reads_integer_string_as_float(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_FLOAT_XYZ", "42")
        assert _env_float("TEST_ENV_FLOAT_XYZ", 0.0) == 42.0

    def test_reads_negative_float(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_FLOAT_XYZ", "-0.5")
        assert _env_float("TEST_ENV_FLOAT_XYZ", 0.0) == -0.5


class TestEnvInt:
    def test_returns_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_ENV_INT_XYZ", raising=False)
        assert _env_int("TEST_ENV_INT_XYZ", 42) == 42

    def test_reads_valid_int_from_env(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT_XYZ", "99")
        assert _env_int("TEST_ENV_INT_XYZ", 0) == 99

    def test_returns_default_on_invalid_value(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT_XYZ", "abc")
        assert _env_int("TEST_ENV_INT_XYZ", 7) == 7

    def test_returns_default_on_float_string(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT_XYZ", "3.14")
        assert _env_int("TEST_ENV_INT_XYZ", 5) == 5

    def test_handles_empty_string(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT_XYZ", "")
        assert _env_int("TEST_ENV_INT_XYZ", 10) == 10


# ---------------------------------------------------------------------------
# Frozen dataclass immutability
# ---------------------------------------------------------------------------


class TestFrozenDataclasses:
    """All config dataclasses must be frozen (immutable)."""

    @pytest.mark.parametrize(
        "cls",
        [
            RerankerWeights,
            HybridSearchWeights,
            SimilarityThresholds,
            PreprocessorConfig,
            ConfidenceConfig,
            ResponseConfig,
            QualityConfig,
            OCRConfig,
            LLMConfig,
            EmbeddingConfig,
            ChunkingConfig,
            PipelineConfig,
            TimeoutConfig,
            SearchDefaults,
            TrustScoreWeights,
            DedupConfig,
            CacheConfig,
        ],
    )
    def test_dataclass_is_frozen(self, cls):
        assert dataclasses.is_dataclass(cls)
        instance = cls()
        # frozen=True means FrozenInstanceError on attribute assignment
        with pytest.raises(dataclasses.FrozenInstanceError):
            instance.this_does_not_exist = 42  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# _coerce_value
# ---------------------------------------------------------------------------


class TestCoerceValue:
    def test_float_from_string(self):
        assert _coerce_value("1.5", "float") == 1.5

    def test_int_from_string(self):
        assert _coerce_value("10", "int") == 10

    def test_bool_from_string_true(self):
        assert _coerce_value("true", "bool") is True
        assert _coerce_value("1", "bool") is True
        assert _coerce_value("yes", "bool") is True

    def test_bool_from_string_false(self):
        assert _coerce_value("false", "bool") is False
        assert _coerce_value("no", "bool") is False

    def test_str_passthrough(self):
        assert _coerce_value(42, "str") == "42"

    def test_unknown_type_passthrough(self):
        obj = {"a": 1}
        assert _coerce_value(obj, "dict[str, int]") is obj


# ---------------------------------------------------------------------------
# _compute_cache_version
# ---------------------------------------------------------------------------


class TestComputeCacheVersion:
    def test_deterministic(self):
        cfg = CacheConfig()
        v1 = _compute_cache_version(cfg)
        v2 = _compute_cache_version(cfg)
        assert v1 == v2

    def test_starts_with_v3_prefix(self):
        cfg = CacheConfig()
        version = _compute_cache_version(cfg)
        assert version.startswith("v3_")

    def test_changes_with_threshold(self):
        cfg1 = CacheConfig()
        # Create config with different threshold
        cfg2 = CacheConfig(threshold_policy=0.5)
        v1 = _compute_cache_version(cfg1)
        v2 = _compute_cache_version(cfg2)
        assert v1 != v2

    def test_changes_with_ttl(self):
        cfg1 = CacheConfig()
        cfg2 = CacheConfig(ttl_policy=999)
        assert _compute_cache_version(cfg1) != _compute_cache_version(cfg2)


# ---------------------------------------------------------------------------
# Weights cache_version auto-set
# ---------------------------------------------------------------------------


class TestWeightsCacheVersion:
    def test_cache_version_auto_computed(self):
        w = Weights()
        assert w.cache.cache_version != ""
        assert w.cache.cache_version.startswith("v3_")

    def test_cache_version_stable_across_instances(self):
        w1 = Weights()
        w2 = Weights()
        assert w1.cache.cache_version == w2.cache.cache_version


# ---------------------------------------------------------------------------
# Weights section classes completeness
# ---------------------------------------------------------------------------


class TestWeightsSectionClasses:
    def test_all_sections_present_in_registry(self):
        w = Weights()
        for name in Weights._SECTION_CLASSES:
            section = getattr(w, name)
            assert dataclasses.is_dataclass(section), f"{name} must be a dataclass"

    def test_to_dict_round_trip_field_count(self):
        w = Weights()
        d = w.to_dict()
        for section_name, cls in Weights._SECTION_CLASSES.items():
            expected_fields = {f.name for f in dataclasses.fields(cls)}
            actual_fields = set(d[section_name].keys())
            assert expected_fields == actual_fields, f"{section_name} fields mismatch"
