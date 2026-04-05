"""Similarity matching sub-package -- re-exports for backward compatibility."""

from .matcher import EnhancedSimilarityMatcher  # noqa: F401
from .strategies import (  # noqa: F401
    EnhancedMatcherConfig,
    MatchDecision,
    _PrecomputedStd,
)
from .utils import (  # noqa: F401
    AUTO_MATCH_THRESHOLD,
    REVIEW_THRESHOLD,
    _PARTICLES_LONG,
    _PARTICLES_SHORT,
    _strip_particles,
    _try_strip_particle,
)

__all__ = [
    "AUTO_MATCH_THRESHOLD",
    "EnhancedMatcherConfig",
    "EnhancedSimilarityMatcher",
    "MatchDecision",
    "REVIEW_THRESHOLD",
    "_PARTICLES_LONG",
    "_PARTICLES_SHORT",
    "_PrecomputedStd",
    "_strip_particles",
    "_try_strip_particle",
]
