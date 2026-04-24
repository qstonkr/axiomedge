"""GraphRAG (Neo4j) query tool — 차별화 자산.

axiomedge 의 GraphRAG 자산을 agent 가 명시적으로 활용 가능하게 노출.
- mode="related_chunks": 엔티티 이름 기반 multi-hop traversal → source URI set
- mode="entities": 키워드 기반 entity search
"""

from __future__ import annotations

import logging
from typing import Any

from src.agentic.protocols import Tool, ToolResult
from src.stores.neo4j.errors import NEO4J_FAILURE

logger = logging.getLogger(__name__)


class GraphQueryTool(Tool):
    name = "graph_query"
    description = (
        "Neo4j GraphRAG 그래프에서 엔티티/관계 기반 검색. "
        "사람·매장·시스템 이름이 명시적으로 등장하거나 'X 와 관련된 Y' 같은 "
        "관계 질문에 사용. multi-hop (max_hops 로 제어) 가능. "
        "단순 의미 검색은 qdrant_search 사용."
    )
    args_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string", "enum": ["related_chunks", "entities"],
                "description": "related_chunks: 엔티티 → 관련 문서 / entities: 키워드 → entity 노드",
            },
            "entity_names": {
                "type": "array", "items": {"type": "string"},
                "description": "mode=related_chunks 일 때 — entity 이름 리스트",
            },
            "keywords": {
                "type": "array", "items": {"type": "string"},
                "description": "mode=entities 일 때 — 검색 키워드",
            },
            "max_hops": {"type": "integer", "default": 2, "description": "그래프 traversal 깊이 (1~3)"},
            "max_results": {"type": "integer", "default": 50},
            "kb_ids": {
                "type": "array", "items": {"type": "string"},
                "description": "검색 대상 KB scope",
            },
        },
        "required": ["mode"],
    }

    async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        graph_repo = state.get("graph_repo")
        if graph_repo is None:
            return ToolResult(
                success=False, data=None,
                error="graph_repo not initialized in state (Neo4j unavailable)",
            )

        mode = args.get("mode")
        if mode not in ("related_chunks", "entities"):
            return ToolResult(success=False, data=None, error=f"unknown mode: {mode!r}")

        try:
            if mode == "related_chunks":
                names = args.get("entity_names") or []
                if not names:
                    return ToolResult(
                        success=False, data=None, error="entity_names required for related_chunks",
                    )
                source_uris = await graph_repo.find_related_chunks(
                    names,
                    max_hops=int(args.get("max_hops", 2)),
                    max_results=int(args.get("max_results", 50)),
                    scope_kb_ids=args.get("kb_ids"),
                )
                source_list = sorted(source_uris) if source_uris else []
                return ToolResult(
                    success=True, data=source_list,
                    metadata={
                        "mode": "related_chunks", "entity_count": len(names),
                        "source_count": len(source_list),
                    },
                )
            # mode == "entities"
            keywords = args.get("keywords") or []
            if not keywords:
                return ToolResult(
                    success=False, data=None, error="keywords required for entities",
                )
            entities = await graph_repo.search_entities(
                keywords, max_facts=int(args.get("max_results", 20)),
            )
            return ToolResult(
                success=True, data=entities or [],
                metadata={"mode": "entities", "count": len(entities or [])},
            )
        except NEO4J_FAILURE as e:
            logger.warning("graph_query failed: %s", e)
            return ToolResult(success=False, data=None, error=f"{type(e).__name__}: {e}")
