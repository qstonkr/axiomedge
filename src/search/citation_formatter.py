"""Citation Formatter.

Purpose:
    Provide a single citation formatting contract across knowledge responses.

Features:
    - Normalize citations from dict payloads or source attribution objects.
    - Render consistent markdown citation text.
    - Preserve structured citation metadata for API responses.

Usage:
    entries = CitationFormatter.from_sources(sources_summary)
    markdown = CitationFormatter.format_markdown(entries)

Examples:
    [1] [K8s 운영 가이드](https://wiki.example.com/page/123) (score=0.92)

Extracted from oreo-ecosystem citation_formatter.py.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CitationEntry:
    """Canonical citation entry for text + structured response rendering."""

    index: int
    ref: str
    document_name: str
    kb_name: str | None = None
    source_uri: str | None = None
    relevance_score: float | None = None
    is_stale: bool = False
    freshness_warning: str | None = None
    days_since_update: int | None = None
    updated_at: str | None = None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_source_attr(source: Any, name: str, default: Any = None) -> Any:
    """Return a source attribute with dict/object compatibility."""
    return getattr(source, name, default)


def _build_citation_entry(idx: int, citation: dict[str, Any]) -> CitationEntry:
    """Build a single CitationEntry from a response citation dict."""
    ref = str(citation.get("ref") or idx)
    document_name = str(citation.get("document_name") or f"문서 {idx}")
    source_uri = citation.get("url") or citation.get("source_uri")
    relevance = _safe_float(
        citation.get("relevance_score")
        if "relevance_score" in citation
        else citation.get("score")
    )
    return CitationEntry(
        index=idx,
        ref=ref,
        document_name=document_name,
        kb_name=(str(citation.get("kb_name")).strip() or None)
        if citation.get("kb_name") is not None
        else None,
        source_uri=str(source_uri).strip() if source_uri else None,
        relevance_score=relevance,
        is_stale=bool(citation.get("is_stale", False)),
        freshness_warning=(
            str(citation.get("freshness_warning")).strip() or None
            if citation.get("freshness_warning") is not None
            else None
        ),
        days_since_update=_safe_int(citation.get("days_since_update")),
        updated_at=(
            str(citation.get("updated_at")).strip() or None
            if citation.get("updated_at") is not None
            else None
        ),
    )


def _build_citation_from_source(idx: int, source: Any) -> CitationEntry:
    """Build a CitationEntry from a source attribution object or dict."""
    if isinstance(source, Mapping):
        read = source.get
    else:
        def read(name: str, default: Any = None, *, _source: Any = source) -> Any:
            return _read_source_attr(_source, name, default)

    document_name = (
        str(
            read("document_name")
            or read("title")
            or read("source_id")
            or f"문서 {idx}"
        ).strip()
        or f"문서 {idx}"
    )

    score = _safe_float(
        read("relevance_score")
        if read("relevance_score") is not None
        else read("score", read("relevance"))
    )

    source_uri = read("source_uri") or read("url")

    return CitationEntry(
        index=idx,
        ref=str(read("ref") or idx),
        document_name=document_name,
        kb_name=str(read("kb_name")).strip() if read("kb_name") else None,
        source_uri=str(source_uri).strip() if source_uri else None,
        relevance_score=score,
        is_stale=bool(read("is_stale", False)),
        freshness_warning=(
            str(read("freshness_warning")).strip() or None
            if read("freshness_warning") is not None
            else None
        ),
        days_since_update=_safe_int(read("days_since_update")),
        updated_at=(
            str(read("updated_at")).strip() or None
            if read("updated_at") is not None
            else None
        ),
    )


class CitationFormatter:
    """Normalize and render citations with a single output convention."""

    DEFAULT_HEADING = "**📖 참고 문서:**"

    @classmethod
    def from_response_citations(cls, citations: list[dict[str, Any]]) -> list[CitationEntry]:
        """Normalize TieredResponse citation dictionaries."""
        return [_build_citation_entry(idx, c) for idx, c in enumerate(citations, start=1)]

    @classmethod
    def from_sources(cls, sources: Iterable[Any]) -> list[CitationEntry]:
        """Normalize source attribution objects into canonical citation entries."""
        return [
            _build_citation_from_source(idx, source)
            for idx, source in enumerate(sources, start=1)
        ]

    @classmethod
    def format_markdown(
        cls,
        entries: list[CitationEntry],
        *,
        include_heading: bool = True,
        heading: str | None = None,
    ) -> str | None:
        """Render canonical markdown citation block."""
        if not entries:
            return None

        lines: list[str] = []
        if include_heading:
            lines.append(heading if heading is not None else cls.DEFAULT_HEADING)

        for entry in entries:
            if entry.source_uri:
                line = f"[{entry.ref}] [{entry.document_name}]({entry.source_uri})"
            else:
                line = f"[{entry.ref}] {entry.document_name}"
            if entry.relevance_score is not None:
                line = f"{line} (score={entry.relevance_score:.2f})"
            lines.append(line)

        return "\n".join(lines)


__all__ = ["CitationEntry", "CitationFormatter"]
