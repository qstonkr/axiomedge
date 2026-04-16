"""Input validation and sanitization for Knowledge Dashboard.

Copied from oreo-ecosystem without changes.
"""

from __future__ import annotations

import html
import re
import unicodedata


def sanitize_input(text: str, max_length: int = 1000) -> str:
    """Strip whitespace, truncate to max_length, and remove control characters."""
    if not text:
        return ""
    result = text.strip()
    result = "".join(
        ch for ch in result
        if not unicodedata.category(ch).startswith("C") or ch in ("\n", "\t")
    )
    result = re.sub(r"[ \t]+", " ", result)
    if len(result) > max_length:
        result = result[:max_length]
    return result


def validate_query(query: str, max_length: int = 500) -> str:
    """Validate and sanitize a search query."""
    sanitized = sanitize_input(query, max_length=max_length)
    if not sanitized:
        raise ValueError("Search query must not be empty.")
    return sanitized


def validate_page_params(
    page: int,
    page_size: int,
    max_page_size: int = 100,
) -> tuple[int, int]:
    """Bound pagination parameters to safe ranges."""
    safe_page = max(1, int(page))
    safe_page_size = max(1, min(int(page_size), max_page_size))
    return safe_page, safe_page_size


_KB_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_kb_id(kb_id: str) -> str:
    """Validate KB identifier format."""
    if not kb_id:
        raise ValueError("KB ID must not be empty.")
    stripped = kb_id.strip()
    if len(stripped) > 50:
        raise ValueError(f"KB ID exceeds maximum length of 50 characters (got {len(stripped)}).")
    if not _KB_ID_PATTERN.match(stripped):
        raise ValueError(
            f"KB ID contains invalid characters: '{stripped}'. "
            "Only alphanumeric, hyphens, and underscores are allowed."
        )
    return stripped


def sanitize_html(text: str) -> str:
    """Escape HTML entities for safe display."""
    if not text:
        return ""
    return html.escape(text, quote=True)
