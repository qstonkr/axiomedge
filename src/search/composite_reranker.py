"""WeKnora-inspired composite reranking helper.

Purpose:
    Apply weighted score fusion between model relevance, original rank score,
    source-type priors, and optional MMR diversification.

Usage:
    reranker = CompositeReranker()
    ranked_chunks = reranker.rerank(query, chunks, top_k=10)

Extracted from oreo-ecosystem composite_reranker.py.
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, replace

from ..config_weights import weights as _w
from ..domain.models import SearchChunk

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompositeRerankerConfig:
    """Configuration for composite scoring."""

    model_weight: float = _w.reranker.model_weight
    base_weight: float = _w.reranker.base_weight
    source_weight: float = _w.reranker.source_weight
    position_weight: float = _w.reranker.position_weight
    mmr_lambda: float = _w.reranker.mmr_lambda
    faq_boost: float = _w.reranker.faq_boost
    mmr_enabled: bool = True


class CompositeReranker:
    """WeKnora-style composite reranker.

    The implementation mirrors the existing search behavior while exposing
    a dedicated component for reuse and easier tuning.
    """

    _source_type_weights: dict[str, float] = {
        "qdrant": _w.reranker.source_qdrant,
        "web": _w.reranker.source_web,
        "web_search": _w.reranker.source_web,
        "graph": _w.reranker.source_graph,
        "graph_search": _w.reranker.source_graph,
        "cross_kb_graph": _w.reranker.source_cross_kb_graph,
        "faq": _w.reranker.source_faq,
        "faq_direct": _w.reranker.source_faq,
        "faq-direct": _w.reranker.source_faq,
    }

    # Cross-KB axis-specific boosts (applied when graph_distance metadata is present)
    _AXIS_BOOSTS: dict[str, float] = {
        "causal": _w.reranker.axis_causal,
        "definitional": _w.reranker.axis_definitional,
        "concept": _w.reranker.axis_concept,
        "temporal": _w.reranker.axis_temporal,
        "process": _w.reranker.axis_process,
        "actor": _w.reranker.axis_actor,
    }

    def __init__(
        self,
        *,
        model_weight: float = _w.reranker.model_weight,
        base_weight: float = _w.reranker.base_weight,
        source_weight: float = _w.reranker.source_weight,
        position_weight: float = _w.reranker.position_weight,
        graph_distance_weight: float = _w.reranker.graph_distance_weight,
        faq_boost: float = _w.reranker.faq_boost,
        mmr_lambda: float = _w.reranker.mmr_lambda,
        mmr_enabled: bool = True,
        source_weights: dict[str, float] | None = None,
        axis_boosts: dict[str, float] | None = None,
    ) -> None:
        self._model_weight = max(0.0, min(1.0, model_weight))
        self._base_weight = max(0.0, min(1.0, base_weight))
        self._source_weight = max(0.0, min(1.0, source_weight))
        self._position_weight = max(0.0, min(1.0, position_weight))
        self._graph_distance_weight = max(0.0, min(1.0, graph_distance_weight))
        self._faq_boost = max(0.0, faq_boost)
        self._mmr_lambda = max(0.0, min(1.0, mmr_lambda))
        self._mmr_enabled = bool(mmr_enabled)
        self._axis_boosts = dict(axis_boosts or self._AXIS_BOOSTS)
        self._source_weights = dict(self._source_type_weights)
        if source_weights:
            self._source_weights.update(
                {
                    str(k).lower(): self._safe_weight(
                        v,
                        default=1.0,
                        source_type=str(k).lower(),
                    )
                    for k, v in source_weights.items()
                },
            )

    def update_axis_boosts(self, boosts: dict[str, float]) -> None:
        """Update axis boosts dynamically (e.g., from AdaptiveAxisBoosts)."""
        self._axis_boosts.update(boosts)

    @staticmethod
    def _safe_float(value: object, default: float) -> float:
        """Convert arbitrary score input to float with fallback."""
        try:
            converted = float(value)
            if not math.isfinite(converted):
                logger.warning(
                    "composite_reranker.non_finite_float",
                    extra={"value": str(value), "default": default},
                )
                return default
            return converted
        except (TypeError, ValueError, OverflowError):
            logger.warning(
                "composite_reranker.safe_float_fallback",
                extra={"value": str(value), "default": default},
            )
            return default

    @staticmethod
    def _safe_weight(value: object, default: float, source_type: str) -> float:
        """Convert and sanitize source weight values."""
        weight = CompositeReranker._safe_float(value, default=default)
        sanitized = max(0.0, weight)
        if sanitized != weight:
            logger.warning(
                "composite_reranker.invalid_source_weight",
                extra={
                    "source_type": source_type,
                    "weight": str(value),
                    "sanitized": sanitized,
                },
            )
        return sanitized

    @staticmethod
    def _extract_component_scores(chunks: list[SearchChunk]) -> tuple[list[float], list[float]]:
        """Return (model_scores, base_scores) from chunk metadata or score fallback."""
        model_scores: list[float] = []
        base_scores: list[float] = []
        for chunk in chunks:
            metadata = chunk.metadata or {}
            default_score = CompositeReranker._safe_float(chunk.score, default=0.0)

            model_score = metadata.get("model_score")
            if model_score is None:
                model_score = metadata.get("cross_encoder_score", default_score)

            base_score = metadata.get("base_score", default_score)

            model_scores.append(CompositeReranker._safe_float(model_score, default_score))
            base_scores.append(CompositeReranker._safe_float(base_score, default_score))

        return model_scores, base_scores

    def rerank(
        self,
        query: str,
        chunks: list[SearchChunk],
        top_k: int,
        *,
        source_weights: dict[str, float] | None = None,
    ) -> list[SearchChunk]:
        """Return reranked chunks with updated scores.

        Args:
            query: Retrieval query (reserved for future query-aware scoring hooks).
            chunks: Input chunks.
            top_k: Maximum result size.
            source_weights: Optional source-type weight override.
        """
        del query

        if not chunks:
            return []

        if source_weights:
            weights = self._source_weights.copy()
            for key, value in source_weights.items():
                source_key = str(key).lower()
                weights[source_key] = self._safe_weight(
                    value,
                    default=self._source_type_weights.get(source_key, 1.0),
                    source_type=source_key,
                )
        else:
            weights = self._source_weights

        model_scores, base_scores = self._extract_component_scores(chunks)
        normalized_model_scores = self._normalize_scores(model_scores)
        normalized_base_scores = self._normalize_scores(base_scores)

        weighted_chunks: list[
            tuple[SearchChunk, float, float, float, float, float]
        ] = []

        for idx, chunk in enumerate(chunks):
            normalized_model_score = normalized_model_scores[idx]
            normalized_base_score = normalized_base_scores[idx]
            source_type = (
                str(
                    (
                        chunk.metadata.get("source_type")
                        or chunk.metadata.get("knowledge_type")
                        or chunk.metadata.get("chunk_source")
                        or "qdrant"
                    )
                    or "qdrant"
                )
                .lower()
            )
            source_boost = weights.get(source_type, 1.0)
            if source_type == "faq":
                source_boost *= self._faq_boost
            # Normalize source_boost: cap raw boost so the weighted contribution
            # stays within [0, source_weight] while still differentiating FAQ (1.2)
            # from default (1.0).  E.g., max_boost=1.5 -> FAQ 1.2 maps to 0.8 band.
            max_boost = max(self._faq_boost, 1.0)
            normalized_boost = min(source_boost, max_boost) / max_boost
            source_contribution = normalized_boost * self._source_weight

            position_prior = 0.0
            if self._position_weight > 0.0:
                position_prior = self._position_weight * (
                    1.0 - (idx / max(1, len(chunks)))
                )

            # Cross-KB graph distance bonus (additive when graph_distance metadata present)
            graph_distance_bonus = 0.0
            if self._graph_distance_weight > 0.0:
                raw_distance = (chunk.metadata or {}).get("graph_distance")
                if raw_distance is not None:
                    try:
                        distance = int(raw_distance)
                    except (TypeError, ValueError):
                        distance = 0
                    if distance > 0:
                        # Closer = higher score: 1/(1 + (d-1)*0.3)
                        graph_score = 1.0 / (1 + (distance - 1) * 0.3)
                        axis_name = str((chunk.metadata or {}).get("traversal_axis", ""))
                        axis_boost = self._axis_boosts.get(axis_name, 1.0)
                        graph_distance_bonus = self._graph_distance_weight * graph_score * axis_boost

            composite_score = (
                (normalized_model_score * self._model_weight)
                + (normalized_base_score * self._base_weight)
                + source_contribution
                + position_prior
                + graph_distance_bonus
            )
            weighted_chunks.append(
                (
                    chunk,
                    max(0.0, min(1.0, composite_score)),
                    normalized_model_score,
                    normalized_base_score,
                    source_boost,
                    position_prior,
                )
            )

        if not weighted_chunks:
            return []

        if self._mmr_enabled and len(weighted_chunks) > 1:
            return self._mmr_rerank(weighted_chunks, max(1, top_k), weights)

        ranked = sorted(weighted_chunks, key=lambda item: item[1], reverse=True)
        return [
            self._replace_score(chunk, score)
            for chunk, score, _model_score, _base_score, _source_boost, _position in ranked[
                : max(1, min(top_k, len(ranked)))
            ]
        ]

    @staticmethod
    def _normalize_scores(values: list[float]) -> list[float]:
        if not values:
            return []

        max_score = max(values)
        min_score = min(values)
        if max_score == min_score:
            return [0.5] * len(values)

        delta = max_score - min_score
        return [(score - min_score) / delta for score in values]

    def _mmr_rerank(
        self,
        weighted_chunks: list[tuple[SearchChunk, float, float, float, float, float]],
        target_count: int,
        source_weights: dict[str, float] | None = None,
    ) -> list[SearchChunk]:
        del source_weights
        pre_sorted = sorted(weighted_chunks, key=lambda item: item[1], reverse=True)
        selected: list[tuple[
            SearchChunk,
            float,
            float,
            float,
            float,
            float,
        ]] = []
        remaining: list[
            tuple[SearchChunk, float, float, float, float, float]
        ] = list(pre_sorted)

        # Pre-compute word sets once for all chunks (P1-3 perf fix)
        word_sets: dict[int, set[str]] = {}
        for item in remaining:
            chunk = item[0]
            word_sets[id(chunk)] = set(
                (chunk.content or "").lower().split()
            )

        selected.append(remaining.pop(0))

        while len(selected) < target_count and remaining:
            best_idx = -1
            best_score = float("-inf")
            for idx, candidate in enumerate(remaining):
                relevance = candidate[1]
                max_sim = 0.0
                candidate_words = word_sets.get(id(candidate[0]), set())
                for picked in selected:
                    picked_words = word_sets.get(id(picked[0]), set())
                    max_sim = max(
                        max_sim,
                        self._jaccard_similarity_sets(
                            candidate_words,
                            picked_words,
                        ),
                    )
                mmr_score = self._mmr_lambda * relevance - (
                    1.0 - self._mmr_lambda
                ) * max_sim
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = idx
            if best_idx < 0:
                break
            selected.append(remaining.pop(best_idx))

        return [
            self._replace_score(chunk, score)
            for chunk, score, _model_score, _base_score, _source_boost, _position in selected
        ]

    @staticmethod
    def _replace_score(chunk: SearchChunk, score: float) -> SearchChunk:
        """Update reranker score with an immutable-friendly path."""
        try:
            return replace(chunk, score=score)
        except TypeError:
            # Fallback for non-dataclass-compatible chunk implementations.
            try:
                return chunk.__class__(**(chunk.__dict__ | {"score": score}))
            except (TypeError, AttributeError, KeyError):
                logger.warning(
                    "CompositeReranker: failed to clone chunk safely; returning original instance",
                    extra={"chunk_id": getattr(chunk, "chunk_id", None)},
                )
                return chunk

    @staticmethod
    def _jaccard_similarity(text1: str, text2: str) -> float:
        words1 = set((text1 or "").lower().split())
        words2 = set((text2 or "").lower().split())
        if not words1 or not words2:
            return 0.0
        union = words1 | words2
        if not union:
            return 0.0
        return len(words1 & words2) / len(union)

    @staticmethod
    def _jaccard_similarity_sets(words1: set[str], words2: set[str]) -> float:
        """Jaccard similarity from pre-computed word sets (avoids re-splitting)."""
        if not words1 or not words2:
            return 0.0
        union = words1 | words2
        if not union:
            return 0.0
        return len(words1 & words2) / len(union)


__all__ = ["CompositeReranker", "CompositeRerankerConfig"]
