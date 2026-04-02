"""Shared LLM utilities — SSOT for text sanitization and token estimation.

Extracted from ollama_client.py / sagemaker_client.py to eliminate duplication.
"""

from __future__ import annotations

import re

from src.config_weights import weights

# Pre-compiled regex patterns for token estimation
LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
CJK_TOKEN_RE = re.compile(r"[\uAC00-\uD7A3]")
PUNCT_TOKEN_RE = re.compile(r"[^\sA-Za-z0-9\uAC00-\uD7A3]")


def sanitize_text(text: str, max_length: int = weights.llm.max_query_length) -> str:
    """Simple input sanitization with length truncation."""
    if not text:
        return ""
    sanitized = text.strip()
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
    return sanitized


def estimate_token_count(value: str) -> int:
    """Rudimentary token counter to avoid dependency on tokenizer libraries."""
    if not value:
        return 0
    normalized = value.strip()
    if not normalized:
        return 0

    latin_tokens = len(LATIN_TOKEN_RE.findall(normalized))
    cjk_tokens = len(CJK_TOKEN_RE.findall(normalized))
    punctuation_tokens = len(PUNCT_TOKEN_RE.findall(normalized))

    estimated = latin_tokens + cjk_tokens + punctuation_tokens
    if estimated == 0:
        estimated = max(1, len(normalized) // 4)

    return max(1, estimated)
