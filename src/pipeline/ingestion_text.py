"""Text processing utilities for ingestion pipeline.

Functions for cleaning, summarizing, and building context prefixes
for document chunks before embedding.
"""

from __future__ import annotations

import re

from src.core.models import RawDocument

_MAX_PREFIX_CHARS = 150  # Prefix should be <15% of typical 2500-char chunk


def extract_document_summary(content: str, max_len: int = 200) -> str:
    """Extract leading summary from document content.

    Cuts at last sentence boundary within max_len chars.
    """
    if not content or len(content) <= max_len:
        return content.strip()
    summary = content[:max_len].strip()
    for sep in (".", "。", "\n", "다.", "요."):
        pos = summary.rfind(sep)
        if pos > 50:
            return summary[: pos + 1]
    return summary


def clean_text_for_embedding(text: str) -> str:
    """Preprocess text before chunking/embedding.

    - Remove HTML tags
    - Normalize whitespace
    - Remove control characters
    """
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _trim_incomplete_tail(text: str) -> str:
    """Trim incomplete sentence fragment from the end of text."""
    _SENTENCE_ENDINGS = (".", "다.", "요.", "!", "?", "。")
    last_15 = text[-15:]
    if any(last_15.endswith(p) for p in _SENTENCE_ENDINGS):
        return text
    for sep in _SENTENCE_ENDINGS:
        pos = text.rfind(sep)
        if pos > len(text) - 100 and pos > 0:
            return text[: pos + 1]
    return text


def clean_passage(text: str) -> str:
    """Clean a single passage: dedup sentences, remove incomplete fragments."""
    if not text:
        return text
    lines = text.split("\n")
    seen: set[str] = set()
    cleaned: list[str] = []
    for line in lines:
        key = line.strip().lower()
        if key and key not in seen:
            seen.add(key)
            cleaned.append(line)
    result = "\n".join(cleaned).rstrip()
    if len(result) > 15:
        result = _trim_incomplete_tail(result)
    return result


def shorten_title(title: str, max_len: int = 60) -> str:
    """Shorten long filenames: remove S3 numeric IDs, truncate."""
    title = re.sub(r"^\d{10,}_", "", title)
    title = re.sub(r"\.(pptx?|pdf|docx?|xlsx?|csv)$", "", title, flags=re.IGNORECASE)
    if len(title) > max_len:
        title = title[:max_len] + "…"
    return title


def build_document_context_prefix(
    raw: RawDocument,
    *,
    heading_path: str = "",
    chunk_type: str = "body",
    chunk_index: int = 0,
    total_chunks: int = 0,
    doc_summary: str = "",
) -> str:
    """Build a compact document context prefix for embedding.

    Target: < 150 chars to avoid diluting chunk content in embeddings.
    Format: [Context] {short_title} | Section {i}/{n}
    """
    parts: list[str] = []

    short_title = shorten_title(raw.title) if raw.title else ""
    context_parts: list[str] = []
    if short_title:
        context_parts.append(short_title)
    if raw.author:
        context_parts.append(raw.author)
    if total_chunks > 0:
        context_parts.append(f"§{chunk_index + 1}/{total_chunks}")
    if context_parts:
        parts.append(f"[Context] {' | '.join(context_parts)}")

    if doc_summary and len(doc_summary) <= 80:
        parts.append(f"[Summary] {doc_summary}")

    if heading_path:
        parts.append(f"[섹션: {heading_path}]")

    if chunk_type != "body":
        parts.append(f"[유형: {chunk_type}]")

    if not parts:
        return ""

    prefix = "\n".join(parts) + "\n\n"

    if len(prefix) > _MAX_PREFIX_CHARS:
        prefix = prefix[:_MAX_PREFIX_CHARS - 3] + "…\n\n"

    return prefix
