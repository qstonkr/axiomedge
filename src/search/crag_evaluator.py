"""CRAG Retrieval Evaluator.

Purpose:
    Evaluate retrieval quality and choose a corrective action using CRAG
    (Corrective RAG) patterns.

Features:
    - 3-way retrieval action decision: CORRECT / AMBIGUOUS / INCORRECT
    - Weighted confidence scoring:
        retrieval 40%, coverage 25%, freshness 20%, query specificity 15%
    - Korean abstention / recommendation messages for transparency

Usage:
    evaluator = CRAGRetrievalEvaluator()
    evaluation = await evaluator.evaluate(query, chunks, search_time_ms=120.0)

Extracted from oreo-ecosystem crag_retrieval_evaluator.py.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .confidence_thresholds import (
    KnowledgeConfidenceThresholds,
    read_env_unit_interval,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RetrievalAction(str, Enum):
    """CRAG retrieval action."""

    CORRECT = "correct"
    AMBIGUOUS = "ambiguous"
    INCORRECT = "incorrect"


class ConfidenceLevel(str, Enum):
    """Confidence level band."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


# ---------------------------------------------------------------------------
# Korean abstention / recommendation messages
# ---------------------------------------------------------------------------

ABSTENTION_MESSAGES: dict[str, str] = {
    "no_knowledge": "제공된 문서에서 해당 주제에 대한 정보를 찾을 수 없습니다.",
    "low_confidence": "관련 정보가 있으나 정확도가 낮아 답변을 보류합니다. 담당자에게 직접 확인을 권장합니다.",
    "llm_only": "문서에서 직접 확인된 정보가 아닌 일반 지식 기반 응답입니다.",
}

CONFIDENCE_RECOMMENDATIONS: dict[str, str | None] = {
    "high": None,  # No additional guidance needed
    "medium": "이 답변은 문서 기반이나, 정확성을 위해 원본 문서를 확인해주세요.",
    "low": "관련 정보가 제한적입니다. 전문가에게 추가 확인을 권장합니다.",
    "uncertain": "확인된 정보가 부족합니다. 담당자에게 문의하세요.",
}

