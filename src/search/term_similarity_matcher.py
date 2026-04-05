"""Term Similarity Matcher Service

LLM 추출 용어를 표준 용어사전과 대조하여 중복/유사 용어를 필터링합니다.

5단계 lightweight-first 매칭:
1. 정규화 후 Exact Match (O(1) set lookup)
2. 한국어 조사 제거 후 매칭 (O(1))
3. 3-gram Jaccard 유사도 (O(n), threshold >= 0.7)
4. Normalized Levenshtein (O(n), 짧은 용어만 len<30, threshold >= 0.8)
5. 토큰 중복 비율 (O(n), threshold >= 0.7)

기존 유틸리티 재사용:
- LexicalScorer: Jaccard 3-gram + Levenshtein
- TermNormalizer: Unicode NFC, 비교용 정규화

Created: 2026-03-06
Extracted from: oreo-ecosystem (application/services/knowledge/term_similarity_matcher.py)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from src.config_weights import weights as _w
from src.nlp.term_normalizer import TermNormalizer
from src.nlp.lexical_scorer import LexicalScorer

logger = logging.getLogger(__name__)

# 한국어 조사 패턴 (trailing)
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


def _tokenize(term: str) -> set[str]:
    """용어를 토큰(음절/단어) 단위로 분해.

    한글은 음절 단위, 영문은 단어 단위로 분리.
    """
    tokens: set[str] = set()
    # 공백/하이픈/언더스코어로 분리
    parts = re.split(r"[\s\-_]+", term.lower())
    for part in parts:
        if not part:
            continue
        # 영문이면 단어 통째로
        if part.isascii():
            tokens.add(part)
        else:
            # 한글이면 2음절 이상인 부분어절 단위 + 개별 음절도 추가
            tokens.add(part)
            if len(part) >= 2:
                for i in range(len(part)):
                    tokens.add(part[i])
    return tokens


@dataclass
class SimilarityMatchResult:
    """유사도 매칭 결과"""

    is_matched: bool
    matched_standard_term: Any | None = None
    match_type: str = "none"  # exact, particle_stripped, jaccard, levenshtein, token_overlap
    similarity_score: float = 0.0


@dataclass
class _PrecomputedStd:
    """사전 계산된 표준 용어 데이터 (매칭 루프 최적화용)."""

    term: Any
    normalized: str
    normalized_ko: str  # empty string if no term_ko
    ngrams: set[str]
    ngrams_ko: set[str]
    tokens: set[str]


class TermSimilarityMatcher:
    """표준 용어 유사도 매칭 서비스

    표준 용어를 메모리에 캐시하고,
    후보 용어와 5단계 cascade 매칭을 수행합니다.

    성능 최적화:
    - 정규화/n-gram/토큰을 load 시 사전 계산
    - n-gram 역색인으로 후보군 축소 (39K -> ~200건)

    Args:
        standard_terms: 표준 용어 리스트. 각 항목은 term, term_ko 속성 필요.
        jaccard_threshold: Jaccard 유사도 임계값 (기본 0.7)
        levenshtein_threshold: Levenshtein 유사도 임계값 (기본 0.8)
        token_overlap_threshold: 토큰 중복 비율 임계값 (기본 0.7)
    """

    # n-gram 역색인에서 후보로 선정할 최소 공유 n-gram 수
    _MIN_SHARED_NGRAMS = _w.similarity.min_shared_ngrams
    # n-gram 역색인에서 가져올 최대 후보 수
    _MAX_CANDIDATES = _w.similarity.max_candidates

    def __init__(
        self,
        jaccard_threshold: float = _w.similarity.jaccard_threshold,
        levenshtein_threshold: float = _w.similarity.levenshtein_threshold,
        token_overlap_threshold: float = _w.similarity.token_overlap_threshold,
    ) -> None:
        self._jaccard_threshold = jaccard_threshold
        self._levenshtein_threshold = levenshtein_threshold
        self._token_overlap_threshold = token_overlap_threshold
        self._scorer = LexicalScorer()

        # 메모리 캐시 (load_standard_terms 호출 후 채워짐)
        self._normalized_lookup: dict[str, Any] = {}
        self._all_standard: list[Any] = []
        self._precomputed: list[_PrecomputedStd] = []
        self._ngram_index: dict[str, list[int]] = {}  # n-gram -> precomputed indices
        self._loaded = False

    def _precompute_term(self, t) -> _PrecomputedStd:
        """Pre-compute normalized forms, n-grams, and tokens for a standard term."""
        normalized = TermNormalizer.normalize_for_comparison(t.term)
        if normalized:
            self._normalized_lookup[normalized] = t

        normalized_ko = ""
        if t.term_ko:
            normalized_ko = TermNormalizer.normalize_for_comparison(t.term_ko)
            if normalized_ko:
                self._normalized_lookup[normalized_ko] = t

        ngrams = self._scorer._ngrams(normalized)
        ngrams_ko = self._scorer._ngrams(normalized_ko) if normalized_ko else set()
        tokens = _tokenize(t.term)
        if t.term_ko:
            tokens |= _tokenize(t.term_ko)

        return _PrecomputedStd(
            term=t,
            normalized=normalized,
            normalized_ko=normalized_ko,
            ngrams=ngrams,
            ngrams_ko=ngrams_ko,
            tokens=tokens,
        )

    def load_standard_terms(self, terms: list[Any]) -> None:
        """표준 용어를 메모리 캐시로 로드.

        정규화, n-gram, 토큰을 사전 계산하고 n-gram 역색인을 구축합니다.

        Args:
            terms: 표준 용어 리스트. 각 항목은 .term, .term_ko 속성 필요.
        """
        if self._loaded:
            return

        self._all_standard = terms
        self._normalized_lookup = {}
        self._precomputed = []
        self._ngram_index = {}

        for idx, t in enumerate(terms):
            pc = self._precompute_term(t)
            self._precomputed.append(pc)

            # n-gram 역색인 구축
            for ng in pc.ngrams | pc.ngrams_ko:
                if ng not in self._ngram_index:
                    self._ngram_index[ng] = []
                self._ngram_index[ng].append(idx)

        self._loaded = True
        logger.info(
            "Loaded %d standard terms (%d lookup entries, %d ngram index keys)",
            len(terms),
            len(self._normalized_lookup),
            len(self._ngram_index),
        )

    def _get_candidates(self, candidate_ngrams: set[str]) -> list[int]:
        """n-gram 역색인으로 후보 인덱스를 빠르게 조회.

        공유 n-gram 수가 많은 순으로 정렬하여 상위 _MAX_CANDIDATES개 반환.
        """
        if not candidate_ngrams:
            return []

        # 각 표준 용어의 공유 n-gram 수를 카운트
        hit_counts: dict[int, int] = {}
        for ng in candidate_ngrams:
            for idx in self._ngram_index.get(ng, []):
                hit_counts[idx] = hit_counts.get(idx, 0) + 1

        # 최소 공유 수 필터 + 상위 N개
        filtered = [
            (idx, cnt) for idx, cnt in hit_counts.items()
            if cnt >= self._MIN_SHARED_NGRAMS
        ]
        filtered.sort(key=lambda x: x[1], reverse=True)

        return [idx for idx, _ in filtered[: self._MAX_CANDIDATES]]

    def _match_exact(self, candidate: str) -> SimilarityMatchResult | None:
        """Step 1-2: Exact match and particle-stripped match."""
        normalized = TermNormalizer.normalize_for_comparison(candidate)
        if normalized in self._normalized_lookup:
            return SimilarityMatchResult(
                is_matched=True,
                matched_standard_term=self._normalized_lookup[normalized],
                match_type="exact",
                similarity_score=1.0,
            )

        stripped = _strip_particles(candidate)
        if stripped != candidate:
            normalized_stripped = TermNormalizer.normalize_for_comparison(stripped)
            if normalized_stripped in self._normalized_lookup:
                return SimilarityMatchResult(
                    is_matched=True,
                    matched_standard_term=self._normalized_lookup[normalized_stripped],
                    match_type="particle_stripped",
                    similarity_score=0.98,
                )
        return None

    def _check_jaccard(
        self, pc: _PrecomputedStd, candidate_ngrams: set[str], best: SimilarityMatchResult,
    ) -> SimilarityMatchResult:
        """Step 3: 3-gram Jaccard similarity check."""
        jaccard = self._jaccard_from_sets(candidate_ngrams, pc.ngrams)
        if pc.ngrams_ko:
            jaccard = max(jaccard, self._jaccard_from_sets(candidate_ngrams, pc.ngrams_ko))
        if jaccard >= self._jaccard_threshold and jaccard > best.similarity_score:
            return SimilarityMatchResult(
                is_matched=True, matched_standard_term=pc.term,
                match_type="jaccard", similarity_score=round(jaccard, 4),
            )
        return best

    def _check_levenshtein(
        self, pc: _PrecomputedStd, normalized: str, best: SimilarityMatchResult,
    ) -> SimilarityMatchResult:
        """Step 4: Normalized Levenshtein (short terms only)."""
        if len(normalized) >= 30:
            return best
        lev_targets = [pc.normalized]
        if pc.normalized_ko:
            lev_targets.append(pc.normalized_ko)
        for lev_target in lev_targets:
            if len(lev_target) >= 30:
                continue
            lev = self._scorer._normalized_levenshtein(normalized, lev_target)
            if lev >= self._levenshtein_threshold and lev > best.similarity_score:
                best = SimilarityMatchResult(
                    is_matched=True, matched_standard_term=pc.term,
                    match_type="levenshtein", similarity_score=round(lev, 4),
                )
        return best

    def _check_token_overlap(
        self, pc: _PrecomputedStd, candidate_tokens: set[str], best: SimilarityMatchResult,
    ) -> SimilarityMatchResult:
        """Step 5: Token overlap ratio."""
        if not candidate_tokens or not pc.tokens:
            return best
        union = len(candidate_tokens | pc.tokens)
        if union <= 0:
            return best
        overlap = len(candidate_tokens & pc.tokens) / union
        if overlap >= self._token_overlap_threshold and overlap > best.similarity_score:
            return SimilarityMatchResult(
                is_matched=True, matched_standard_term=pc.term,
                match_type="token_overlap", similarity_score=round(overlap, 4),
            )
        return best

    def _match_candidate_against_standard(
        self,
        pc: _PrecomputedStd,
        normalized: str,
        candidate_ngrams: set[str],
        candidate_tokens: set[str],
        best: SimilarityMatchResult,
    ) -> SimilarityMatchResult:
        """Steps 3-5: Compare a candidate against a single standard term."""
        best = self._check_jaccard(pc, candidate_ngrams, best)
        best = self._check_levenshtein(pc, normalized, best)
        best = self._check_token_overlap(pc, candidate_tokens, best)
        return best

    def match(self, candidate: str) -> SimilarityMatchResult:
        """단일 용어 매칭 (5단계 cascade).

        Step 1-2: O(1) dict lookup (exact match, particle strip)
        Step 3-5: n-gram 역색인으로 후보군 축소 후 비교

        Args:
            candidate: 매칭할 후보 용어

        Returns:
            SimilarityMatchResult with match details
        """
        if not candidate or not self._normalized_lookup:
            return SimilarityMatchResult(is_matched=False)

        exact = self._match_exact(candidate)
        if exact is not None:
            return exact

        normalized = TermNormalizer.normalize_for_comparison(candidate)
        candidate_ngrams = self._scorer._ngrams(normalized)
        candidate_indices = self._get_candidates(candidate_ngrams)

        if not candidate_indices:
            return SimilarityMatchResult(is_matched=False)

        candidate_tokens = _tokenize(candidate)
        best = SimilarityMatchResult(is_matched=False)
        for idx in candidate_indices:
            best = self._match_candidate_against_standard(
                self._precomputed[idx], normalized, candidate_ngrams, candidate_tokens, best,
            )
        return best

    @staticmethod
    def _jaccard_from_sets(a: set[str], b: set[str]) -> float:
        """사전 계산된 n-gram set으로 Jaccard 계산."""
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        intersection = len(a & b)
        union = len(a | b)
        if union == 0:
            return 0.0
        return intersection / union

    def filter_terms(
        self, terms: list[Any]
    ) -> tuple[list[Any], list[Any]]:
        """용어를 표준 용어와 대조하여 분리.

        Args:
            terms: 후보 용어 목록 (.term, .term_ko 속성 필요)

        Returns:
            (new_terms, matched_terms) 튜플
            - new_terms: 표준 용어와 매칭되지 않은 신규 용어
            - matched_terms: 표준 용어와 매칭된 용어 (필터링 대상)
        """
        new_terms: list[Any] = []
        matched_terms: list[Any] = []

        for term in terms:
            # term과 term_ko 양쪽 검사
            result = self.match(term.term)
            if not result.is_matched and hasattr(term, 'term_ko') and term.term_ko:
                result = self.match(term.term_ko)

            if result.is_matched:
                matched_terms.append(term)
                logger.debug(
                    "Filtered term '%s' (matched '%s' via %s, score=%.2f)",
                    term.term,
                    result.matched_standard_term.term if result.matched_standard_term else "?",
                    result.match_type,
                    result.similarity_score,
                )
            else:
                new_terms.append(term)

        return new_terms, matched_terms
