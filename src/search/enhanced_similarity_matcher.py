"""Enhanced Term Similarity Matcher -- 3-Layer Architecture

산업 표준 3-Layer 아키텍처로 표준 용어사전 유사도 매칭:

Layer 1: Normalization + Exact Match (O(1))
  - TermNormalizer, 조사 제거, 동의어/약어/physical_meaning lookup 확장

Layer 2: Multi-Channel Candidate Retrieval + RRF Fusion
  - S_edit: RapidFuzz (WRatio, token_sort_ratio, partial_ratio)
  - S_sparse: 3-gram Jaccard (n-gram 역색인)
  - S_dense: BGE-M3 ONNX cosine (config flag 기반)

Layer 3: Precision Judgment (Cross-Encoder, config flag 기반)
  - BGE-Reranker-v2-m3 via CrossEncoderReranker
  - 3-Zone Decision: AUTO_MATCH / REVIEW / NEW_TERM

설계서: docs/design/GLOSSARY_SIMILARITY_MATCHING_DESIGN.md
Created: 2026-03-10
Extracted from: oreo-ecosystem (application/services/knowledge/enhanced_similarity_matcher.py)
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from src.config_weights import weights as _w
from src.nlp.term_normalizer import TermNormalizer
from src.nlp.lexical_scorer import LexicalScorer

logger = logging.getLogger(__name__)

# =============================================================================
# Decision Policy Thresholds (Phase 0 분포 분석 후 조정)
# =============================================================================
AUTO_MATCH_THRESHOLD = _w.similarity.auto_match
REVIEW_THRESHOLD = _w.similarity.review

# 한국어 조사 패턴
_PARTICLES_LONG = ["에서", "으로", "까지", "부터", "처럼", "같이", "에게", "한테", "보다"]
_PARTICLES_SHORT = ["가", "를", "에", "의", "는", "은", "도", "와", "과", "이", "로", "만", "서"]


def _strip_particles(term: str) -> str:
    """한국어 trailing 조사 제거."""
    changed = True
    while changed:
        changed = False
        for p in _PARTICLES_LONG:
            if term.endswith(p) and len(term) > len(p) + 2:
                term = term[: -len(p)]
                changed = True
                break
        if not changed:
            for p in _PARTICLES_SHORT:
                if term.endswith(p) and len(term) > len(p) + 2:
                    term = term[: -len(p)]
                    changed = True
                    break
    return term


# =============================================================================
# Result Types
# =============================================================================


@dataclass
class MatchDecision:
    """3-zone 매칭 판정 결과."""

    zone: str  # "AUTO_MATCH" | "REVIEW" | "NEW_TERM"
    matched_term: Any | None = None
    score: float = 0.0
    match_type: str = "none"  # exact, synonym, particle, rapidfuzz, sparse, dense, cross_encoder, morpheme
    channel_scores: dict[str, float] = field(default_factory=dict)
    # 복합어 형태소 매칭 시 모든 매칭 결과 (e.g. 방송서비스 -> [방송, 서비스])
    matched_morphemes: list[tuple[str, Any]] = field(default_factory=list)


@dataclass
class _PrecomputedStd:
    """사전 계산된 표준 용어 데이터."""

    term: Any
    normalized: str
    normalized_ko: str
    ngrams: set[str]
    ngrams_ko: set[str]
    # RapidFuzz용: 매칭 대상 텍스트
    match_text: str  # "term term_ko" 결합


# =============================================================================
# Configuration (replaces FeatureFlags from oreo-ecosystem)
# =============================================================================


@dataclass
class EnhancedMatcherConfig:
    """Configuration for EnhancedSimilarityMatcher.

    Replaces oreo-ecosystem FeatureFlags with simple config booleans.
    """
    enable_synonym_expansion: bool = True
    enable_rapidfuzz: bool = True
    enable_dense_search: bool = True
    enable_cross_encoder: bool = True


# =============================================================================
# Main Service
# =============================================================================


class EnhancedSimilarityMatcher:
    """3-Layer 표준 용어 유사도 매칭 서비스.

    기존 TermSimilarityMatcher를 대체하는 enhanced 버전.
    Config flag 기반으로 각 Layer를 독립적으로 활성화/비활성화 가능.
    """

    _MIN_SHARED_NGRAMS = _w.similarity.min_shared_ngrams
    _MAX_CANDIDATES = _w.similarity.max_candidates

    def __init__(
        self,
        jaccard_threshold: float = 0.7,
        levenshtein_threshold: float = 0.8,
        config: EnhancedMatcherConfig | None = None,
    ) -> None:
        self._jaccard_threshold = jaccard_threshold
        self._levenshtein_threshold = levenshtein_threshold
        self._config = config or EnhancedMatcherConfig()
        self._scorer = LexicalScorer()

        # L1: Exact match lookup (both word + term)
        self._normalized_lookup: dict[str, Any] = {}
        # L1.5: Word-only lookup (단어사전 전용, morpheme decomposition용)
        self._word_lookup: dict[str, Any] = {}
        self._all_standard: list[Any] = []
        self._precomputed: list[_PrecomputedStd] = []  # TERM 타입만 (L2 유사도용)

        # L2 Sparse: n-gram 역색인
        self._ngram_index: dict[str, list[int]] = {}

        # L2 Edit: RapidFuzz용 정규화 리스트
        self._rf_choices: list[str] = []
        self._rf_idx_map: list[int] = []  # choices index -> precomputed index

        # L2 Dense: 별도 모듈 (lazy init)
        self._dense_index: Any | None = None
        # On-demand dense: embedding 어댑터 (full index 대신 per-page mini index용)
        self._embedding_adapter: Any | None = None

        # L3: Cross-Encoder (lazy init)
        self._cross_encoder: Any | None = None

        # Graceful degradation (설계서 4.3.2)
        self._force_disable_ce: bool = False

        self._loaded = False

    # =========================================================================
    # Initialization
    # =========================================================================

    def load_standard_terms(
        self,
        terms: list[Any],
        *,
        get_term_type: Any = None,
    ) -> None:
        """표준 용어 로드 + 사전 계산.

        Args:
            terms: 표준 용어 리스트. 각 항목은 .term, .term_ko, .synonyms,
                   .abbreviations, .physical_meaning, .term_type 속성 필요.
            get_term_type: Optional callable(term) -> str to determine WORD vs TERM.
                          If None, uses term.term_type attribute.
        """
        if self._loaded:
            return

        self._all_standard = terms
        self._normalized_lookup = {}
        self._word_lookup = {}
        self._precomputed = []
        self._ngram_index = {}
        self._rf_choices = []
        self._rf_idx_map = []

        collision_count = 0
        word_count = 0
        term_idx = 0  # precomputed index (TERM 타입만)

        for t in terms:
            normalized = TermNormalizer.normalize_for_comparison(t.term)
            normalized_ko = ""
            if t.term_ko:
                normalized_ko = TermNormalizer.normalize_for_comparison(t.term_ko)

            # Determine if WORD type
            if get_term_type is not None:
                is_word = get_term_type(t) == "WORD"
            else:
                is_word = getattr(t, 'term_type', None) == "WORD"

            # L1: 기본 lookup (양쪽 모두)
            if normalized:
                self._normalized_lookup[normalized] = t
            if normalized_ko:
                self._normalized_lookup.setdefault(normalized_ko, t)

            # L1 확장: synonyms, abbreviations, physical_meaning
            if self._config.enable_synonym_expansion:
                for syn in getattr(t, 'synonyms', []):
                    syn_norm = TermNormalizer.normalize_for_comparison(syn)
                    if syn_norm:
                        if syn_norm in self._normalized_lookup:
                            collision_count += 1
                        else:
                            self._normalized_lookup[syn_norm] = t

                for abbr in getattr(t, 'abbreviations', []):
                    abbr_norm = TermNormalizer.normalize_for_comparison(abbr)
                    if abbr_norm:
                        if abbr_norm in self._normalized_lookup:
                            collision_count += 1
                        else:
                            self._normalized_lookup[abbr_norm] = t

                pm = getattr(t, 'physical_meaning', None)
                if pm:
                    pm_norm = TermNormalizer.normalize_for_comparison(pm)
                    if pm_norm:
                        if pm_norm in self._normalized_lookup:
                            collision_count += 1
                        else:
                            self._normalized_lookup[pm_norm] = t

            if is_word:
                # WORD: _word_lookup에만 추가 (L1.5 morpheme용)
                word_count += 1
                if normalized:
                    self._word_lookup[normalized] = t
                if normalized_ko:
                    self._word_lookup.setdefault(normalized_ko, t)
                continue  # WORD는 L2 precomputed/ngram/rf에 추가하지 않음

            # TERM: L2 유사도 매칭용 사전 계산
            ngrams = self._scorer._ngrams(normalized)
            ngrams_ko = self._scorer._ngrams(normalized_ko) if normalized_ko else set()

            match_text = t.term
            if t.term_ko:
                match_text += " " + t.term_ko

            self._precomputed.append(_PrecomputedStd(
                term=t,
                normalized=normalized,
                normalized_ko=normalized_ko,
                ngrams=ngrams,
                ngrams_ko=ngrams_ko,
                match_text=match_text,
            ))

            # L2 Sparse: n-gram 역색인
            for ng in ngrams | ngrams_ko:
                if ng not in self._ngram_index:
                    self._ngram_index[ng] = []
                self._ngram_index[ng].append(term_idx)

            # L2 Edit: RapidFuzz 리스트
            if self._config.enable_rapidfuzz:
                self._rf_choices.append(normalized)
                self._rf_idx_map.append(term_idx)
                if normalized_ko:
                    self._rf_choices.append(normalized_ko)
                    self._rf_idx_map.append(term_idx)

            term_idx += 1

        self._loaded = True
        logger.info(
            "EnhancedSimilarityMatcher loaded: %d terms (%d words, %d terms), "
            "%d lookup entries, %d word_lookup entries, "
            "%d ngram keys, %d rf_choices, %d collisions",
            len(terms),
            word_count,
            len(self._precomputed),
            len(self._normalized_lookup),
            len(self._word_lookup),
            len(self._ngram_index),
            len(self._rf_choices),
            collision_count,
        )

    # =========================================================================
    # Layer 1: Normalization + Exact Match
    # =========================================================================

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
            match_type = "exact"
            if self._config.enable_synonym_expansion:
                # 동의어/약어 매칭인지 구분
                t_norm = TermNormalizer.normalize_for_comparison(t.term)
                t_ko_norm = TermNormalizer.normalize_for_comparison(t.term_ko) if t.term_ko else ""
                if normalized != t_norm and normalized != t_ko_norm:
                    match_type = "synonym"
            return MatchDecision(
                zone="AUTO_MATCH",
                matched_term=t,
                score=1.0,
                match_type=match_type,
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

    def _l1_5_morpheme_decompose(self, term: Any) -> list[tuple[str, Any]]:
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
            from src.nlp.morpheme_analyzer import get_analyzer

            analyzer = get_analyzer()
            if not analyzer.is_available:
                return []
            result = analyzer.analyze(candidate_ko)
        except Exception:
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

    # =========================================================================
    # Layer 2: Multi-Channel Candidate Retrieval
    # =========================================================================

    def _l2_rapidfuzz(
        self, candidate: str, top_k: int = 50, score_cutoff: int = _w.similarity.rapidfuzz_score_cutoff
    ) -> list[tuple[int, float]]:
        """L2 S_edit: RapidFuzz 문자열 유사도."""
        if not self._config.enable_rapidfuzz or not self._rf_choices:
            return []

        try:
            from rapidfuzz import fuzz, process
        except ImportError:
            logger.warning("rapidfuzz not installed, skipping S_edit channel")
            return []

        normalized = TermNormalizer.normalize_for_comparison(candidate)
        if not normalized:
            return []

        # WRatio: 길이 차이, 부분 매칭 자동 보정
        results = process.extract(
            normalized,
            self._rf_choices,
            scorer=fuzz.WRatio,
            score_cutoff=score_cutoff,
            limit=top_k,
        )

        # dedup: 같은 precomputed index가 여러번 나올 수 있음 (term + term_ko)
        seen: dict[int, float] = {}
        query_len = len(normalized)
        for match_str, score, choice_idx in results:
            pc_idx = self._rf_idx_map[choice_idx]
            score_normalized = score / 100.0

            # Length ratio penalty: WRatio partial matching이 짧은 표준 용어를
            # 긴 PENDING 용어에 높은 점수로 매칭하는 false positive 방지
            # e.g., "시"(1글자)가 "본부시스템"(5글자)에 90% 매칭되는 문제
            matched_choice = self._rf_choices[choice_idx]
            matched_len = len(matched_choice) if matched_choice else 0
            if matched_len > 0 and query_len > 0:
                length_ratio = min(matched_len, query_len) / max(matched_len, query_len)
                if length_ratio < _w.similarity.rapidfuzz_length_ratio_min:
                    score_normalized *= max(length_ratio, _w.similarity.rapidfuzz_length_ratio_floor)

            if pc_idx not in seen or score_normalized > seen[pc_idx]:
                seen[pc_idx] = score_normalized

        return sorted(seen.items(), key=lambda x: x[1], reverse=True)[:top_k]

    def _l2_sparse(
        self, candidate: str, top_k: int = 50, jaccard_threshold: float | None = None
    ) -> list[tuple[int, float]]:
        """L2 S_sparse: n-gram Jaccard (역색인 사용)."""
        normalized = TermNormalizer.normalize_for_comparison(candidate)
        if not normalized:
            return []

        candidate_ngrams = self._scorer._ngrams(normalized)
        if not candidate_ngrams:
            return []

        # 역색인으로 후보 수집
        hit_counts: dict[int, int] = {}
        for ng in candidate_ngrams:
            for idx in self._ngram_index.get(ng, []):
                hit_counts[idx] = hit_counts.get(idx, 0) + 1

        filtered = [
            (idx, cnt) for idx, cnt in hit_counts.items()
            if cnt >= self._MIN_SHARED_NGRAMS
        ]
        filtered.sort(key=lambda x: x[1], reverse=True)
        candidate_indices = [idx for idx, _ in filtered[:self._MAX_CANDIDATES]]

        # Jaccard 계산
        results: list[tuple[int, float]] = []
        for idx in candidate_indices:
            pc = self._precomputed[idx]
            jaccard = self._jaccard_from_sets(candidate_ngrams, pc.ngrams)
            if pc.ngrams_ko:
                jaccard_ko = self._jaccard_from_sets(candidate_ngrams, pc.ngrams_ko)
                jaccard = max(jaccard, jaccard_ko)
            effective_threshold = jaccard_threshold if jaccard_threshold is not None else self._jaccard_threshold
            if jaccard >= effective_threshold:
                results.append((idx, jaccard))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def _l2_fuse(
        self,
        edit_results: list[tuple[int, float]],
        sparse_results: list[tuple[int, float]],
        dense_results: list[tuple[int, float]],
        top_k: int = 50,
    ) -> list[tuple[int, float]]:
        """RRF Fusion (lightweight inline -- Document 변환 없이 직접 계산)."""
        k = _w.similarity.rrf_k
        scores: dict[int, float] = {}

        # 채널별 가중치
        has_dense = len(dense_results) > 0
        if has_dense:
            channel_weights = [
                _w.similarity.rrf_edit_weight,
                _w.similarity.rrf_sparse_weight,
                _w.similarity.rrf_dense_weight,
            ]  # edit, sparse, dense
        else:
            # Normalize edit+sparse weights to sum=1.0 when dense unavailable
            _sum = _w.similarity.rrf_edit_weight + _w.similarity.rrf_sparse_weight
            channel_weights = [
                _w.similarity.rrf_edit_weight / _sum if _sum > 0 else 0.5,
                _w.similarity.rrf_sparse_weight / _sum if _sum > 0 else 0.5,
            ]

        channels = [edit_results, sparse_results]
        if has_dense:
            channels.append(dense_results)

        for weight, channel in zip(channel_weights, channels):
            for rank, (idx, _score) in enumerate(channel, start=1):
                rrf = weight * (1.0 / (k + rank))
                scores[idx] = scores.get(idx, 0.0) + rrf

        sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_items[:top_k]

    # =========================================================================
    # Layer 3: Cross-Encoder Precision Judgment
    # =========================================================================

    async def _l3_cross_encoder_score(
        self, candidate_text: str, standard_idx: int
    ) -> float | None:
        """L3: Cross-Encoder 점수 계산 (단일 쌍)."""
        if not self._config.enable_cross_encoder:
            return None

        if self._cross_encoder is None:
            return None

        if self._cross_encoder.model is None:
            return None

        pc = self._precomputed[standard_idx]
        standard_text = pc.term.term
        if pc.term.term_ko:
            standard_text += " " + pc.term.term_ko
        if getattr(pc.term, 'definition', None):
            standard_text += " " + pc.term.definition[:100]

        try:
            pairs = [[candidate_text, standard_text]]
            raw_scores = await asyncio.to_thread(
                self._cross_encoder.model.predict,
                pairs,
                batch_size=1,
                show_progress_bar=False,
            )
            raw_score = float(raw_scores[0])
            # sigmoid(s/3) 정규화
            return 1 / (1 + math.exp(-raw_score / 3))
        except Exception as e:
            logger.warning("CrossEncoder scoring failed: %s", e)
            return None

    async def _l3_cross_encoder_batch(
        self, candidate_text: str, top_indices: list[int], top_k: int = 10
    ) -> list[tuple[int, float]]:
        """L3: Cross-Encoder 배치 rerank."""
        if not self._config.enable_cross_encoder:
            return []

        if self._cross_encoder is None:
            return []

        if self._cross_encoder.model is None:
            return []

        pairs = []
        for idx in top_indices:
            pc = self._precomputed[idx]
            std_text = pc.term.term
            if pc.term.term_ko:
                std_text += " " + pc.term.term_ko
            if getattr(pc.term, 'definition', None):
                std_text += " " + pc.term.definition[:100]
            pairs.append([candidate_text, std_text])

        try:
            raw_scores = await asyncio.to_thread(
                self._cross_encoder.model.predict,
                pairs,
                batch_size=32,
                show_progress_bar=False,
            )
            results = []
            for i, raw_score in enumerate(raw_scores.tolist()):
                normalized = 1 / (1 + math.exp(-raw_score / 3))
                results.append((top_indices[i], normalized))
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:top_k]
        except Exception as e:
            logger.warning("CrossEncoder batch rerank failed: %s", e)
            return []

    # =========================================================================
    # Decision Policy
    # =========================================================================

    @staticmethod
    def _decide_zone(score: float) -> str:
        """3-zone 판정."""
        if score >= AUTO_MATCH_THRESHOLD:
            return "AUTO_MATCH"
        elif score >= REVIEW_THRESHOLD:
            return "REVIEW"
        return "NEW_TERM"

    # =========================================================================
    # Public API
    # =========================================================================

    async def match_enhanced(self, term: Any) -> MatchDecision:
        """3-Layer 파이프라인 매칭 (단일 용어).

        L1 -> L2 -> L3 순서로 실행. 각 Layer에서 확정 시 조기 반환.
        """
        return await self._match_single(term)

    async def _match_single(
        self,
        term: Any,
        ce_top_k: int = 50,
        dense_override: list[tuple[int, float]] | None = None,
    ) -> MatchDecision:
        """내부 매칭 구현 (배치 최적화 파라미터 지원).

        Args:
            term: 매칭 대상 용어 (.term, .term_ko, .definition 속성 필요)
            ce_top_k: Cross-Encoder에 보낼 후보 수 (graceful degradation)
            dense_override: 배치에서 사전 계산된 dense 결과 (배치 최적화)
        """
        if not self._loaded or not self._normalized_lookup:
            return MatchDecision(zone="NEW_TERM")

        # --- L1: Exact Match ---
        for candidate_str in [term.term, getattr(term, 'term_ko', None)]:
            if not candidate_str:
                continue
            l1 = self._l1_exact_match(candidate_str)
            if l1 is not None:
                return l1

        # --- L1.5: Morpheme Decomposition (enrichment, no early exit) ---
        morpheme_info = self._l1_5_morpheme_decompose(term)

        # --- L2: Multi-Channel Retrieval ---
        query_text = term.term
        term_ko = getattr(term, 'term_ko', None)
        if term_ko:
            query_text += " " + term_ko

        # S_edit (RapidFuzz)
        edit_results = self._l2_rapidfuzz(term.term)
        if term_ko:
            edit_ko = self._l2_rapidfuzz(term_ko)
            merged: dict[int, float] = {}
            for idx, score in edit_results + edit_ko:
                if idx not in merged or score > merged[idx]:
                    merged[idx] = score
            edit_results = sorted(merged.items(), key=lambda x: x[1], reverse=True)[:50]

        # S_sparse (n-gram Jaccard)
        sparse_results = self._l2_sparse(term.term)
        if term_ko:
            sparse_ko = self._l2_sparse(term_ko)
            merged_sp: dict[int, float] = {}
            for idx, score in sparse_results + sparse_ko:
                if idx not in merged_sp or score > merged_sp[idx]:
                    merged_sp[idx] = score
            sparse_results = sorted(merged_sp.items(), key=lambda x: x[1], reverse=True)[:50]

        # S_dense (BGE-M3 ONNX) -- config flag 기반
        dense_results: list[tuple[int, float]] = []
        if dense_override is not None:
            dense_results = dense_override
        elif self._config.enable_dense_search and self._dense_index is not None:
            dense_results = self._dense_index.search(query_text, top_k=50)

        # RRF Fusion
        fused = self._l2_fuse(edit_results, sparse_results, dense_results, top_k=50)

        if not fused:
            return MatchDecision(zone="NEW_TERM", matched_morphemes=morpheme_info)

        # --- L3: Cross-Encoder Rerank (config flag + graceful degradation) ---
        use_ce = self._config.enable_cross_encoder and not self._force_disable_ce
        if use_ce:
            ce_query = term.term
            if term_ko:
                ce_query += " " + term_ko
            definition = getattr(term, 'definition', None)
            if definition:
                ce_query += " " + definition[:100]

            ce_indices = [idx for idx, _ in fused[:ce_top_k]]
            ce_results = await self._l3_cross_encoder_batch(ce_query, ce_indices, top_k=10)

            if ce_results:
                best_idx, best_score = ce_results[0]
                pc = self._precomputed[best_idx]
                zone = self._decide_zone(best_score)

                ch_scores = self._collect_channel_scores(
                    best_idx, edit_results, sparse_results, dense_results
                )
                ch_scores["cross_encoder"] = best_score

                return MatchDecision(
                    zone=zone,
                    matched_term=pc.term if zone != "NEW_TERM" else None,
                    score=best_score,
                    match_type="cross_encoder",
                    channel_scores=ch_scores,
                    matched_morphemes=morpheme_info,
                )

        # L3 비활성 또는 실패 시: RRF 점수 기반 판정
        best_idx, best_rrf = fused[0]
        pc = self._precomputed[best_idx]

        ch_scores = self._collect_channel_scores(
            best_idx, edit_results, sparse_results, dense_results
        )
        max_channel_score = max(ch_scores.values()) if ch_scores else 0.0

        # Cross-Encoder 없이는 보수적 판정 — SSOT: config_weights.SimilarityThresholds
        if max_channel_score >= _w.similarity.fallback_auto_match:
            zone = "AUTO_MATCH"
        elif max_channel_score >= _w.similarity.fallback_review:
            zone = "REVIEW"
        else:
            zone = "NEW_TERM"

        # 길이 비율 가드: 짧은 표준 용어가 긴 PENDING에 AUTO_MATCH되는 것 방지
        # RapidFuzz 페널티를 우회하는 채널(Jaccard/Dense)에서 나온 경우도 차단
        if zone == "AUTO_MATCH":
            pending_text = term.term or ""
            std_ko = getattr(pc.term, 'term_ko', '') or ""
            if std_ko and pending_text:
                ratio = min(len(std_ko), len(pending_text)) / max(len(std_ko), len(pending_text))
                if ratio < 0.5:
                    zone = "REVIEW"

        return MatchDecision(
            zone=zone,
            matched_term=pc.term if zone != "NEW_TERM" else None,
            score=max_channel_score,
            match_type="rrf_fusion",
            channel_scores=ch_scores,
            matched_morphemes=morpheme_info,
        )

    @staticmethod
    def _collect_channel_scores(
        target_idx: int,
        edit_results: list[tuple[int, float]],
        sparse_results: list[tuple[int, float]],
        dense_results: list[tuple[int, float]],
    ) -> dict[str, float]:
        """best match의 채널별 점수 수집."""
        ch: dict[str, float] = {}
        for idx, s in edit_results:
            if idx == target_idx:
                ch["s_edit"] = s
                break
        for idx, s in sparse_results:
            if idx == target_idx:
                ch["s_sparse"] = s
                break
        for idx, s in dense_results:
            if idx == target_idx:
                ch["s_dense"] = s
                break
        return ch

    async def match_batch(
        self, terms: list[Any], *, disable_cross_encoder: bool = False,
    ) -> list[MatchDecision]:
        """배치 매칭 (PENDING 전체).

        Graceful Degradation (설계서 4.3.2):
        - PENDING <= 3,000건: 전체 파이프라인 (L1->L2->L3)
        - 3,000 < PENDING <= 10,000건: L3 Cross-Encoder Top-K를 10으로 축소
        - PENDING > 10,000건: L3 비활성, L2 RRF 점수로 직접 판정

        Args:
            terms: 매칭할 용어 리스트 (.term, .term_ko 속성 필요)
            disable_cross_encoder: True이면 L3 Cross-Encoder를 강제 비활성화
        """
        # 외부에서 CE 비활성화 요청 시 즉시 적용
        if disable_cross_encoder:
            self._force_disable_ce = True
            ce_top_k = 0
        else:
            # Graceful degradation 설정
            pending_count = len(terms)
            ce_top_k = 50

            if pending_count > _w.similarity.reduced_ce_max_terms:
                logger.warning(
                    "Large batch (%d), disabling Cross-Encoder for timeout protection",
                    pending_count,
                )
                self._force_disable_ce = True
            elif pending_count > _w.similarity.full_pipeline_max_terms:
                logger.info(
                    "Medium batch (%d), reducing Cross-Encoder Top-K to 10",
                    pending_count,
                )
                ce_top_k = 10
                self._force_disable_ce = False
            else:
                self._force_disable_ce = False

        # Dense 배치 최적화 (설계서 4.3.1)
        dense_batch_results: dict[int, list[tuple[int, float]]] | None = None

        # L1 미매칭 PENDING 수집 (dense용)
        l1_unmatched_indices: list[int] = []
        l1_unmatched_texts: list[str] = []
        for i, term in enumerate(terms):
            l1 = None
            for candidate_str in [term.term, getattr(term, 'term_ko', None)]:
                if candidate_str:
                    l1 = self._l1_exact_match(candidate_str)
                    if l1:
                        break
            if l1 is None:
                query = term.term
                term_ko = getattr(term, 'term_ko', None)
                if term_ko:
                    query += " " + term_ko
                l1_unmatched_indices.append(i)
                l1_unmatched_texts.append(query)

        if self._config.enable_dense_search and l1_unmatched_texts:
            if self._dense_index is not None:
                # 기존 방식: 사전 구축된 full dense index 사용
                batch_dense = self._dense_index.search_batch(
                    l1_unmatched_texts, top_k=50
                )
                dense_batch_results = {}
                for j, idx in enumerate(l1_unmatched_indices):
                    dense_batch_results[idx] = batch_dense[j]

            elif self._embedding_adapter is not None:
                # On-demand mini dense: edit/sparse 후보만 임베딩
                dense_batch_results = self._build_mini_dense(
                    terms, l1_unmatched_indices, l1_unmatched_texts,
                )

        results: list[MatchDecision] = []
        for i, term in enumerate(terms):
            # 배치 dense 결과 주입
            override_dense = dense_batch_results.get(i) if dense_batch_results else None
            decision = await self._match_single(term, ce_top_k=ce_top_k, dense_override=override_dense)
            results.append(decision)

        # cleanup
        self._force_disable_ce = False
        return results

    def _build_mini_dense(
        self,
        terms: list[Any],
        l1_unmatched_indices: list[int],
        l1_unmatched_texts: list[str],
    ) -> dict[int, list[tuple[int, float]]] | None:
        """On-demand mini dense: edit/sparse 후보 standard terms만 임베딩.

        54K 전체 표준 용어를 임베딩하는 대신,
        edit/sparse 채널에서 이미 찾은 후보 (~500-2000개)만 mini index로 구축.
        50 pending x (edit top-50 + sparse top-50) 후보 -> 중복 제거 -> 1000-3000개.
        batch_size=50 기준 20-60 배치 = 수초 내 완료.
        """
        if not self._embedding_adapter:
            return None

        try:
            from rapidfuzz import fuzz, process as rf_process
        except ImportError:
            return None

        # Phase 1: L1-unmatched 용어의 edit/sparse 후보 indices 수집
        all_candidate_indices: set[int] = set()
        for i in l1_unmatched_indices:
            term = terms[i]
            # Quick edit candidate collection (top-10 per term for speed)
            for search_str in [term.term, getattr(term, 'term_ko', None)]:
                if search_str and self._config.enable_rapidfuzz and self._rf_choices:
                    normalized = TermNormalizer.normalize_for_comparison(search_str)
                    if normalized:
                        try:
                            results = rf_process.extract(
                                normalized, self._rf_choices,
                                scorer=fuzz.WRatio, score_cutoff=60, limit=10,
                            )
                            for _, _, choice_idx in results:
                                all_candidate_indices.add(self._rf_idx_map[choice_idx])
                        except Exception:
                            pass

            # Quick sparse candidate collection (top-10 per term for speed)
            for search_str in [term.term, getattr(term, 'term_ko', None)]:
                if search_str:
                    sparse_res = self._l2_sparse(search_str, top_k=10)
                    for idx, _ in sparse_res:
                        all_candidate_indices.add(idx)

        if not all_candidate_indices:
            return None

        # Phase 2: 후보 standard terms만으로 mini dense index 구축
        candidate_list = sorted(all_candidate_indices)
        mini_pcs = [self._precomputed[idx] for idx in candidate_list]

        logger.info(
            "Building mini dense index: %d candidates from edit/sparse (vs %d total standard)",
            len(candidate_list), len(self._precomputed),
        )

        try:
            from src.search.dense_term_index import DenseTermIndex

            mini_index = DenseTermIndex(self._embedding_adapter)
            mini_index.build(mini_pcs, batch_size=50)

            if not mini_index.is_ready:
                logger.warning("Mini dense index build failed (not ready)")
                return None

            # Phase 3: pending terms를 mini index에서 검색
            batch_results = mini_index.search_batch(
                l1_unmatched_texts, top_k=50, batch_size=50,
            )

            # Index remap: mini index (0-based) -> original precomputed index
            dense_batch_results: dict[int, list[tuple[int, float]]] = {}
            for j, orig_term_idx in enumerate(l1_unmatched_indices):
                remapped = [
                    (candidate_list[mini_idx], score)
                    for mini_idx, score in batch_results[j]
                ]
                dense_batch_results[orig_term_idx] = remapped

            logger.info(
                "Mini dense search complete: %d pending terms, %d candidate terms",
                len(l1_unmatched_texts), len(candidate_list),
            )
            return dense_batch_results

        except Exception as e:
            logger.warning("Mini dense index build/search failed: %s", e)
            return None

    def init_dense_index(self, provider: Any) -> None:
        """Dense 인덱스 초기화 (외부에서 주입)."""
        if not self._config.enable_dense_search:
            return
        try:
            from src.search.dense_term_index import DenseTermIndex
            self._dense_index = DenseTermIndex(provider)
            self._dense_index.build(self._precomputed)
            logger.info("Dense term index built: %d terms", len(self._precomputed))
        except Exception as e:
            logger.warning("Dense index init failed: %s", e)
            self._dense_index = None

    def set_cross_encoder(self, cross_encoder: Any) -> None:
        """Cross-Encoder 모델 외부 주입."""
        self._cross_encoder = cross_encoder

    def set_embedding_adapter(self, adapter: Any) -> None:
        """Embedding 어댑터 외부 주입 (on-demand mini dense용)."""
        self._embedding_adapter = adapter

    # =========================================================================
    # Utility
    # =========================================================================

    @staticmethod
    def _jaccard_from_sets(a: set[str], b: set[str]) -> float:
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        intersection = len(a & b)
        union = len(a | b)
        return intersection / union if union > 0 else 0.0
