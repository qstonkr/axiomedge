"""Multi-candidate disambiguation helpers.

매처 결과의 candidates 가 다중일 때 (F1 모호 케이스, 예: '이전' RELOC vs BFR)
주변 컨텍스트 정보로 best 후보 선택.

우선순위:
  1. KB prior (이미 매처 단계에서 적용 — candidates[0] = KB prior 우선)
  2. Surrounding-token word_type majority (이 모듈)
  3. Confidence score (tiebreak)
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)


def disambiguate_by_surrounding_tokens(
    candidates: list[Any],
    surrounding_word_types: list[str | None],
    *,
    fallback: str = "confidence",
) -> Any:
    """주변 토큰의 word_type majority 로 candidates 첫 후보 결정.

    Args:
        candidates: 매처가 반환한 multi-candidate (sorted by score 가정).
        surrounding_word_types: 같은 chunk/문맥의 다른 토큰들의 매처 결과
            best.word_type 리스트. None 은 무관 토큰.
        fallback: surrounding 정보 부족 시 fallback 정책 (currently 'confidence').

    Returns:
        선택된 candidate (단일).
    """
    if not candidates:
        raise ValueError("candidates cannot be empty")
    if len(candidates) == 1:
        return candidates[0]

    # surrounding word_type 분포 (None 제외)
    types = [t for t in surrounding_word_types if t]
    if not types:
        return _fallback_select(candidates, fallback)

    # Majority word_type
    counter = Counter(types)
    majority_type, _ = counter.most_common(1)[0]

    # candidates 중 majority 와 일치하는 첫 번째
    for c in candidates:
        if getattr(c, "word_type", None) == majority_type:
            logger.debug(
                "Disambig: surrounding majority=%s → matched candidate.term=%s",
                majority_type, getattr(c, "term", None),
            )
            return c

    # 일치 없으면 fallback
    return _fallback_select(candidates, fallback)


def _fallback_select(candidates: list[Any], policy: str) -> Any:
    """surrounding context 부족 시 fallback 선택."""
    if policy == "confidence":
        # confidence_score 가장 높은 후보 (None 은 1.0 가정)
        def _conf(c):
            v = getattr(c, "confidence_score", None)
            return float(v) if v is not None else 1.0
        return max(candidates, key=_conf)
    # 기본: 첫 번째
    return candidates[0]
