"""KB lister tool — 가용 KB 목록 + 메타데이터.

agent 가 어떤 KB 를 검색할지 결정 못 했을 때 첫 단계로 호출.
KBConfigModel 의 status='active' 만 반환.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agentic.protocols import Tool, ToolResult

logger = logging.getLogger(__name__)


class KBListerTool(Tool):
    name = "kb_list"
    description = (
        "현재 검색 가능한 KB 목록 + 간단 메타 (이름/설명/문서수) 반환. "
        "Agent 가 KB scope 를 결정하지 못했을 때 첫 단계로 호출. "
        "결과를 보고 qdrant_search 의 kb_ids 를 채움."
    )
    args_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tier": {
                "type": "string", "enum": ["global", "team", "personal"],
                "description": "특정 tier 만 (선택)",
            },
            "limit": {"type": "integer", "default": 50},
        },
    }

    async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        kb_registry = state.get("kb_registry")
        if kb_registry is None:
            return ToolResult(
                success=False, data=None,
                error="kb_registry not initialized in state",
            )

        try:
            limit = int(args.get("limit", 50))
            tier_filter = args.get("tier")
            kbs = await kb_registry.list_all(limit=limit)
            if tier_filter:
                kbs = [k for k in kbs if k.get("tier") == tier_filter]
            # active only
            kbs = [k for k in kbs if k.get("status") == "active"]
            # 단순화 — agent 가 prompt 에 토큰 적게 쓰도록
            slim = [
                {
                    "kb_id": k.get("id") or k.get("kb_id"),
                    "name": k.get("name", ""),
                    "description": (k.get("description") or "")[:200],
                    "tier": k.get("tier"),
                    "document_count": k.get("document_count", 0),
                }
                for k in kbs
            ]
            return ToolResult(
                success=True, data=slim,
                metadata={"count": len(slim), "tier_filter": tier_filter},
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("kb_list failed: %s", e)
            return ToolResult(success=False, data=None, error=f"{type(e).__name__}: {e}")
