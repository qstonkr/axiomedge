"""Enhanced Term Similarity Matcher -- 3-Layer cascade matching.

3-Layer 전략 설계 근거:

    L1  (Exact)     -> 정규화 + 동의어 사전 매칭. O(1). 확실한 건 바로 결정.
    L1.5 (Morpheme) -> 한국어 복합어 분해 ("결제수단종류"->["결제","수단","종류"]).
                      매칭 결정 없이 메타데이터만 수집 -> L2/L3에 전달.
    L2  (Multi-Ch)  -> 3채널 병렬 검색 + RRF 퓨전
                      - S_edit  (RapidFuzz WRatio, 가중치 0.25)
                      - S_sparse (N-gram Jaccard, 가중치 0.25)
                      - S_dense  (임베딩 코사인, 가중치 0.50)
                      -> RRF k=60 으로 순위 퓨전.
    L3  (CE)        -> Cross-encoder 정밀 판정. top-K를 sigmoid(score/3) 정규화.

Decision zone (3구역):
    AUTO_MATCH: CE >= 0.85 (fallback >= 0.90) -> 자동 매칭
    REVIEW:     CE >= 0.50 (fallback >= 0.60) -> 수동 검토
    NEW_TERM:   나머지 -> 신규 용어

Fallback 임계값이 더 높은 이유: CE 없이 RRF 점수만으로는 확신이 낮으므로.

Graceful degradation:
    <= 3,000 용어 -> full pipeline (CE top-50)
    <= 10,000     -> reduced (CE top-10)
    > 10,000      -> CE 비활성화, RRF fallback only

Implementation split:
    _l1_exact.py     -- L1 exact match + L1.5 morpheme decomposition
    _l2_retrieval.py -- L2 multi-channel retrieval + RRF fusion
    _l3_rerank.py    -- L3 cross-encoder + decision policy + batch helpers
    _batch.py        -- Batch matching + dense index setup
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.config.weights import weights as _w
from src.nlp.korean.term_normalizer import TermNormalizer
from src.nlp.korean.lexical_scorer import LexicalScorer

from .strategies import (
    EnhancedMatcherConfig,
    GlossaryTermLike,
    MatchDecision,
    _PrecomputedStd,
)
from ._l1_exact import L1ExactMixin
from ._l2_retrieval import L2RetrievalMixin
from ._l3_rerank import L3RerankMixin
from ._batch import BatchMatchMixin

logger = logging.getLogger(__name__)


class EnhancedSimilarityMatcher(
    L1ExactMixin,
    L2RetrievalMixin,
    L3RerankMixin,
    BatchMatchMixin,
):
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
        self._precomputed: list[_PrecomputedStd] = []

        # L2 Sparse: n-gram 역색인
        self._ngram_index: dict[str, list[int]] = {}

        # L2 Edit: RapidFuzz용 정규화 리스트
        self._rf_choices: list[str] = []
        self._rf_idx_map: list[int] = []

        # L2 Dense: 별도 모듈 (lazy init)
        self._dense_index: Any | None = None
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
        """Register L1 exact/synonym lookups. Returns collision count."""
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
        self, t: GlossaryTermLike, normalized: str,
        normalized_ko: str, term_idx: int,
    ) -> None:
        """Register a TERM-type entry for L2 similarity matching."""
        ngrams = self._scorer._ngrams(normalized)
        ngrams_ko = (
            self._scorer._ngrams(normalized_ko) if normalized_ko
            else set()
        )

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
        """표준 용어 로드 + 사전 계산."""
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
                normalized_ko = TermNormalizer.normalize_for_comparison(
                    t.term_ko,
                )

            if get_term_type is not None:
                is_word = get_term_type(t) == "WORD"
            else:
                is_word = getattr(t, 'term_type', None) == "WORD"

            collision_count += self._register_l1_lookups(
                t, normalized, normalized_ko,
            )

            if is_word:
                word_count += 1
                if normalized:
                    self._word_lookup[normalized] = t
                if normalized_ko:
                    self._word_lookup.setdefault(normalized_ko, t)
                continue

            self._register_term_for_l2(
                t, normalized, normalized_ko, term_idx,
            )
            term_idx += 1

        self._loaded = True
        logger.info(
            "EnhancedSimilarityMatcher loaded: %d terms "
            "(%d words, %d terms), %d lookup, %d word_lookup, "
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
    # Public API
    # =========================================================================

    async def match_enhanced(
        self, term: GlossaryTermLike,
    ) -> MatchDecision:
        """3-Layer 파이프라인 매칭 (단일 용어).

        L1 -> L2 -> L3 순서로 실행.
        """
        return await self._match_single(term)

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
        for candidate_str in [
            term.term, getattr(term, 'term_ko', None),
        ]:
            if not candidate_str:
                continue
            l1 = self._l1_exact_match(candidate_str)
            if l1 is not None:
                return l1

        # L1.5: Morpheme Decomposition (enrichment, no early exit)
        morpheme_info = self._l1_5_morpheme_decompose(term)

        # L2: Multi-Channel Retrieval + RRF Fusion
        edit_results, sparse_results, dense_results, _ = (
            self._l2_retrieve_all_channels(term, dense_override)
        )
        fused = self._l2_fuse(
            edit_results, sparse_results, dense_results, top_k=50,
        )

        if not fused:
            return MatchDecision(
                zone="NEW_TERM", matched_morphemes=morpheme_info,
            )

        # L3: Cross-Encoder Rerank
        l3_result = await self._l3_rerank_fused(
            term, fused, edit_results, sparse_results,
            dense_results, morpheme_info, ce_top_k,
        )
        if l3_result is not None:
            return l3_result

        # Fallback: RRF score-based decision
        return self._decide_zone_from_rrf(
            term, fused, edit_results, sparse_results,
            dense_results, morpheme_info,
        )
