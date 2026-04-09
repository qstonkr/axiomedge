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


_INJECTION_PATTERNS = re.compile(
    r"(?i)"
    r"(?:ignore\s+(?:all\s+)?(?:previous|above|prior)\s+instructions)"
    r"|(?:disregard\s+(?:all\s+)?(?:previous|above|prior))"
    r"|(?:you\s+are\s+now\s+(?:a|an|in)\s+)"
    r"|(?:system\s*:\s*)"
    r"|(?:\[SYSTEM\]|\[INST\]|\[/INST\])"
    r"|(?:<<SYS>>|<</SYS>>|<\|im_start\|>)"
    r"|(?:이전\s*(?:지시|명령|설정).*(?:무시|잊어|취소))"
    r"|(?:시스템\s*(?:프롬프트|설정).*(?:변경|무시|초기화))"
    r"|(?:(?:모든|전체)\s*문서.*(?:출력|보여|표시))"
)


def sanitize_text(text: str, max_length: int = weights.llm.max_query_length) -> str:
    """Input sanitization with length truncation and prompt injection defense."""
    if not text:
        return ""
    sanitized = text.strip()
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
    sanitized = _INJECTION_PATTERNS.sub("[BLOCKED]", sanitized)
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
