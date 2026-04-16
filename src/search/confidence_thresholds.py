"""Knowledge Confidence Thresholds.

Purpose:
    Provide SSOT confidence thresholds for knowledge Q&A quality decisions.

Features:
    - Shared confidence bands (high/medium/low).
    - Shared retrieval action thresholds (correct/ambiguous).
    - Utility clamp for safe environment overrides.

Usage:
    from src.search.confidence_thresholds import KnowledgeConfidenceThresholds

Examples:
    if score >= KnowledgeConfidenceThresholds.HIGH:
        level = "high"

Extracted from oreo-ecosystem confidence_thresholds.py.
"""

from __future__ import annotations

import os

from src.config.weights import weights as _w


class KnowledgeConfidenceThresholds:
    """SSOT confidence thresholds for the knowledge-answering pipeline."""

    HIGH = _w.confidence.high
    MEDIUM = _w.confidence.medium
    LOW = _w.confidence.low

    RETRIEVAL_CORRECT = _w.confidence.retrieval_correct
    RETRIEVAL_AMBIGUOUS = _w.confidence.retrieval_ambiguous
    RETRIEVAL_WEAKNESS = _w.confidence.crag_weakness

    # CRAG action selector thresholds (decoupled from response strategy).
    CRAG_CORRECT = _w.confidence.crag_correct
    CRAG_WEAKNESS = _w.confidence.crag_weakness

    # Query classifier confidence routing thresholds.
    QUERY_PATTERN_MATCH_MIN = _w.confidence.retrieval_correct
    QUERY_LLM_FALLBACK_MIN = _w.confidence.query_llm_fallback_min

    # Claim-to-evidence semantic match threshold.
    EVIDENCE_MATCH_MIN = _w.confidence.crag_correct

    # Response strategy confidence thresholds by query type.
    # FACTUAL query strategy must align with CRAG "correct" criterion.
    FACTUAL_RESPONSE_MIN = _w.confidence.factual_min
    ANALYTICAL_RESPONSE_MIN = _w.confidence.analytical_min
    ADVISORY_RESPONSE_MIN = _w.confidence.advisory_min
    COMPARATIVE_RESPONSE_MIN = _w.confidence.medium
    MULTI_HOP_RESPONSE_MIN = _w.confidence.multi_hop_min
    UNKNOWN_RESPONSE_MIN = _w.confidence.unknown_response_min

    # Default threshold for proactive quality alerting.
    QUALITY_ALERT_MIN = _w.confidence.medium

    # Quality-gate minimum thresholds (promotion/evaluation path SSOT).
    QUALITY_GATE_FAITHFULNESS_MIN = _w.confidence.quality_gate_faithfulness
    QUALITY_GATE_CONTEXT_RELEVANCY_MIN = _w.confidence.quality_gate_context_relevancy
    QUALITY_GATE_ANSWER_RELEVANCY_MIN = _w.confidence.quality_gate_answer_relevancy


def clamp_unit_interval(value: float, default: float) -> float:
    """Clamp a numeric value into [0.0, 1.0] with a safe default fallback."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(0.0, min(parsed, 1.0))


def read_env_unit_interval(key: str, default: float) -> float:
    """Read a unit-interval threshold from environment with safe clamping."""
    return clamp_unit_interval(os.getenv(key), default=default)


__all__ = [
    "KnowledgeConfidenceThresholds",
    "clamp_unit_interval",
    "read_env_unit_interval",
]
