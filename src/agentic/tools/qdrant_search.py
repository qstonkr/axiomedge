"""Qdrant hybrid search tool — 가장 자주 쓰일 retrieval 도구.

기존 _step_search_collections 와 동일 backend 활용 — embedder + qdrant_search engine.
agent 가 plan 단계에서 query + (optional) kb_ids + top_k 결정.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agentic.protocols import Tool, ToolResult

logger = logging.getLogger(__name__)


class QdrantSearchTool(Tool):
    name = "qdrant_search"
    description = (
        "벡터 + sparse hybrid search 로 KB 에서 관련 chunk 를 찾는다. "
        "일반적인 의미 검색 / 사실 lookup 에 사용. "
        "entity 관계나 multi-hop 추론이 필요하면 graph_query 사용."
    )
    args_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "검색 질의 (한국어 그대로)"},
            "kb_ids": {
                "type": "array", "items": {"type": "string"},
                "description": "검색 대상 KB 목록. 비우면 모든 활성 KB.",
            },
            "top_k": {"type": "integer", "default": 5, "description": "반환할 chunk 수"},
        },
        "required": ["query"],
    }

    async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(success=False, data=None, error="query is required")

        embedder = state.get("embedder")
        search_engine = state.get("qdrant_search")
        if embedder is None or search_engine is None:
            return ToolResult(
                success=False, data=None,
                error="qdrant_search or embedder not initialized in state",
            )

        kb_ids = args.get("kb_ids") or []
        top_k = int(args.get("top_k", 5))

        try:
            # Embed query (sync via to_thread inside embedder usually)
            encode_fn = getattr(embedder, "encode", None)
            if encode_fn is not None:
                import asyncio as _asyncio
                emb = await _asyncio.to_thread(
                    lambda: encode_fn([query], return_dense=True, return_sparse=True),
                )
                dense = emb["dense_vecs"][0]
                sparse = emb.get("lexical_weights", [None])[0]
            else:
                dense = (await embedder.embed_documents([query]))[0]
                sparse = None

            # Resolve collections — empty kb_ids → use ['knowledge'] as default
            collections = kb_ids or ["knowledge"]
            results = await search_engine.search(
                kb_id=collections[0], dense_vector=dense, sparse_vector=sparse, top_k=top_k,
            )
            chunks = [
                {
                    "chunk_id": r.point_id, "content": r.content, "score": r.score,
                    "kb_id": collections[0], "metadata": r.metadata or {},
                }
                for r in results
            ]
            return ToolResult(
                success=True, data=chunks,
                metadata={"count": len(chunks), "kb_ids": collections},
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("qdrant_search failed: %s", e)
            return ToolResult(success=False, data=None, error=f"{type(e).__name__}: {e}")
