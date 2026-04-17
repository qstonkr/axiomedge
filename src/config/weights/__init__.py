"""Centralized weights, thresholds, and tuning parameters (SSOT).

검색/인제스트/임베딩/캐시/LLM 관련 **하이퍼파라미터** 를 한 곳에 모은다.
코드에서는 ``from src.config.weights import weights`` 한 줄만 쓰면 됨.

### Config 3파일 경계 (이 파일은 가운데)

| 파일 | 역할 |
|---|---|
| ``src/config.py`` | **인프라** — DB 주소, 포트, timeout, 연결 풀 (env var override) |
| ``src/config_weights`` (이 패키지) | **하이퍼파라미터** — 검색 가중치, threshold, 캐시 TTL |
| ``src/distill/config.py`` | **Distill 프로필** — LoRA, lr, epochs, QA style (YAML / DB override) |

### 서브모듈 맵

- **search** — RerankerWeights, HybridSearchWeights, SimilarityThresholds, SearchDefaults
- **confidence** — ConfidenceConfig, ResponseConfig
- **quality** — QualityConfig, TrustScoreWeights
- **pipeline** — OCRConfig, ChunkingConfig, PipelineConfig, DedupConfig
- **llm** — LLMConfig, EmbeddingConfig, TimeoutConfig
- **cache** — CacheConfig

Usage:
    from src.config.weights import weights

    weights.reranker.model_weight      # 0.6
    weights.llm.temperature            # 0.7
    weights.chunking.max_chunk_chars   # 2500
"""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

# Re-export all section classes for backward compatibility
from .search import (
    RerankerWeights,
    HybridSearchWeights,
    SimilarityThresholds,
    PreprocessorConfig,
    SearchDefaults,
)
from .confidence import ConfidenceConfig, ResponseConfig
from .quality import QualityConfig, TrustScoreWeights
from .pipeline import OCRConfig, ChunkingConfig, PipelineConfig, DedupConfig
from .llm import LLMConfig, EmbeddingConfig, TimeoutConfig
from .cache import CacheConfig, compute_cache_version

# Backward compat alias (used by tests)
_compute_cache_version = compute_cache_version
from ._helpers import _env_float, _env_int, _coerce_value

__all__ = [
    "weights",
    "Weights",
    "RerankerWeights",
    "HybridSearchWeights",
    "SimilarityThresholds",
    "PreprocessorConfig",
    "SearchDefaults",
    "ConfidenceConfig",
    "ResponseConfig",
    "QualityConfig",
    "TrustScoreWeights",
    "OCRConfig",
    "ChunkingConfig",
    "PipelineConfig",
    "DedupConfig",
    "LLMConfig",
    "EmbeddingConfig",
    "TimeoutConfig",
    "CacheConfig",
    # Helpers (used by tests)
    "_env_float",
    "_env_int",
]


class Weights:
    """All weights and thresholds in one place.

    Mutable singleton: supports runtime hot-reload via ``update_from_dict``
    and ``reset``.
    """

    _SECTION_CLASSES: dict[str, type] = {
        "reranker": RerankerWeights,
        "hybrid_search": HybridSearchWeights,
        "similarity": SimilarityThresholds,
        "preprocessor": PreprocessorConfig,
        "confidence": ConfidenceConfig,
        "response": ResponseConfig,
        "quality": QualityConfig,
        "ocr": OCRConfig,
        "llm": LLMConfig,
        "embedding": EmbeddingConfig,
        "chunking": ChunkingConfig,
        "pipeline": PipelineConfig,
        "timeouts": TimeoutConfig,
        "search": SearchDefaults,
        "trust_score": TrustScoreWeights,
        "dedup": DedupConfig,
        "cache": CacheConfig,
    }

    def __init__(self) -> None:
        self._init_defaults()

    def _init_defaults(self) -> None:
        """Initialize all sections with their default values."""
        for name, cls in self._SECTION_CLASSES.items():
            object.__setattr__(self, name, cls())
        cache_cfg: CacheConfig = getattr(self, "cache")
        object.__setattr__(cache_cfg, "cache_version", compute_cache_version(cache_cfg))

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Serialize all sections to a nested dict."""
        result: dict[str, dict[str, Any]] = {}
        for name in self._SECTION_CLASSES:
            section = getattr(self, name)
            result[name] = asdict(section)
        return result

    def update_from_dict(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """Apply partial updates to weight sections.

        ``overrides`` is ``{"section.field": value}`` or ``{"section": {"field": value}}``.
        Returns a dict of applied changes.
        """
        applied: dict[str, Any] = {}

        normalized: dict[str, dict[str, Any]] = {}
        for key, value in overrides.items():
            if isinstance(value, dict) and key in self._SECTION_CLASSES:
                normalized[key] = value
            elif "." in key:
                section_name, field_name = key.split(".", 1)
                normalized.setdefault(section_name, {})[field_name] = value

        for section_name, field_overrides in normalized.items():
            if section_name not in self._SECTION_CLASSES:
                continue
            self._apply_section_overrides(section_name, field_overrides, applied)

        return applied

    def _apply_section_overrides(
        self, section_name: str, field_overrides: dict[str, Any], applied: dict[str, Any],
    ) -> None:
        """Apply field overrides to a single weight section."""
        cls = self._SECTION_CLASSES[section_name]
        current = getattr(self, section_name)
        current_dict = asdict(current)
        valid_fields = {f.name for f in fields(cls)}

        changes: dict[str, Any] = {}
        for field_name, new_value in field_overrides.items():
            if field_name not in valid_fields:
                continue
            old_value = current_dict.get(field_name)
            expected_type = next(f.type for f in fields(cls) if f.name == field_name)
            try:
                coerced = _coerce_value(new_value, expected_type)
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
                continue
            changes[field_name] = coerced
            applied[f"{section_name}.{field_name}"] = {"old": old_value, "new": coerced}

        if changes:
            merged = {**current_dict, **changes}
            object.__setattr__(self, section_name, cls(**merged))

    def reset(self) -> None:
        """Reset all sections to their default values."""
        self._init_defaults()


# Singleton
weights = Weights()