# Also support ConfidenceLevel enum keys for convenience
_LEVEL_RECOMMENDATIONS: dict[ConfidenceLevel, str | None] = {
    ConfidenceLevel.HIGH: None,
    ConfidenceLevel.MEDIUM: CONFIDENCE_RECOMMENDATIONS["medium"],
    ConfidenceLevel.LOW: CONFIDENCE_RECOMMENDATIONS["low"],
    ConfidenceLevel.UNCERTAIN: CONFIDENCE_RECOMMENDATIONS["uncertain"],
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CRAGEvaluation:
    """CRAG evaluation result."""

    action: RetrievalAction
    confidence_score: float
    confidence_level: ConfidenceLevel
    factors: dict[str, float]
    recommendation: str | None
    source_attribution: bool


# ---------------------------------------------------------------------------
# SearchChunk interface (duck-typed dict or object)
# ---------------------------------------------------------------------------


def _chunk_score(chunk: Any) -> float:
    if isinstance(chunk, dict):
        return float(chunk.get("score", 0.0))
    return float(getattr(chunk, "score", 0.0))


def _chunk_doc_id(chunk: Any) -> str:
    if isinstance(chunk, dict):
        return str(chunk.get("document_id") or chunk.get("chunk_id", ""))
    return str(getattr(chunk, "document_id", None) or getattr(chunk, "chunk_id", ""))


def _chunk_metadata(chunk: Any) -> dict:
    if isinstance(chunk, dict):
        return chunk.get("metadata") or {}
    return getattr(chunk, "metadata", None) or {}


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


_WHITESPACE_SPLIT = re.compile(r"\s+")


class CRAGRetrievalEvaluator:
    """CRAG pattern-based retrieval quality evaluator."""

    # Default thresholds (environment override available)
    CORRECT_THRESHOLD = KnowledgeConfidenceThresholds.RETRIEVAL_CORRECT
    AMBIGUOUS_THRESHOLD = KnowledgeConfidenceThresholds.RETRIEVAL_AMBIGUOUS
    HIGH_CONFIDENCE_THRESHOLD = KnowledgeConfidenceThresholds.HIGH

    # Weights (must sum to 1.0)
    _WEIGHT_RETRIEVAL_RELEVANCE = 0.40
    _WEIGHT_SOURCE_COVERAGE = 0.25
    _WEIGHT_SOURCE_FRESHNESS = 0.20
    _WEIGHT_QUERY_SPECIFICITY = 0.15

    def __init__(self) -> None:
        self._correct_threshold = read_env_unit_interval(
            "CRAG_CORRECT_THRESHOLD", self.CORRECT_THRESHOLD
        )
        self._ambiguous_threshold = read_env_unit_interval(
            "CRAG_AMBIGUOUS_THRESHOLD", self.AMBIGUOUS_THRESHOLD
        )

    async def evaluate(
        self,
        query: str,
        search_chunks: list[Any],
        search_time_ms: float,
    ) -> CRAGEvaluation:
        """Evaluate retrieval -> CORRECT / AMBIGUOUS / INCORRECT decision."""
        await asyncio.sleep(0)
        retrieval_quality = self._calculate_retrieval_relevance(search_chunks)
        source_coverage = self._calculate_source_coverage(search_chunks, query)
        source_freshness = self._calculate_source_freshness(search_chunks)
        query_specificity = self._calculate_query_specificity(query)

        score = (
            (retrieval_quality * self._WEIGHT_RETRIEVAL_RELEVANCE)
            + (source_coverage * self._WEIGHT_SOURCE_COVERAGE)
            + (source_freshness * self._WEIGHT_SOURCE_FRESHNESS)
            + (query_specificity * self._WEIGHT_QUERY_SPECIFICITY)
        )
        score = max(0.0, min(score, 1.0))

        # Apply search time penalty as a mild score reduction (max 10%)
        search_time_penalty = self._search_time_penalty(search_time_ms)
        if search_time_penalty > 0:
            score *= (1.0 - search_time_penalty * 0.1)
            score = max(0.0, score)

        if not search_chunks:
            action = RetrievalAction.INCORRECT
        elif score >= self._correct_threshold:
            action = RetrievalAction.CORRECT
        elif score >= self._ambiguous_threshold:
            action = RetrievalAction.AMBIGUOUS
        else:
            action = RetrievalAction.INCORRECT

        confidence_level = self._to_confidence_level(score)
        source_attribution = bool(search_chunks) and action != RetrievalAction.INCORRECT

        recommendation = _LEVEL_RECOMMENDATIONS.get(confidence_level)
        if action == RetrievalAction.INCORRECT:
            recommendation = (
                ABSTENTION_MESSAGES["no_knowledge"]
                if not search_chunks
                else ABSTENTION_MESSAGES["low_confidence"]
            )

        return CRAGEvaluation(
            action=action,
            confidence_score=score,
            confidence_level=confidence_level,
            factors={
                "retrieval_quality": retrieval_quality,
                "source_freshness": source_freshness,
                "coverage": source_coverage,
                "query_specificity": query_specificity,
                "search_time_penalty": search_time_penalty,
            },
            recommendation=recommendation,
            source_attribution=source_attribution,
        )

    # -- Factor calculations -----------------------------------------------

    def _calculate_retrieval_relevance(self, chunks: list[Any]) -> float:
        """Score-based relevance (top-k weighted average)."""
        if not chunks:
            return 0.0
        weighted_sum = 0.0
        weight_total = 0.0
        for i, chunk in enumerate(chunks[:10]):
            weight = 1.0 / (i + 1)
            weighted_sum += max(0.0, min(_chunk_score(chunk), 1.0)) * weight
            weight_total += weight
        return weighted_sum / weight_total if weight_total > 0 else 0.0

    def _calculate_source_coverage(self, chunks: list[Any], query: str) -> float:
        """Source coverage: unique documents vs expected, with section diversity bonus."""
        if not chunks:
            return 0.0
        unique_docs = len({_chunk_doc_id(c) for c in chunks})
        expected = self._estimate_expected_sources(query)
        coverage = unique_docs / max(expected, 1)

        # 섹션 다양성 보너스: 같은 문서 내 여러 섹션 커버 시 +0.1
        section_bonus = self._section_coverage_bonus(chunks)
        coverage += section_bonus

        return max(0.0, min(coverage, 1.0))

    @staticmethod
    def _section_coverage_bonus(chunks: list[Any]) -> float:
        """트리 인덱스 활성화 시, 섹션 다양성에 따른 커버리지 보너스.

        같은 문서의 여러 섹션이 히트되면 해당 문서의 맥락 커버리지가 높다고 판단.
        """
        from src.config import get_settings
        if not get_settings().tree_index.enabled:
            return 0.0

        doc_sections: dict[str, set[str]] = {}
        for chunk in chunks:
            meta = _chunk_metadata(chunk)
            doc_id = meta.get("document_id", "") or meta.get("doc_id", "")
            hp = meta.get("heading_path", "") or ""
            top_section = hp.split(" > ")[0].strip() if hp else ""
            if doc_id and top_section:
                doc_sections.setdefault(doc_id, set()).add(top_section)

        if not doc_sections:
            return 0.0

        # 2+ 섹션이 히트된 문서 수에 비례한 보너스 (최대 0.1)
        multi_section_docs = sum(1 for secs in doc_sections.values() if len(secs) >= 2)
        return min(multi_section_docs * 0.05, 0.1)

    def _calculate_query_specificity(self, query: str) -> float:
        """Query specificity score."""
        query = (query or "").strip()
        if not query:
            return 0.0
        tokens = [t for t in _WHITESPACE_SPLIT.split(query) if t]
        if not tokens:
            return 0.0
        unique_ratio = len(set(tokens)) / len(tokens)
        length_factor = min(len(tokens) / 10.0, 1.0)
        keyword_factor = min(
            sum(1 for t in tokens if len(t) >= 3) / len(tokens),
            1.0,
        )
        return max(
            0.0,
            min((unique_ratio * 0.4) + (length_factor * 0.3) + (keyword_factor * 0.3), 1.0),
        )

    def _calculate_source_freshness(self, chunks: list[Any]) -> float:
        """Simple exponential-decay freshness score."""
        if not chunks:
            return 0.0
        scores: list[float] = []
        now = datetime.now(timezone.utc)
        for chunk in chunks[:10]:
            meta = _chunk_metadata(chunk)
            updated_at = (
                meta.get("updated_at")
                or meta.get("last_updated")
                or meta.get("last_modified")
                or meta.get("modified_at")
            )
            if not updated_at:
                continue
            parsed = self._parse_datetime(str(updated_at))
            if parsed is None:
                continue
            age_days = max((now - parsed).total_seconds() / 86400, 0)
            # Exponential decay: half-life ~180 days
            freshness = 2.0 ** (-age_days / 180.0)
            scores.append(max(0.0, min(freshness, 1.0)))

        if not scores:
            return 0.5  # neutral when no timestamp data
        return max(0.0, min(sum(scores) / len(scores), 1.0))

    # -- Helpers -----------------------------------------------------------

    def _estimate_expected_sources(self, query: str) -> int:
        tokens = [t for t in _WHITESPACE_SPLIT.split((query or "").strip()) if t]
        if len(tokens) >= 10:
            return 4
        if len(tokens) >= 5:
            return 3
        return 2

    def _to_confidence_level(self, score: float) -> ConfidenceLevel:
        if score >= self.HIGH_CONFIDENCE_THRESHOLD:
            return ConfidenceLevel.HIGH
        if score >= KnowledgeConfidenceThresholds.MEDIUM:
            return ConfidenceLevel.MEDIUM
        if score >= KnowledgeConfidenceThresholds.RETRIEVAL_AMBIGUOUS:
            return ConfidenceLevel.LOW
        return ConfidenceLevel.UNCERTAIN

    def _search_time_penalty(self, search_time_ms: float) -> float:
        if search_time_ms <= 0:
            return 0.0
        return max(0.0, min(search_time_ms / 3000.0, 1.0))

    def _parse_datetime(self, value: str) -> datetime | None:
        try:
            normalized = value.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None


__all__ = [
    "ABSTENTION_MESSAGES",
    "CONFIDENCE_RECOMMENDATIONS",
    "CRAGEvaluation",
    "CRAGRetrievalEvaluator",
    "ConfidenceLevel",
    "RetrievalAction",
]
