"""Similarity matching utility functions."""

from __future__ import annotations

from src.config_weights import weights as _w

# =============================================================================
# Decision Policy Thresholds (Phase 0 분포 분석 후 조정)
# =============================================================================
AUTO_MATCH_THRESHOLD = _w.similarity.auto_match
REVIEW_THRESHOLD = _w.similarity.review

# 한국어 조사 패턴
_PARTICLES_LONG = ["에서", "으로", "까지", "부터", "처럼", "같이", "에게", "한테", "보다"]
_PARTICLES_SHORT = ["가", "를", "에", "의", "는", "은", "도", "와", "과", "이", "로", "만", "서"]


def _try_strip_particle(term: str, particles: list[str]) -> tuple[str, bool]:
    """Try to strip one trailing particle from the given list."""
    for p in particles:
        if term.endswith(p) and len(term) > len(p) + 2:
            return term[: -len(p)], True
    return term, False


def _strip_particles(term: str) -> str:
    """한국어 trailing 조사 제거."""
    changed = True
    while changed:
        term, changed = _try_strip_particle(term, _PARTICLES_LONG)
        if not changed:
            term, changed = _try_strip_particle(term, _PARTICLES_SHORT)
    return term
