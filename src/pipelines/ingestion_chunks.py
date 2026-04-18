# pyright: reportAttributeAccessIssue=false
"""Ingestion pipeline — chunk building, OCR splitting, morpheme extraction, context prefixes.

Extracted from ingestion.py for module size management.
"""

from __future__ import annotations

import asyncio
import logging
import re as _re_morph
from typing import Any

from src.config.weights import weights
from src.core.models import IngestionResult, RawDocument
from .chunker import Chunker
from .document_parser import ParseResult, _table_to_markdown
from .ingestion_helpers import _BINARY_EXTENSIONS
from .ingestion_text import (
    extract_document_summary as _extract_document_summary,
    clean_text_for_embedding as _clean_text_for_embedding,
    clean_passage as _clean_passage,
    build_document_context_prefix as _build_document_context_prefix,
)
from .document_parser import parse_bytes_enhanced

logger = logging.getLogger(__name__)


def try_binary_parse(raw: RawDocument) -> ParseResult | None:
    """Attempt enhanced binary parsing for known file extensions."""
    filename = raw.metadata.get("filename", "")
    filename_lower = filename.lower() if filename else ""
    if not filename_lower or not any(filename_lower.endswith(ext) for ext in _BINARY_EXTENSIONS):
        return None
    try:
        file_bytes = raw.metadata.get("file_bytes")
        if isinstance(file_bytes, bytes):
            return parse_bytes_enhanced(file_bytes, filename)
    except (
        RuntimeError, OSError, ValueError, TypeError,
        KeyError, AttributeError, ImportError,
    ) as e:
        logger.warning(
            "Enhanced parsing failed for doc_id=%s: %s",
            raw.doc_id, e,
        )
    return None


def build_body_chunks(
    chunk_result: Any, heading_map: dict[int, str],
) -> list[tuple[str, str, str]]:
    """Convert chunk result into typed body chunks."""
    return [
        (chunk_text, "body", heading_map.get(idx, ""))
        for idx, chunk_text in enumerate(chunk_result.chunks)
    ]


def append_table_chunks(
    typed_chunks: list[tuple[str, str, str]], parse_result: ParseResult | None,
) -> None:
    """Append table chunks from parse result."""
    if not parse_result or not parse_result.tables:
        return
    for table_data in parse_result.tables:
        table_md = _table_to_markdown(table_data)
        if table_md.strip():
            typed_chunks.append((table_md, "table", ""))


async def split_ocr_text(
    ocr_text: str, chunker: Chunker,
) -> list[tuple[str, str, str]]:
    """Split OCR text into typed chunks by page/slide/image boundaries."""
    import re as _re
    page_segments = _re.split(r'(?=\[(?:Page|Slide)\s+\d+[^\]]*\])', ocr_text)
    if len(page_segments) <= 1:
        page_segments = _re.split(r'(?=\[Image\s+\d+[^\]]*\])', ocr_text)

    ocr_chunks: list[tuple[str, str, str]] = []
    for seg in page_segments:
        seg = seg.strip()
        if not seg:
            continue
        if len(seg) > weights.chunking.max_chunk_chars:
            sub_result = await asyncio.to_thread(chunker.chunk, seg)
            for sc in sub_result.chunks:
                ocr_chunks.append((sc, "ocr", ""))
        else:
            ocr_chunks.append((seg, "ocr", ""))
    return ocr_chunks


