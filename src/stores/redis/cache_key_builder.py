"""Cache Key Builder.

Generates structured, normalized cache keys.
Key format: {prefix}:{kb_id_hash}:{query_hash}

Normalizes query text before hashing for consistent key generation.

py.
Simplified: no PII normalization, no model-specific namespacing.
"""

from __future__ import annotations

import hashlib
import re


def normalize_query(query: str) -> str:
    """Normalize a query for consistent cache key generation.

    - Lowercase
    - Collapse whitespace
    - Strip leading/trailing whitespace
    """
    result = query.lower()
    result = re.sub(r"\s+", " ", result).strip()
    return result


def build_cache_key(
    query: str,
    kb_ids: list[str] | None = None,
    prefix: str = "knowledge",
    top_k: int = 0,
) -> str:
    """Build a structured cache key.

    Args:
        query: User query text.
        kb_ids: Knowledge base IDs for isolation.
        prefix: Key prefix namespace.
        top_k: Top-K parameter.

    Returns:
        Deterministic cache key string.
    """
    normalized = normalize_query(query)
    raw = normalized
    if kb_ids:
        raw += "::" + ",".join(sorted(kb_ids))
    if top_k:
        raw += f"::top_k={top_k}"
    query_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]

    kb_part = ""
    if kb_ids:
        kb_str = ",".join(sorted(kb_ids))
        kb_part = hashlib.sha256(kb_str.encode()).hexdigest()[:8]

    if kb_part:
        return f"{prefix}:{kb_part}:{query_hash}"
    return f"{prefix}:{query_hash}"
