"""Enhanced Term Similarity Matcher -- 3-Layer cascade matching.

3-Layer 전략 설계 근거:

    L1  (Exact)     → 정규화 + 동의어 사전 매칭. O(1). 확실한 건 바로 결정.
    L1.5 (Morpheme) → 한국어 복합어 분해 ("결제수단종류"→["결제","수단","종류"]).
                      매칭 결정 없이 메타데이터만 수집 → L2/L3에 전달.
    L2  (Multi-Ch)  → 3채널 병렬 검색 + RRF 퓨전
                      - S_edit  (RapidFuzz WRatio, 가중치 0.25)
                      - S_sparse (N-gram Jaccard, 가중치 0.25)
                      - S_dense  (임베딩 코사인, 가중치 0.50)
                      → RRF k=60 으로 순위 퓨전.
    L3  (CE)        → Cross-encoder 정밀 판정. top-K를 sigmoid(score/3) 정규화.

Decision zone (3구역):
    AUTO_MATCH: CE ≥ 0.85 (fallback ≥ 0.90) → 자동 매칭
    REVIEW:     CE ≥ 0.50 (fallback ≥ 0.60) → 수동 검토
    NEW_TERM:   나머지 → 신규 용어

Fallback 임계값이 더 높은 이유: CE 없이 RRF 점수만으로는 확신이 낮으므로.

Graceful degradation:
    ≤ 3,000 용어 → full pipeline (CE top-50)
    ≤ 10,000     → reduced (CE top-10)
    > 10,000     → CE 비활성화, RRF fallback only
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from typing import Any

from src.config.weights import weights as _w
from src.nlp.korean.term_normalizer import TermNormalizer
from src.nlp.korean.lexical_scorer import LexicalScorer

from .strategies import EnhancedMatcherConfig, GlossaryTermLike, MatchDecision, _PrecomputedStd
from .utils import AUTO_MATCH_THRESHOLD, REVIEW_THRESHOLD, _strip_particles

logger = logging.getLogger(__name__)


from ._batch import BatchMatchMixin


class EnhancedSimilarityMatcher(BatchMatchMixin):
    """3-Layer 표준 용어 유사도 매칭 서비스.

    기존 TermSimilarityMatcher를 대체하는 enhanced 버전.
    Config flag 기반으로 각 Layer를 독립적으로 활성화/비활성화 가능.
    """

    _MIN_SHARED_NGRAMS = _w.similarity.min_shared_ngrams
    _MAX_CANDIDATES = _w.similarity.max_candidates

    def __init__(
        self,
        jaccard_threshold: float = _w.similarity.jaccard_threshold,
        levenshtein_threshold: float = _w.similarity.levenshtein_threshold,
        config: EnhancedMatcherConfig | None = None,
    ) -> None:
        self._jaccard_threshold = jaccard_threshold
        self._levenshtein_threshold = levenshtein_threshold
        self._config = config or EnhancedMatcherConfig()
        self._scorer = LexicalScorer()

        # L1: Exact match lookup (both word + term)
        self._normalized_lookup: dict[str, GlossaryTermLike] = {}
        # L1.5: Word-only lookup (단어사전 전용, morpheme decomposition용)
        self._word_lookup: dict[str, GlossaryTermLike] = {}
        self._all_standard: list[GlossaryTermLike] = []
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

    def _register_l1_lookups(
        self, t: GlossaryTermLike, normalized: str, normalized_ko: str,
    ) -> int:
        """Register L1 exact/synonym lookups for a term. Returns collision count."""
        collisions = 0
        if normalized:
            self._normalized_lookup[normalized] = t
        if normalized_ko:
            self._normalized_lookup.setdefault(normalized_ko, t)

        if not self._config.enable_synonym_expansion:
            return collisions

        alias_sources = [
            *getattr(t, 'synonyms', []),
            *getattr(t, 'abbreviations', []),
        ]
        pm = getattr(t, 'physical_meaning', None)
        if pm:
            alias_sources.append(pm)

        for alias in alias_sources:
            alias_norm = TermNormalizer.normalize_for_comparison(alias)
            if alias_norm:
                if alias_norm in self._normalized_lookup:
                    collisions += 1
                else:
                    self._normalized_lookup[alias_norm] = t

        return collisions

    def _register_term_for_l2(
        self, t: GlossaryTermLike, normalized: str, normalized_ko: str, term_idx: int,
    ) -> None:
        """Register a TERM-type entry for L2 similarity matching."""
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

        for ng in ngrams | ngrams_ko:
            if ng not in self._ngram_index:
                self._ngram_index[ng] = []
            self._ngram_index[ng].append(term_idx)

        if self._config.enable_rapidfuzz:
            self._rf_choices.append(normalized)
            self._rf_idx_map.append(term_idx)
            if normalized_ko:
                self._rf_choices.append(normalized_ko)
                self._rf_idx_map.append(term_idx)

    def load_standard_terms(
        self,
        terms: list[GlossaryTermLike],
        *,
        get_term_type: Callable[[GlossaryTermLike], str] | None = None,
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
        term_idx = 0

        for t in terms:
            normalized = TermNormalizer.normalize_for_comparison(t.term)
            normalized_ko = ""
            if t.term_ko:
                normalized_ko = TermNormalizer.normalize_for_comparison(t.term_ko)

            if get_term_type is not None:
                is_word = get_term_type(t) == "WORD"
            else:
                is_word = getattr(t, 'term_type', None) == "WORD"

            collision_count += self._register_l1_lookups(t, normalized, normalized_ko)

            if is_word:
                word_count += 1
                if normalized:
                    self._word_lookup[normalized] = t
                if normalized_ko:
                    self._word_lookup.setdefault(normalized_ko, t)
                continue

            self._register_term_for_l2(t, normalized, normalized_ko, term_idx)
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

    # =========================================================================
    # Layer 2: Multi-Channel Candidate Retrieval
    # =========================================================================

    @staticmethod
    def _apply_length_penalty(score: float, query_len: int, matched_choice: str) -> float:
        """Apply length ratio penalty to prevent short-term false positives."""
        matched_len = len(matched_choice) if matched_choice else 0
        if matched_len > 0 and query_len > 0:
            length_ratio = min(matched_len, query_len) / max(matched_len, query_len)
            if length_ratio < _w.similarity.rapidfuzz_length_ratio_min:
                score *= max(length_ratio, _w.similarity.rapidfuzz_length_ratio_floor)
        return score

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
            score_normalized = self._apply_length_penalty(
                score / 100.0, query_len, self._rf_choices[choice_idx],
            )
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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

    async def match_enhanced(self, term: GlossaryTermLike) -> MatchDecision:
        """3-Layer 파이프라인 매칭 (단일 용어).

        L1 -> L2 -> L3 순서로 실행. 각 Layer에서 확정 시 조기 반환.
        """
        return await self._match_single(term)

    @staticmethod
    def _merge_channel_results(
        primary: list[tuple[int, float]],
        secondary: list[tuple[int, float]],
        top_k: int = 50,
    ) -> list[tuple[int, float]]:
        """Merge two channel result lists, keeping max score per index."""
        merged: dict[int, float] = {}
        for idx, score in primary + secondary:
            if idx not in merged or score > merged[idx]:
                merged[idx] = score
        return sorted(merged.items(), key=lambda x: x[1], reverse=True)[:top_k]

    def _l2_retrieve_all_channels(
        self,
        term: GlossaryTermLike,
        dense_override: list[tuple[int, float]] | None,
    ) -> tuple[list[tuple[int, float]], list[tuple[int, float]], list[tuple[int, float]], str]:
        """Run all L2 retrieval channels. Returns (edit, sparse, dense, query_text)."""
        term_ko = getattr(term, 'term_ko', None)
        query_text = term.term + (" " + term_ko if term_ko else "")

        edit_results = self._l2_rapidfuzz(term.term)
        if term_ko:
            edit_results = self._merge_channel_results(edit_results, self._l2_rapidfuzz(term_ko))

        sparse_results = self._l2_sparse(term.term)
        if term_ko:
            sparse_results = self._merge_channel_results(
                sparse_results, self._l2_sparse(term_ko),
            )

        dense_results: list[tuple[int, float]] = []
        if dense_override is not None:
            dense_results = dense_override
        elif self._config.enable_dense_search and self._dense_index is not None:
            dense_results = self._dense_index.search(query_text, top_k=50)

        return edit_results, sparse_results, dense_results, query_text

    async def _l3_rerank_fused(
        self,
        term: GlossaryTermLike,
        fused: list[tuple[int, float]],
        edit_results: list[tuple[int, float]],
        sparse_results: list[tuple[int, float]],
        dense_results: list[tuple[int, float]],
        morpheme_info: list[tuple[str, Any]],
        ce_top_k: int,
    ) -> MatchDecision | None:
        """Try L3 cross-encoder reranking. Returns None if unavailable."""
        if not (self._config.enable_cross_encoder and not self._force_disable_ce):
            return None

        term_ko = getattr(term, 'term_ko', None)
        ce_query = term.term
        if term_ko:
            ce_query += " " + term_ko
        definition = getattr(term, 'definition', None)
        if definition:
            ce_query += " " + definition[:100]

        ce_indices = [idx for idx, _ in fused[:ce_top_k]]
        ce_results = await self._l3_cross_encoder_batch(ce_query, ce_indices, top_k=10)
        if not ce_results:
            return None

        best_idx, best_score = ce_results[0]
        pc = self._precomputed[best_idx]
        zone = self._decide_zone(best_score)

        ch_scores = self._collect_channel_scores(best_idx, edit_results, sparse_results, dense_results)
        ch_scores["cross_encoder"] = best_score

        return MatchDecision(
            zone=zone,
            matched_term=pc.term if zone != "NEW_TERM" else None,
            score=best_score,
            match_type="cross_encoder",
            channel_scores=ch_scores,
            matched_morphemes=morpheme_info,
        )

    def _decide_zone_from_rrf(
        self,
        term: GlossaryTermLike,
        fused: list[tuple[int, float]],
        edit_results: list[tuple[int, float]],
        sparse_results: list[tuple[int, float]],
        dense_results: list[tuple[int, float]],
        morpheme_info: list[tuple[str, Any]],
    ) -> MatchDecision:
        """Fallback zone decision from RRF scores (no cross-encoder)."""
        best_idx, _best_rrf = fused[0]
        pc = self._precomputed[best_idx]

        ch_scores = self._collect_channel_scores(best_idx, edit_results, sparse_results, dense_results)
        max_channel_score = max(ch_scores.values()) if ch_scores else 0.0

        if max_channel_score >= _w.similarity.fallback_auto_match:
            zone = "AUTO_MATCH"
        elif max_channel_score >= _w.similarity.fallback_review:
            zone = "REVIEW"
        else:
            zone = "NEW_TERM"

        # Length ratio guard
        if zone == "AUTO_MATCH":
            pending_text = term.term or ""
            std_ko = getattr(pc.term, 'term_ko', '') or ""
            if std_ko and pending_text:
                ratio = min(len(std_ko), len(pending_text)) / max(len(std_ko), len(pending_text))
                if ratio < _w.similarity.rapidfuzz_length_ratio_min:
                    zone = "REVIEW"

        return MatchDecision(
            zone=zone,
            matched_term=pc.term if zone != "NEW_TERM" else None,
            score=max_channel_score,
            match_type="rrf_fusion",
            channel_scores=ch_scores,
            matched_morphemes=morpheme_info,
        )

    async def _match_single(
        self,
        term: GlossaryTermLike,
        ce_top_k: int = 50,
        dense_override: list[tuple[int, float]] | None = None,
    ) -> MatchDecision:
        """내부 매칭 구현 (배치 최적화 파라미터 지원)."""
        if not self._loaded or not self._normalized_lookup:
            return MatchDecision(zone="NEW_TERM")

        # L1: Exact Match
        for candidate_str in [term.term, getattr(term, 'term_ko', None)]:
            if not candidate_str:
                continue
            l1 = self._l1_exact_match(candidate_str)
            if l1 is not None:
                return l1

        # L1.5: Morpheme Decomposition (enrichment, no early exit)
        morpheme_info = self._l1_5_morpheme_decompose(term)

        # L2: Multi-Channel Retrieval + RRF Fusion
        edit_results, sparse_results, dense_results, _ = self._l2_retrieve_all_channels(
            term, dense_override,
        )
        fused = self._l2_fuse(edit_results, sparse_results, dense_results, top_k=50)

        if not fused:
            return MatchDecision(zone="NEW_TERM", matched_morphemes=morpheme_info)

        # L3: Cross-Encoder Rerank
        l3_result = await self._l3_rerank_fused(
            term, fused, edit_results, sparse_results, dense_results, morpheme_info, ce_top_k,
        )
        if l3_result is not None:
            return l3_result

        # Fallback: RRF score-based decision
        return self._decide_zone_from_rrf(
            term, fused, edit_results, sparse_results, dense_results, morpheme_info,
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

    def _resolve_ce_config(
        self, terms: list[Any], disable_cross_encoder: bool,
    ) -> int:
        """Determine cross-encoder top-k based on batch size (graceful degradation)."""
        if disable_cross_encoder:
            self._force_disable_ce = True
            return 0

        pending_count = len(terms)
        if pending_count > _w.similarity.reduced_ce_max_terms:
            logger.warning(
                "Large batch (%d), disabling Cross-Encoder for timeout protection",
                pending_count,
            )
            self._force_disable_ce = True
            return 0

        if pending_count > _w.similarity.full_pipeline_max_terms:
            logger.info(
                "Medium batch (%d), reducing Cross-Encoder Top-K to 10",
                pending_count,
            )
            self._force_disable_ce = False
            return 10

        self._force_disable_ce = False
        return 50

    def _collect_l1_unmatched(
        self, terms: list[Any],
    ) -> tuple[list[int], list[str]]:
        """Collect indices and query texts for terms not matched by L1."""
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
        return l1_unmatched_indices, l1_unmatched_texts

    def _prepare_dense_batch(
        self,
        terms: list[Any],
        l1_unmatched_indices: list[int],
        l1_unmatched_texts: list[str],
    ) -> dict[int, list[tuple[int, float]]] | None:
        """Pre-compute dense results for L1-unmatched terms."""
        if not self._config.enable_dense_search or not l1_unmatched_texts:
            return None

        if self._dense_index is not None:
            batch_dense = self._dense_index.search_batch(l1_unmatched_texts, top_k=50)
            return {idx: batch_dense[j] for j, idx in enumerate(l1_unmatched_indices)}

        if self._embedding_adapter is not None:
            return self._build_mini_dense(terms, l1_unmatched_indices, l1_unmatched_texts)

        return None

