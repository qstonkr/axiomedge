# pyright: reportAttributeAccessIssue=false
"""Similarity matcher — batch matching & dense index methods.

Extracted from matcher.py for SRP. Mixin for EnhancedSimilarityMatcher.
"""

from __future__ import annotations

import logging
from typing import Any

from src.nlp.korean.term_normalizer import TermNormalizer

from .strategies import MatchDecision

logger = logging.getLogger(__name__)


class BatchMatchMixin:
    """Batch matching, dense index, cross-encoder setup methods."""

    async def match_batch(
        self, terms: list[Any], *, disable_cross_encoder: bool = False,
    ) -> list[MatchDecision]:
        """배치 매칭 (PENDING 전체).

        Graceful Degradation (설계서 4.3.2):
        - PENDING <= 3,000건: 전체 파이프라인 (L1->L2->L3)
        - 3,000 < PENDING <= 10,000건: L3 Cross-Encoder Top-K를 10으로 축소
        - PENDING > 10,000건: L3 비활성, L2 RRF 점수로 직접 판정
        """
        ce_top_k = self._resolve_ce_config(terms, disable_cross_encoder)
        l1_unmatched_indices, l1_unmatched_texts = self._collect_l1_unmatched(terms)
        dense_batch_results = self._prepare_dense_batch(
            terms, l1_unmatched_indices, l1_unmatched_texts,
        )

        results: list[MatchDecision] = []
        for i, term in enumerate(terms):
            override_dense = dense_batch_results.get(i) if dense_batch_results else None
            decision = await self._match_single(
                term, ce_top_k=ce_top_k, dense_override=override_dense,
            )
            results.append(decision)

        self._force_disable_ce = False
        return results

    def _rapidfuzz_candidates(
        self, search_str: str, rf_process: Any, fuzz: Any,
    ) -> list[int]:
        """Get candidate indices via RapidFuzz edit-distance search."""
        if not self._config.enable_rapidfuzz or not self._rf_choices:
            return []
        normalized = TermNormalizer.normalize_for_comparison(search_str)
        if not normalized:
            return []
        try:
            results = rf_process.extract(
                normalized, self._rf_choices,
                scorer=fuzz.WRatio, score_cutoff=60, limit=10,
            )
            return [self._rf_idx_map[choice_idx] for _, _, choice_idx in results]
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("RapidFuzz extraction failed: %s", e)
            return []

    def _collect_mini_dense_candidates(
        self, terms: list[Any], l1_unmatched_indices: list[int],
    ) -> set[int]:
        """Collect candidate standard term indices from edit/sparse channels."""
        try:
            from rapidfuzz import fuzz, process as rf_process
        except ImportError:
            return set()

        all_candidate_indices: set[int] = set()
        for i in l1_unmatched_indices:
            term = terms[i]
            for search_str in [term.term, getattr(term, 'term_ko', None)]:
                if not search_str:
                    continue
                all_candidate_indices.update(
                    self._rapidfuzz_candidates(search_str, rf_process, fuzz),
                )
                for idx, _ in self._l2_sparse(search_str, top_k=10):
                    all_candidate_indices.add(idx)
        return all_candidate_indices

    def _build_and_search_mini_index(
        self,
        candidate_list: list[int],
        l1_unmatched_indices: list[int],
        l1_unmatched_texts: list[str],
    ) -> dict[int, list[tuple[int, float]]] | None:
        """Build mini dense index from candidates and search pending terms."""
        from src.search.dense_term_index import DenseTermIndex

        mini_pcs = [self._precomputed[idx] for idx in candidate_list]
        mini_index = DenseTermIndex(self._embedding_adapter)
        mini_index.build(mini_pcs, batch_size=50)

        if not mini_index.is_ready:
            logger.warning("Mini dense index build failed (not ready)")
            return None

        batch_results = mini_index.search_batch(
            l1_unmatched_texts, top_k=50, batch_size=50,
        )

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

    def _build_mini_dense(
        self,
        terms: list[Any],
        l1_unmatched_indices: list[int],
        l1_unmatched_texts: list[str],
    ) -> dict[int, list[tuple[int, float]]] | None:
        """On-demand mini dense: edit/sparse 후보 standard terms만 임베딩."""
        if not self._embedding_adapter:
            return None

        all_candidate_indices = self._collect_mini_dense_candidates(terms, l1_unmatched_indices)
        if not all_candidate_indices:
            return None

        candidate_list = sorted(all_candidate_indices)
        logger.info(
            "Building mini dense index: %d candidates from edit/sparse (vs %d total standard)",
            len(candidate_list), len(self._precomputed),
        )

        try:
            return self._build_and_search_mini_index(
                candidate_list, l1_unmatched_indices, l1_unmatched_texts,
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
