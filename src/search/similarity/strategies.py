"""Similarity matching result types and configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class GlossaryTermLike(Protocol):
    """Duck-type Protocol for glossary term objects (GlossaryTermModel etc.)."""

    term: str
    term_ko: str | None
    term_type: str  # "word" | "term"


@dataclass
class MatchDecision:
    """3-zone 매칭 판정 결과."""

    zone: str  # "AUTO_MATCH" | "REVIEW" | "NEW_TERM"
    matched_term: GlossaryTermLike | None = None
    score: float = 0.0
    # exact, synonym, particle, rapidfuzz, sparse, dense, cross_encoder, morpheme
    match_type: str = "none"
    channel_scores: dict[str, float] = field(default_factory=dict)
    # 복합어 형태소 매칭 시 모든 매칭 결과 (e.g. 방송서비스 -> [방송, 서비스])
    matched_morphemes: list[tuple[str, GlossaryTermLike]] = field(default_factory=list)


@dataclass
class _PrecomputedStd:
    """사전 계산된 표준 용어 데이터."""

    term: GlossaryTermLike
    normalized: str
    normalized_ko: str
    ngrams: set[str]
    ngrams_ko: set[str]
    # RapidFuzz용: 매칭 대상 텍스트
    match_text: str  # "term term_ko" 결합


@dataclass
class EnhancedMatcherConfig:
    """Configuration for EnhancedSimilarityMatcher.

    Replaces oreo-ecosystem FeatureFlags with simple config booleans.
    """
    enable_synonym_expansion: bool = True
    enable_rapidfuzz: bool = True
    enable_dense_search: bool = True
    enable_cross_encoder: bool = True
