"""Similarity matcher -- L2 multi-channel candidate retrieval.

Extracted from matcher.py for SRP. Mixin for EnhancedSimilarityMatcher.
"""

from __future__ import annotations

import logging

from src.config.weights import weights as _w
from src.nlp.korean.term_normalizer import TermNormalizer

from .strategies import GlossaryTermLike

logger = logging.getLogger(__name__)


class L2RetrievalMixin:
    """L2 multi-channel retrieval: RapidFuzz edit, n-gram sparse, dense, and RRF fusion."""

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
        self, candidate: str, top_k: int = 50,
        score_cutoff: int = _w.similarity.rapidfuzz_score_cutoff,
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
            effective_threshold = (
                jaccard_threshold if jaccard_threshold is not None
                else self._jaccard_threshold
            )
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
