"""Embedding Guard - Vector Validation.

Validates embedding vectors before use in Qdrant search.
Catches anomalies: NaN/Inf, zero vectors, dimension mismatch, magnitude outliers.

Adapted from oreo-ecosystem domain/knowledge/embedding_guard.py.
Simplified: no StatsD metrics emission.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from enum import Enum

from src.config_weights import weights as _w

logger = logging.getLogger(__name__)

# Constants (BGE-M3) — SSOT: config_weights.EmbeddingConfig.dimension
EXPECTED_DIMENSION: int = _w.embedding.dimension
MAGNITUDE_MIN = 0.1
MAGNITUDE_MAX = 50.0
ZERO_EPSILON = 1e-8


def sparse_token_hash(token: str) -> int:
    """Deterministic token hash for sparse vector indices.

    Uses MD5 for cross-process stability (Python hash() is randomized).
    Returns value in range [1, 99999] — Qdrant requires indices > 0.
    """
    h = int(hashlib.md5(token.encode()).hexdigest(), 16) % 100000
    return h if h > 0 else 1


class VectorVerdict(Enum):
    VALID = "valid"
    INVALID = "invalid"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class VectorCheckResult:
    verdict: VectorVerdict
    issues: tuple[str, ...] = ()
    dimension: int = 0
    l2_norm: float = 0.0

    @property
    def is_valid(self) -> bool:
        return self.verdict == VectorVerdict.VALID


_VALID_DEFAULT = VectorCheckResult(verdict=VectorVerdict.VALID)


def validate_vector(
    vector: list[float] | tuple[float, ...],
    *,
    expected_dim: int = EXPECTED_DIMENSION,
) -> VectorCheckResult:
    """Validate an embedding vector for anomalies.

    Never raises -- returns VALID on internal error.
    """
    try:
        return _validate_impl(vector, expected_dim=expected_dim)
    except Exception:  # noqa: BLE001
        return _VALID_DEFAULT


def _validate_impl(
    vector: list[float] | tuple[float, ...],
    *,
    expected_dim: int,
) -> VectorCheckResult:
    if not vector:
        return VectorCheckResult(
            verdict=VectorVerdict.INVALID,
            issues=("empty_vector",),
        )

    dim = len(vector)
    issues: list[str] = []

    # Dimension check
    if dim != expected_dim:
        issues.append(f"dimension_mismatch:{dim}!={expected_dim}")

    # NaN / Inf check
    has_nan_inf = False
    for v in vector:
        if math.isnan(v):
            issues.append("contains_nan")
            has_nan_inf = True
            break
        if math.isinf(v):
            issues.append("contains_inf")
            has_nan_inf = True
            break

    # L2 norm
    l2_norm = 0.0
    if not has_nan_inf:
        l2_norm = math.sqrt(sum(x * x for x in vector))

    # Zero vector
    if l2_norm < ZERO_EPSILON:
        issues.append("zero_vector")

    # Magnitude bounds
    if l2_norm > ZERO_EPSILON:
        if l2_norm < MAGNITUDE_MIN:
            issues.append(f"magnitude_too_low:{l2_norm:.4f}")
        elif l2_norm > MAGNITUDE_MAX:
            issues.append(f"magnitude_too_high:{l2_norm:.4f}")

    if not issues:
        return VectorCheckResult(
            verdict=VectorVerdict.VALID,
            dimension=dim,
            l2_norm=round(l2_norm, 6),
        )

    critical = ("empty_vector", "contains_nan", "contains_inf", "zero_vector", "dimension_mismatch")
    has_critical = any(
        issue.startswith(prefix) for issue in issues for prefix in critical
    )

    return VectorCheckResult(
        verdict=VectorVerdict.INVALID if has_critical else VectorVerdict.DEGRADED,
        issues=tuple(issues),
        dimension=dim,
        l2_norm=round(l2_norm, 6),
    )


def safe_embedding_or_zero(
    vector: list[float] | None,
    *,
    expected_dim: int = EXPECTED_DIMENSION,
) -> list[float]:
    """Return the vector if valid, else a zero vector of expected_dim.

    Use this as a fallback to prevent search failures.
    """
    if vector is None:
        return [0.0] * expected_dim
    result = validate_vector(vector, expected_dim=expected_dim)
    if result.is_valid or result.verdict == VectorVerdict.DEGRADED:
        return list(vector)
    logger.warning("Invalid embedding vector, using zero fallback: %s", result.issues)
    return [0.0] * expected_dim
