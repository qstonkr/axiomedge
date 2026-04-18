# pyright: reportGeneralTypeIssues=false
"""Glossary lookup tool — 도메인 용어 정의 + 동의어 조회.

axiomedge 의 한국어 도메인 사전 (글러서리) 활용.
agent 가 모호한 용어 만나면 첫 단계로 lookup 후 정제된 query 로 재검색.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agentic.protocols import Tool, ToolResult

logger = logging.getLogger(__name__)


class GlossaryLookupTool(Tool):
    name = "glossary_lookup"
    description = (
        "특정 도메인 용어의 정의·동의어·관련 KB 조회. "
        "예: 'PBU 가 뭐야?' / '미소 매장' 의 정확한 정의 필요할 때. "
        "용어가 KB 에 없으면 entities 검색 (graph_query) 또는 qdrant_search 로 우회."
    )
    args_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "term": {"type": "string", "description": "조회할 용어 (한국어 그대로)"},
            "kb_id": {"type": "string", "description": "특정 KB scope (선택)"},
        },
        "required": ["term"],
    }

    async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        glossary = state.get("glossary") or state.get("glossary_repo")
        if glossary is None:
            return ToolResult(
                success=False, data=None,
                error="glossary repository not initialized in state",
            )

        term = args.get("term", "").strip()
        if not term:
            return ToolResult(success=False, data=None, error="term is required")

        kb_id = args.get("kb_id", "")

        try:
            get_by_term = getattr(glossary, "get_by_term", None)
            if get_by_term is None or not callable(get_by_term):
                return ToolResult(
                    success=False, data=None,
                    error="glossary repo has no get_by_term method",
                )
            entry = await get_by_term(kb_id, term)
            if entry is None:
                return ToolResult(
                    success=True, data=None,
                    metadata={"term": term, "found": False},
                )
            return ToolResult(
                success=True, data=entry,
                metadata={"term": term, "found": True, "kb_id": kb_id or "(any)"},
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("glossary_lookup failed: %s", e)
            return ToolResult(success=False, data=None, error=f"{type(e).__name__}: {e}")
