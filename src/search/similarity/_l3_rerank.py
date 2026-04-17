"""Similarity matcher -- L3 cross-encoder precision judgment.

Extracted from matcher.py for SRP. Mixin for EnhancedSimilarityMatcher.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from src.config.weights import weights as _w

from .strategies import GlossaryTermLike, MatchDecision
from .utils import AUTO_MATCH_THRESHOLD, REVIEW_THRESHOLD

logger = logging.getLogger(__name__)


class L3RerankMixin:
    """L3 cross-encoder scoring, batch reranking, and zone decision logic."""

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

        ch_scores = self._collect_channel_scores(
            best_idx, edit_results, sparse_results, dense_results,
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

        ch_scores = self._collect_channel_scores(
            best_idx, edit_results, sparse_results, dense_results,
        )
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