async def build_typed_chunks(
    raw: RawDocument,
    parse_result: ParseResult | None,
    chunker: Chunker,
) -> tuple[list[tuple[str, str, str]], dict[int, str], str] | IngestionResult:
    """Parse, chunk, and clean document content.

    Returns:
        (typed_chunks, heading_map, doc_summary) on success,
        or IngestionResult on failure.
    """
    if parse_result is None:
        parse_result = try_binary_parse(raw)

    body_content = parse_result.text if parse_result else raw.content
    body_content = _clean_text_for_embedding(body_content)
    doc_summary = _extract_document_summary(body_content)

    if raw.metadata.get("_is_legal_document"):
        chunk_result = await asyncio.to_thread(
            chunker.chunk_legal_articles, body_content,
        )
    else:
        chunk_result = await asyncio.to_thread(
            chunker.chunk_with_headings, body_content,
        )
    has_extra = parse_result and (
        parse_result.tables or parse_result.ocr_text
    )
    if not chunk_result.chunks and not has_extra:
        return IngestionResult.failure_result(
            reason="No chunks produced from document content", stage="chunk",
        )

    heading_map: dict[int, str] = {}
    if chunk_result.heading_chunks:
        for i, hc in enumerate(chunk_result.heading_chunks):
            heading_map[i] = hc.heading_path

    typed_chunks = build_body_chunks(chunk_result, heading_map)
    append_table_chunks(typed_chunks, parse_result)

    if parse_result and parse_result.ocr_text.strip():
        ocr_chunks = await split_ocr_text(parse_result.ocr_text.strip(), chunker)
        typed_chunks.extend(ocr_chunks)

    if not typed_chunks:
        return IngestionResult.failure_result(
            reason="No typed chunks produced from document content", stage="chunk",
        )

    cleaned_chunks = [
        (cleaned, ct, hp)
        for chunk_text, ct, hp in typed_chunks
        if (cleaned := _clean_passage(chunk_text)).strip()
    ]
    typed_chunks = cleaned_chunks if cleaned_chunks else typed_chunks

    return typed_chunks, heading_map, doc_summary


def extract_morphemes(typed_chunks: list[tuple[str, str, str]]) -> list[str]:
    """Extract KiwiPy morphemes from typed chunks."""
    try:
        from kiwipiepy import Kiwi as _Kiwi
        _kiwi = _Kiwi()
        _noun_tags = {"NNG", "NNP", "SL"}
        morphemes = []
        for chunk_text, _, _ in typed_chunks:
            tokens = _kiwi.tokenize(chunk_text[:2000])
            morphs = " ".join(t.form for t in tokens if t.tag in _noun_tags and len(t.form) >= 2)
            morphemes.append(morphs)
        return morphemes
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        return [""] * len(typed_chunks)


def append_date_author_tokens(
    chunk_morphemes: list[str], title: str | None, author: str | None,
) -> list[str]:
    """Append date/author tokens to morphemes for sparse matching."""
    _dm = _re_morph.search(r"(20\d{2})[_\-./](0[1-9]|1[0-2])", title or "")
    if not _dm:
        _dm = _re_morph.search(r"(20\d{2})년\s*(\d{1,2})월", title or "")
    _date_tokens = ""
    if _dm:
        _y, _m = _dm.group(1), str(int(_dm.group(2))).zfill(2)
        _date_tokens = f" {_y} {_y}년 {int(_m)}월 {_y}_{_m}"
    _wk = _re_morph.search(r"(\d{1,2})월\s*(\d)주차", title or "")
    if _wk:
        _date_tokens += f" {_wk.group(1)}월 {_wk.group(2)}주차"
    if author:
        _date_tokens += f" {author}"
    if _date_tokens:
        return [m + _date_tokens for m in chunk_morphemes]
    return chunk_morphemes


def add_context_prefixes(
    raw: RawDocument,
    typed_chunks: list[tuple[str, str, str]],
    doc_summary: str,
) -> tuple[list[str], list[str], list[str]]:
    """Add document context prefix to each chunk. Returns (prefixed, types, heading_paths)."""
    total = len(typed_chunks)
    prefixed_chunks: list[str] = []
    chunk_types: list[str] = []
    chunk_heading_paths: list[str] = []
    for idx, (chunk_text, chunk_type, heading_path) in enumerate(typed_chunks):
        doc_prefix = _build_document_context_prefix(
            raw, heading_path=heading_path, chunk_type=chunk_type,
            chunk_index=idx, total_chunks=total, doc_summary=doc_summary,
        )
        prefixed_chunks.append(f"{doc_prefix}{chunk_text}" if doc_prefix else chunk_text)
        chunk_types.append(chunk_type)
        chunk_heading_paths.append(heading_path)
    return prefixed_chunks, chunk_types, chunk_heading_paths
