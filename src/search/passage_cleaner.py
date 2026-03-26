"""Passage cleaner for pre-reranking text normalization.

Adapted from oreo-ecosystem passage_cleaner.py.
Cleans retrieved passages before cross-encoder reranking to improve quality.

Features:
- Whitespace normalization (collapse runs, trim excess newlines)
- Sentence-level deduplication (case-insensitive exact match)
- Incomplete fragment removal (trailing text < 15 chars without punctuation)
- Minimum content length filtering
"""

from __future__ import annotations

import re
from typing import Any


# Sentence boundary pattern for Korean + English
_SENTENCE_END_RE = re.compile(r'[.!?。다요음]\s*$')


def clean_passage(text: str, min_length: int = 10) -> str:
    """Clean a single passage for reranking.

    1. Normalize whitespace
    2. Remove duplicate sentences
    3. Remove trailing incomplete fragments
    """
    if not text or len(text.strip()) < min_length:
        return text

    # 1. Whitespace normalization
    text = re.sub(r'[^\S\n]+', ' ', text)  # collapse horizontal whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)  # max 2 consecutive newlines
    text = text.strip()

    # 2. Sentence deduplication
    lines = text.split('\n')
    seen: set[str] = set()
    deduped: list[str] = []
    for line in lines:
        key = line.strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(line)
        elif not key:
            deduped.append(line)  # preserve blank lines
    text = '\n'.join(deduped)

    # 3. Remove trailing incomplete fragment (< 15 chars without sentence end)
    text = text.rstrip()
    if len(text) > 15:
        last_chunk = text[-15:]
        if not _SENTENCE_END_RE.search(last_chunk):
            for sep in ('.', '다.', '요.', '!', '?', '。'):
                pos = text.rfind(sep)
                if pos > len(text) - 100 and pos > 0:
                    text = text[:pos + 1]
                    break

    return text


def clean_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clean all retrieved chunks before reranking."""
    cleaned = []
    for chunk in chunks:
        content = chunk.get('content', '')
        cleaned_content = clean_passage(content)
        if cleaned_content and len(cleaned_content) >= 10:
            chunk_copy = dict(chunk)
            chunk_copy['content'] = cleaned_content
            cleaned.append(chunk_copy)
    return cleaned
