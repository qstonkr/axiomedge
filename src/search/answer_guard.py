"""Generic Answer Detection Guard.

Detects vague / non-specific LLM answers and replaces them with
chunk-based deterministic fallback. Extracted from oreo-ecosystem
HubSearchService._should_replace_enriched_answer() and _build_fallback_answer().

Usage:
    from src.search.answer_guard import AnswerGuard

    guard = AnswerGuard()
    final_answer = guard.guard(answer, chunks, query)
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Generic / vague answer patterns (Korean)
# ---------------------------------------------------------------------------

_GENERIC_FALLBACK_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        # "제공된 문서에는 ... 포함되어 있지 않"
        r"^제공된 문서들?에는 .*포함되어 있지 않",
        # "제공된 문서 ... 직접적인 ... 정보 ... 찾을 수 없"
        r"^제공된 문서들?.*직접적인 .*정보.*찾을 수 없",
        r"^직접적인 .*정보를 .*찾을 수 없",
        # "현재 제공된 정보로는 ... 어렵"
        r"^현재 제공된 정보로는 .*어렵",
        # Additional Korean generic patterns
        r"관련 부서에 문의",
        r"확인 후 답변",
        r"정보를 찾을 수 없습니다",
        r"해당 정보가 없",
        r"관련 자료가 충분하지 않",
        r"답변을 드리기 어렵",
        r"확인이 필요합니다",
        r"담당자에게 문의.*바랍니다$",
    )
)


def _should_replace(
    answer: str,
    *,
    chunks: list[dict[str, Any]],
    disclaimer: str | None = None,
) -> bool:
    """Return True when the answer should be replaced with a fallback.

    Criteria:
    - Empty or whitespace-only answer.
    - No search chunks found.
    - Disclaimer indicates no grounding documents.
    - Short answer (<= 200 chars) starting with a generic no-answer pattern.
    """
    if not answer.strip():
        return True
    if not chunks:
        return True
    if disclaimer and "검색된 근거 문서 없이" in disclaimer:
        return True

    normalized = " ".join(answer.split())
    # Long answers are unlikely to be generic rejections
    if len(normalized) > 200:
        return False

    return any(
        pattern.search(normalized)
        for pattern in _GENERIC_FALLBACK_PATTERNS
    )


def _grounded_snippet(content: str, limit: int = 180) -> str:
    """Produce a safe text snippet from chunk content."""
    normalized = " ".join(str(content or "").split())
    if not normalized:
        return "본문 미리보기를 제공할 수 없습니다."
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _build_fallback_answer(query: str, chunks: list[dict[str, Any]]) -> str:
    """Build a deterministic grounded fallback from chunk content."""
    if not chunks:
        return "검색 조건에 맞는 문서를 찾지 못했습니다. 다른 키워드로 다시 시도해 주세요."

    lines = [f"'{query}' 관련 검색 결과를 기준으로 확인된 내용입니다."]
    for index, chunk in enumerate(chunks[:3], start=1):
        title = chunk.get("document_name") or chunk.get("kb_id") or f"문서 {index}"
        kb_name = chunk.get("kb_id") or "지식 베이스"
        snippet = _grounded_snippet(chunk.get("content", ""))
        lines.append(f"{index}. {title} ({kb_name}): {snippet}")
    if len(chunks) > 3:
        lines.append(f"추가로 확인된 관련 문서 {len(chunks) - 3}건이 더 있습니다.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class AnswerGuard:
    """Guard that replaces generic LLM answers with chunk-based fallback.

    Usage::

        guard = AnswerGuard()
        answer = guard.guard(llm_answer, chunks, query)
    """

    def guard(
        self,
        answer: str | None,
        chunks: list[dict[str, Any]],
        query: str,
        *,
        disclaimer: str | None = None,
    ) -> str:
        """Return *answer* as-is if grounded, or a deterministic fallback.

        Args:
            answer: LLM-generated answer candidate (may be None/empty).
            chunks: Search result chunks (list of dicts with ``content``,
                ``document_name``, ``kb_id`` keys).
            query: Original user query.
            disclaimer: Optional disclaimer string from answer enrichment.

        Returns:
            Grounded answer or deterministic fallback string.
        """
        if answer and not _should_replace(
            answer, chunks=chunks, disclaimer=disclaimer
        ):
            return answer
        return _build_fallback_answer(query, chunks)


__all__ = [
    "AnswerGuard",
    "_GENERIC_FALLBACK_PATTERNS",
]
