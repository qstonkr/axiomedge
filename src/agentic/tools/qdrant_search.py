"""Qdrant hybrid search tool — 가장 자주 쓰일 retrieval 도구.

기존 _step_search_collections 와 동일 backend 활용 — embedder + qdrant_search engine.
agent 가 plan 단계에서 query + (optional) kb_ids + top_k 결정.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agentic.protocols import Tool, ToolResult

logger = logging.getLogger(__name__)


def _resolve_kb_id(
    requested: str, active_kbs: list[dict[str, Any]] | None,
) -> str | None:
    """LLM 이 변형해서 보낸 kb_id 를 active KB 의 진짜 id 로 보정.

    매칭 우선순위:
      1. exact (case-insensitive)
      2. requested 가 active id 의 substring 또는 그 반대
         예: 'espa' → 'g-espa', 'home_shopping_AX' → 'hax' (역은 안 됨)
      3. hyphen/underscore 무시 비교
         예: 'home-shopping-ax' → 'hax' 는 안 되지만 'g_espa' → 'g-espa' 는 됨

    못 찾으면 None — 호출자가 unresolved list 로 노출.
    """
    if not requested or active_kbs is None:
        return None
    req = requested.strip().lower()
    if not req:
        return None
    # 1. exact (case-insensitive)
    for kb in active_kbs:
        kid = (kb.get("id") or kb.get("kb_id") or "")
        if kid.lower() == req:
            return kid
    # 2. substring (양방향)
    for kb in active_kbs:
        kid = (kb.get("id") or kb.get("kb_id") or "")
        kid_l = kid.lower()
        if req in kid_l or kid_l in req:
            return kid
    # 3. hyphen/underscore 무시 비교
    req_norm = req.replace("-", "").replace("_", "")
    for kb in active_kbs:
        kid = (kb.get("id") or kb.get("kb_id") or "")
        kid_norm = kid.lower().replace("-", "").replace("_", "")
        if req_norm == kid_norm:
            return kid
    return None


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

        requested_kb_ids = args.get("kb_ids") or []
        top_k = int(args.get("top_k", 5))

        # Resolve active KB list once — needed for both fan-out fallback and
        # fuzzy-id correction (LLM 이 'espa' 처럼 변형해서 보내는 경우).
        active_kbs: list[dict[str, Any]] | None = None
        kb_registry = state.get("kb_registry")
        if kb_registry is not None:
            try:
                organization_id = state.get("organization_id")
                current_user_id = state.get("current_user_id")
                all_kbs = await kb_registry.list_all(organization_id=organization_id)
                active_kbs = [
                    k for k in all_kbs
                    if k.get("status") == "active"
                    and (
                        k.get("tier") != "personal"
                        or k.get("owner_id") == current_user_id
                    )
                ]
            except Exception as e:  # noqa: BLE001 — kb_registry 실패도 detail 노출
                logger.warning("qdrant_search kb_registry.list_all failed: %s", e)
                active_kbs = None  # fall through; explicit ids 가 있으면 그대로 시도

        # Resolve collections — explicit list 는 active KB 매칭 후 사용, 빈
        # list 면 org 의 active KB 들로 fan-out. 옛 ``["knowledge"]`` fallback
        # (존재하지 않는 collection) 은 의도적으로 제거: planner 가 kb_ids 를
        # 안 채워도 검색이 동작해야 빈 답변 / silent failure 회피.
        kb_id_corrections: list[str] = []
        if requested_kb_ids:
            collections: list[str] = []
            unresolved: list[str] = []
            for raw_id in requested_kb_ids:
                resolved = _resolve_kb_id(raw_id, active_kbs)
                if resolved is None:
                    unresolved.append(raw_id)
                    # active KB 정보가 아예 없으면 그대로 시도 (downstream 이 에러로 노출)
                    if active_kbs is None:
                        collections.append(raw_id)
                else:
                    collections.append(resolved)
                    if resolved != raw_id:
                        kb_id_corrections.append(f"{raw_id!r}→{resolved!r}")
            collections = list(dict.fromkeys(collections))  # dedup, preserve order
            if not collections:
                hint = (
                    f"none matched any active KB. requested={requested_kb_ids!r} "
                    f"unresolved={unresolved!r}"
                )
                return ToolResult(success=False, data=None, error=hint)
        else:
            if active_kbs is None:
                return ToolResult(
                    success=False, data=None,
                    error="kb_ids unspecified and kb_registry unavailable in state",
                )
            collections = [
                c for k in active_kbs
                if (c := k.get("id") or k.get("kb_id"))
            ]
            if not collections:
                return ToolResult(
                    success=False, data=None,
                    error="no active KBs available to search in this org",
                )

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
        except Exception as e:  # noqa: BLE001 — embedding 실패도 fan-out 전에 노출
            logger.warning("qdrant_search embed failed: %s", e)
            return ToolResult(
                success=False, data=None,
                error=f"embed failed: {type(e).__name__}: {e}",
            )

        # Fan out across all selected KBs and merge by score. 일부 KB 가 실패해도
        # 다른 KB 결과는 살림 (per_kb_errors 로 부분 실패 노출).
        all_chunks: list[dict[str, Any]] = []
        per_kb_errors: list[str] = []
        for kb_id in collections:
            try:
                results = await search_engine.search(
                    kb_id=kb_id, dense_vector=dense, sparse_vector=sparse, top_k=top_k,
                )
            except Exception as e:  # noqa: BLE001 — partial fan-out tolerance
                per_kb_errors.append(f"{kb_id}: {type(e).__name__}: {e}")
                logger.warning("qdrant_search %s failed: %s", kb_id, e)
                continue
            for r in results:
                all_chunks.append({
                    "chunk_id": r.point_id, "content": r.content, "score": r.score,
                    "kb_id": kb_id, "metadata": r.metadata or {},
                })

        all_chunks.sort(key=lambda c: c.get("score", 0), reverse=True)
        top_chunks = all_chunks[:top_k]

        if not top_chunks:
            err = "; ".join(per_kb_errors) if per_kb_errors else (
                f"no chunks returned across {len(collections)} KB(s)"
            )
            return ToolResult(success=False, data=None, error=err)

        return ToolResult(
            success=True, data=top_chunks,
            metadata={
                "count": len(top_chunks),
                "kb_ids": collections,
                "scanned_kb_count": len(collections),
                "per_kb_errors": per_kb_errors,
                "kb_id_corrections": kb_id_corrections,
            },
        )
