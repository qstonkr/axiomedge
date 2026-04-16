"""Cache configuration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class CacheConfig:
    """Multi-layer cache parameters."""

    l1_max_entries: int = 10000
    l1_ttl_seconds: int = 300
    l2_similarity_threshold: float = 0.92
    l2_max_entries: int = 50000
    l2_ttl_seconds: int = 3600
    enable_semantic_cache: bool = True
    idempotency_ttl_seconds: int = 60

    threshold_policy: float = 1.0
    threshold_code: float = 0.95
    threshold_kb: float = 0.92
    threshold_general: float = 0.85

    ttl_policy: int = 1800
    ttl_code: int = 1800
    ttl_kb_search: int = 3600
    ttl_general: int = 7200

    cache_version: str = ""


def compute_cache_version(cfg: CacheConfig) -> str:
    """Generate cache version hash from config values."""
    sig = json.dumps({
        "th": [cfg.threshold_policy, cfg.threshold_code, cfg.threshold_kb, cfg.threshold_general],
        "ttl": [cfg.ttl_policy, cfg.ttl_code, cfg.ttl_kb_search, cfg.ttl_general],
        "pipe": "v3",
    }, sort_keys=True)
    return "v3_" + hashlib.sha256(sig.encode()).hexdigest()[:8]
