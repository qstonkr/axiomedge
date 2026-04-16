"""Confidence scoring and response generation thresholds."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceConfig:
    """Confidence scoring thresholds."""

    high: float = 0.85
    medium: float = 0.70
    low: float = 0.50

    retrieval_correct: float = 0.70
    retrieval_ambiguous: float = 0.40

    crag_correct: float = 0.60
    crag_weakness: float = 0.50

    factual_min: float = 0.70
    analytical_min: float = 0.70
    advisory_min: float = 0.50
    multi_hop_min: float = 0.75

    entity_embedding_threshold: float = 0.85
    glossary_match_confidence: float = 0.95
    rule_based_confidence: float = 0.90

    unknown_response_min: float = 0.80
    query_llm_fallback_min: float = 0.30

    quality_gate_faithfulness: float = 0.70
    quality_gate_context_relevancy: float = 0.65
    quality_gate_answer_relevancy: float = 0.70


@dataclass(frozen=True)
class ResponseConfig:
    """Tiered response generation parameters."""

    factual_relevance_threshold: float = 0.3
    analytical_relevance_threshold: float = 0.5
    advisory_relevance_threshold: float = 0.5
    default_relevance_threshold: float = 0.5
