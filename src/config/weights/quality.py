"""Document quality and trust score weights."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QualityConfig:
    """Document quality tier thresholds."""

    gold_min_chars: int = 2000
    gold_structured_min_chars: int = 1000
    silver_min_chars: int = 500
    silver_structured_min_chars: int = 200
    bronze_min_chars: int = 50
    min_content_length: int = 50
    noise_max_chars: int = 50

    stale_threshold_days: int = 730
    stale_weight: float = 0.7
    fresh_boost: float = 1.2
    stale_penalty: float = 0.8
    outdated_penalty: float = 0.5

    fresh_max_days: int = 90
    stale_max_days: int = 365

    kts_has_metadata_high: float = 0.8
    kts_has_metadata_low: float = 0.3
    kts_freshness_default: float = 0.5
    kts_freshness_30d: float = 1.0
    kts_freshness_90d: float = 0.8
    kts_freshness_180d: float = 0.5
    kts_freshness_old: float = 0.3
    kts_tier_high: float = 0.7
    kts_tier_medium: float = 0.4


@dataclass(frozen=True)
class TrustScoreWeights:
    """Knowledge Trust Score signal weights.

    KTS = 0.20 * source_credibility
        + 0.20 * freshness
        + 0.25 * user_validation
        + 0.10 * usage
        + 0.15 * hallucination
        + 0.10 * consistency
    """

    source_credibility_weight: float = 0.20
    freshness_weight: float = 0.20
    user_validation_weight: float = 0.25
    usage_weight: float = 0.10
    hallucination_weight: float = 0.15
    consistency_weight: float = 0.10

    tier_high: float = 85.0
    tier_medium: float = 70.0
    tier_low: float = 50.0

    verification_threshold: float = 50.0

    cred_git_docs: float = 0.95
    cred_confluence_personal: float = 0.75
    cred_teams_chat: float = 0.50
    cred_user_unverified: float = 0.30
    cred_user_verified: float = 0.80
    cred_auto_extracted: float = 0.20

    freshness_decay_start_ratio: float = 0.5
    freshness_stale_threshold: float = 0.3

    usage_weight_views: float = 0.2
    usage_weight_citations: float = 0.3
    usage_weight_ctr: float = 0.3
    usage_weight_bookmarks: float = 0.2
