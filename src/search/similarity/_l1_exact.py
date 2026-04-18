# pyright: reportAttributeAccessIssue=false
"""Similarity matcher -- L1 exact matching & L1.5 morpheme decomposition.

Extracted from matcher.py for SRP. Mixin for EnhancedSimilarityMatcher.
"""

from __future__ import annotations

import logging
from typing import Any

from src.nlp.korean.term_normalizer import TermNormalizer

from .strategies import GlossaryTermLike, MatchDecision
from .utils import _strip_particles

logger = logging.getLogger(__name__)


class L1ExactMixin:
    """L1 exact match + L1.5 morpheme decomposition methods."""

    def _classify_match_type(self, normalized: str, t) -> str:
        """Determine if a normalized match is exact or synonym."""
        if not self._config.enable_synonym_expansion:
            return "exact"
        t_norm = TermNormalizer.normalize_for_comparison(t.term)
        t_ko_norm = TermNormalizer.normalize_for_comparison(t.term_ko) if t.term_ko else ""
        if normalized != t_norm and normalized != t_ko_norm:
            return "synonym"
        return "exact"

    def _l1_exact_match(self, candidate: str) -> MatchDecision | None:
        """L1: 정규화 후 exact match."""
        if not candidate:
            return None

        normalized = TermNormalizer.normalize_for_comparison(candidate)
        if not normalized:
            return None

        # Step 1: Exact match
        if normalized in self._normalized_lookup:
            t = self._normalized_lookup[normalized]
            return MatchDecision(
                zone="AUTO_MATCH",
                matched_term=t,
                score=1.0,
                match_type=self._classify_match_type(normalized, t),
            )

        # Step 2: 조사 제거 후 매칭
        stripped = _strip_particles(candidate)
        if stripped != candidate:
            norm_stripped = TermNormalizer.normalize_for_comparison(stripped)
            if norm_stripped and norm_stripped in self._normalized_lookup:
                return MatchDecision(
                    zone="AUTO_MATCH",
                    matched_term=self._normalized_lookup[norm_stripped],
                    score=0.98,
                    match_type="particle",
                )

        return None

    # =========================================================================
    # Layer 1.5: Morpheme Decomposition (Korean Compound Words)
    # =========================================================================

    def _l1_5_morpheme_decompose(
        self, term: GlossaryTermLike,
    ) -> list[tuple[str, GlossaryTermLike]]:
        """L1.5: 한국어 복합어 형태소 분리 (enrichment only, 조기 종료 없음).

        "결제수단종류" -> ["결제", "수단", "종류"] 분리 후 개별 형태소를
        단어사전(word_lookup)에서 검색하여 매칭된 형태소 목록 반환.

        MatchDecision/zone 판정 없음 -- L2 sparse/dense 검색의 보조 메타데이터 용도.
        PENDING 용어는 한글이 term 필드에 있을 수 있으므로 양쪽 확인.
        """
        # term_ko 우선, 없으면 term이 한글인지 확인
        candidate_ko = getattr(term, 'term_ko', None)
        if not candidate_ko:
            term_str = getattr(term, 'term', '')
            if term_str and any("\uac00" <= c <= "\ud7a3" for c in term_str):
                candidate_ko = term_str
        if not candidate_ko or len(candidate_ko) < 3:
            return []

        try:
            from src.nlp.korean.morpheme_analyzer import get_analyzer

            analyzer = get_analyzer()
            if not analyzer.is_available:
                return []
            result = analyzer.analyze(candidate_ko)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            return []

        # 명사 형태소 추출 (2글자 이상의 NNG/NNP)
        noun_morphemes = [
            t.form
            for t in result.tokens
            if t.tag in ("NNG", "NNP") and len(t.form) >= 2
        ]

        # Fallback: 형태소 분석기가 미등록어(UN)로 반환하면 brute-force 2분할
        if len(noun_morphemes) < 2:
            noun_morphemes = self._brute_force_split(candidate_ko)

        if len(noun_morphemes) < 2:
            return []  # 단일 형태소는 L1에서 이미 처리

        # 각 형태소를 단어사전(word_lookup)에서만 검색
        matched: list[tuple[str, Any]] = []
        for morpheme in noun_morphemes:
            norm = TermNormalizer.normalize_for_comparison(morpheme)
            if norm and norm in self._word_lookup:
                matched.append((morpheme, self._word_lookup[norm]))

        return matched

    def _brute_force_split(self, text: str) -> list[str]:
        """형태소 분석 실패 시 brute-force 2분할로 복합어 분리.

        "보안시스템" (5글자) -> 가능한 분할 위치:
          pos=2: "보안" + "시스템" -> 둘 다 lookup에 있으면 반환
        가장 많은 분할 조각이 lookup에 매칭되는 분할을 선택.
        """
        if len(text) < 4:  # 최소 2+2
            return []

        best_split: list[str] = []
        best_match_count = 0

        for pos in range(2, len(text) - 1):  # 각 조각 최소 2글자
            left, right = text[:pos], text[pos:]
            if len(right) < 2:
                continue
            left_norm = TermNormalizer.normalize_for_comparison(left)
            right_norm = TermNormalizer.normalize_for_comparison(right)
            match_count = 0
            if left_norm and left_norm in self._word_lookup:
                match_count += 1
            if right_norm and right_norm in self._word_lookup:
                match_count += 1
            if match_count > best_match_count:
                best_match_count = match_count
                best_split = [left, right]

        return best_split if best_match_count >= 1 else []
