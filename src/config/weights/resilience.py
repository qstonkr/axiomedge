"""Resilience (retry + circuit breaker) configuration per external service.

Each service has its own retry policy because failure modes differ:
- SageMaker: cold-start can take 30s — use longer backoff
- TEI: usually fast, fail fast
- Ollama: local — short retry to detect down quickly
- Confluence: rate-limited — moderate retry
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.resilience import ResilienceConfig


@dataclass(frozen=True)
class ResilienceWeights:
    """Per-service resilience policies."""

    tei: ResilienceConfig = field(
        default_factory=lambda: ResilienceConfig(
            max_attempts=3, initial_backoff_seconds=0.5, max_backoff_seconds=4.0,
        )
    )
    sagemaker: ResilienceConfig = field(
        default_factory=lambda: ResilienceConfig(
            max_attempts=4, initial_backoff_seconds=1.0, max_backoff_seconds=16.0,
        )
    )
    qdrant: ResilienceConfig = field(
        default_factory=lambda: ResilienceConfig(
            max_attempts=3, initial_backoff_seconds=0.3, max_backoff_seconds=4.0,
        )
    )
    ollama: ResilienceConfig = field(
        default_factory=lambda: ResilienceConfig(
            max_attempts=2, initial_backoff_seconds=0.5, max_backoff_seconds=2.0,
        )
    )
    confluence: ResilienceConfig = field(
        default_factory=lambda: ResilienceConfig(
            max_attempts=3, initial_backoff_seconds=1.0, max_backoff_seconds=8.0,
        )
    )
