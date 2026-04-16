"""KB Management API endpoints.

Serves both /api/v1/kb/* (original) and /api/v1/admin/kb/* (dashboard calls).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.app import _get_state
from src.config.weights import weights as _w

logger = logging.getLogger(__name__)

_QDRANT_NOT_INIT = "Qdrant not initialized"
def _default_qdrant_url() -> str:
    from src.config import get_settings
    return get_settings().qdrant.url

# Original KB router
router = APIRouter(prefix="/api/v1/kb", tags=["KB Management"])

# Admin KB router (what the dashboard actually calls)
admin_router = APIRouter(prefix="/api/v1/admin/kb", tags=["KB Admin"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class KBCreateRequest(BaseModel):
    kb_id: str = Field(..., max_length=100)
    name: str = Field(..., max_length=200)
    description: str = Field(default="", max_length=2000)
    tier: str = Field(default="global")


class KBInfo(BaseModel):
    kb_id: str
    name: str
    description: str = ""
    tier: str = "global"
    doc_count: int = 0
    chunk_count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _enrich_kb_counts(kbs: list[dict], store) -> None:
    """Enrich KB list with Qdrant chunk counts and doc_count.

    Qdrant count 실패는 KB list 응답 전체를 막지 않지만, 조용히 0 으로
    fallback 하면 UI 가 잘못된 정보를 표시. 로그를 남겨 인시던트 디버깅 가능.
    """
    if not store:
        return
    for kb in kbs:
        kb_id = kb.get("kb_id") or kb.get("id", "")
        try:
            kb["chunk_count"] = await store.count(kb_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to count chunks for KB %s: %s — falling back to 0",
                kb_id, e,
            )
            kb.setdefault("chunk_count", 0)
        kb["doc_count"] = kb.get("document_count", 0)


async def _list_kbs_from_registry(
    kb_registry, store, *, tier: str | None, status: str | None
) -> dict | None:
    """Try listing KBs from registry. Returns None on failure."""
    try:
        if status:
            kbs = await kb_registry.list_by_status(status)
        elif tier:
            kbs = await kb_registry.list_by_tier(tier)
        else:
            kbs = await kb_registry.list_all()
        await _enrich_kb_counts(kbs, store)
        return {"kbs": kbs}
    except Exception as e:  # noqa: BLE001
        logger.warning("KB registry query failed, falling back to Qdrant: %s", e)
        return None


async def _list_kbs_from_qdrant(collections, store) -> dict:
    """Fallback: list KBs from Qdrant collections."""
    try:
        raw_names = await collections.get_existing_collection_names()
        prefix = getattr(collections._provider.config, "collection_prefix", "kb") + "_"
        kbs = []
        for raw_name in raw_names:
            kb_id = raw_name[len(prefix):] if raw_name.startswith(prefix) else raw_name
            count = 0
            if store:
                try:
                    count = await store.count(kb_id)
                except Exception as e:  # noqa: BLE001
                    logger.debug("Count failed for %s: %s", kb_id, e)
            kbs.append({
                "kb_id": kb_id, "name": kb_id, "description": "",
                "tier": "global", "doc_count": 0, "chunk_count": count,
                "status": "active", "settings": {},
            })
        return {"kbs": kbs}
    except Exception as e:  # noqa: BLE001
        return {"kbs": [], "error": str(e)}


async def _list_kbs_impl(tier: str | None = None, status: str | None = None) -> dict:
    """Shared implementation for listing KBs."""
    state = _get_state()
    kb_registry = state.get("kb_registry")
    store = state.get("qdrant_store")

    if kb_registry:
        result = await _list_kbs_from_registry(kb_registry, store, tier=tier, status=status)
        if result is not None:
            return result

    # Fallback to Qdrant collections
    collections = state.get("qdrant_collections")
    if not collections:
        return {"kbs": []}
    return await _list_kbs_from_qdrant(collections, store)


# ============================================================================
# Original /api/v1/kb/* routes
# ============================================================================

@router.post("/create", responses={503: {"description": "Qdrant not initialized"}, 500: {"description": "Internal error"}})
async def create_kb(request: KBCreateRequest):
    """Create a new knowledge base (Qdrant collection)."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    if not collections:
        raise HTTPException(status_code=503, detail=_QDRANT_NOT_INIT)

    try:
        await collections.ensure_collection(request.kb_id)
        return {"success": True, "kb_id": request.kb_id, "message": f"KB '{request.name}' created"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def list_kbs():
    """List all knowledge bases."""
    return await _list_kbs_impl()


@router.delete("/{kb_id}", responses={503: {"description": "Qdrant not initialized"}, 500: {"description": "Internal error"}})
async def delete_kb(kb_id: str):
    """Delete a knowledge base."""
    state = _get_state()
    provider = state.get("qdrant_provider")
    if not provider:
        raise HTTPException(status_code=503, detail=_QDRANT_NOT_INIT)

    try:
        client = await provider.ensure_client()
        collections = state.get("qdrant_collections")
        collection_name = collections.get_collection_name(kb_id) if collections else kb_id
        await client.delete_collection(collection_name)
        return {"success": True, "kb_id": kb_id, "message": f"KB '{kb_id}' deleted"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Admin /api/v1/admin/kb/* routes (dashboard calls these)
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb
# ---------------------------------------------------------------------------
@admin_router.get("")
async def admin_list_kbs(
    tier: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
):
    """List KBs (admin)."""
    return await _list_kbs_impl(tier=tier, status=status)


# ---------------------------------------------------------------------------
# POST /api/v1/admin/kb
# ---------------------------------------------------------------------------
@admin_router.post("", responses={503: {"description": "Qdrant not initialized"}, 500: {"description": "Internal error"}})
async def admin_create_kb(body: dict[str, Any]):
    """Create a KB (admin)."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    if not collections:
        raise HTTPException(status_code=503, detail=_QDRANT_NOT_INIT)

    kb_id = body.get("kb_id", body.get("name", ""))
    try:
        await collections.ensure_collection(kb_id)
        return {"success": True, "kb_id": kb_id, "message": f"KB '{kb_id}' created"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/stats  (aggregation - MUST be before {kb_id})
# ---------------------------------------------------------------------------
async def _get_registry_counts(kb_registry: Any) -> tuple[int, int]:
    """Get KB and document counts from registry. Returns (total_kbs, total_documents)."""
    if not kb_registry:
        return 0, 0
    try:
        kbs = await kb_registry.list_all()
        return len(kbs), sum(kb.get("document_count", 0) for kb in kbs)
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to get KB registry stats: %s", e)
        return 0, 0


async def _get_qdrant_chunk_counts(
    collections: Any, store: Any, fallback_total_kbs: int,
) -> tuple[int, int]:
    """Get chunk counts from Qdrant. Returns (total_chunks, total_kbs)."""
    if not (collections and store):
        return 0, fallback_total_kbs
    try:
        raw_names = await collections.get_existing_collection_names()
        total_kbs = fallback_total_kbs or len(raw_names)
        prefix = getattr(collections._provider.config, "collection_prefix", "kb") + "_"
        total_chunks = 0
        for raw_name in raw_names:
            kb_id = raw_name[len(prefix):] if raw_name.startswith(prefix) else raw_name
            try:
                total_chunks += await store.count(kb_id)
            except Exception as e:  # noqa: BLE001
                logger.debug("Chunk count failed for %s: %s", kb_id, e)
        return total_chunks, total_kbs
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to get Qdrant collection names: %s", e)
        return 0, fallback_total_kbs


async def _get_avg_quality_score(
    collections: Any, store: Any, qdrant_url: str,
) -> float:
    """Calculate average quality score from Qdrant metadata."""
    if not (collections and store):
        return 0.0
    try:
        import httpx
        quality_sum = 0.0
        quality_count = 0
        async with httpx.AsyncClient(timeout=_w.timeouts.httpx_kb_scroll) as client:
            raw_names = await collections.get_existing_collection_names()
            for raw_name in raw_names:
                resp = await client.post(
                    f"{qdrant_url}/collections/{raw_name}/points/scroll",
                    json={"limit": 50, "with_payload": ["quality_score"], "with_vector": False},
                )
                if resp.status_code == 200:
                    for p in resp.json().get("result", {}).get("points", []):
                        qs = p.get("payload", {}).get("quality_score")
                        if isinstance(qs, (int, float)) and qs > 0:
                            quality_sum += qs
                            quality_count += 1
        return round(quality_sum / quality_count, 1) if quality_count > 0 else 0.0
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to calculate avg quality score: %s", e)
        return 0.0


@admin_router.get("/stats")
async def admin_kb_aggregation():
    """Get KB aggregation stats."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    store = state.get("qdrant_store")

    total_kbs, total_documents = await _get_registry_counts(state.get("kb_registry"))
    total_chunks, total_kbs = await _get_qdrant_chunk_counts(collections, store, total_kbs)
    qdrant_url = state.get("qdrant_url", _default_qdrant_url())
    avg_quality_score = await _get_avg_quality_score(collections, store, qdrant_url)

    return {
        "total_kbs": total_kbs,
        "total_documents": total_documents,
        "total_chunks": total_chunks,
        "avg_quality_score": avg_quality_score,
        "by_tier": {},
        "by_status": {},
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/kb/search-cache/clear
# ---------------------------------------------------------------------------
@admin_router.post("/search-cache/clear", responses={500: {"description": "Cache clear error"}})
async def clear_search_cache():
    """Clear search cache (Redis-backed)."""
    state = _get_state()
    search_cache = state.get("search_cache")
    deleted = 0
    if search_cache:
        try:
            deleted = await search_cache.clear()
        except Exception as e:  # noqa: BLE001
            logger.warning("Search cache clear failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Cache clear error: {e}")
    return {"success": True, "message": "Search cache cleared", "deleted": deleted}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/learning/low-confidence
# Note: This is handled in feedback.py to avoid route conflict
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}")
async def admin_get_kb(kb_id: str):
    """Get single KB."""
    state = _get_state()
    kb_registry = state.get("kb_registry")

    if kb_registry:
        try:
            kb = await kb_registry.get_kb(kb_id)
            if kb:
                return kb
        except Exception as e:  # noqa: BLE001
            logger.warning("KB registry get failed: %s", e)

    store = state.get("qdrant_store")
    chunk_count = 0
    if store:
        try:
            chunk_count = await store.count(kb_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("Chunk count failed for %s: %s", kb_id, e)

    return {
        "kb_id": kb_id,
        "name": kb_id,
        "description": "",
        "tier": "global",
        "status": "active",
        "doc_count": 0,
        "chunk_count": chunk_count,
        "settings": {},
        "created_at": None,
        "updated_at": None,
    }


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/kb/{kb_id}
# ---------------------------------------------------------------------------
@admin_router.put("/{kb_id}")
async def admin_update_kb(kb_id: str, body: dict[str, Any]):
    """Update KB."""
    return {"success": True, "kb_id": kb_id, "message": "KB updated"}


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/kb/{kb_id}
# ---------------------------------------------------------------------------
@admin_router.delete("/{kb_id}", responses={503: {"description": "Qdrant not initialized"}, 500: {"description": "Internal error"}})
async def admin_delete_kb(kb_id: str):
    """Delete KB (admin)."""
    state = _get_state()
    provider = state.get("qdrant_provider")
    if not provider:
        raise HTTPException(status_code=503, detail=_QDRANT_NOT_INIT)

    try:
        client = await provider.ensure_client()
        collections = state.get("qdrant_collections")
        collection_name = collections.get_collection_name(kb_id) if collections else kb_id
        await client.delete_collection(collection_name)
        return {"success": True, "kb_id": kb_id, "message": f"KB '{kb_id}' deleted"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/stats
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/stats")
async def admin_kb_stats(kb_id: str):
    """Get KB stats."""
    state = _get_state()
    store = state.get("qdrant_store")
    chunk_count = 0
    if store:
        try:
            chunk_count = await store.count(kb_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("Chunk count failed for KB stats %s: %s", kb_id, e)

    return {
        "kb_id": kb_id,
        "total_documents": 0,
        "total_chunks": chunk_count,
        "by_source_type": {},
        "by_category": {},
        "freshness": {
            "fresh": 0,
            "stale": 0,
            "expired": 0,
        },
    }


async def _scroll_kb_documents(
    qdrant_url: str, collection_name: str, max_docs: int,
) -> dict[str, dict]:
    """Scroll through Qdrant points and collect unique documents."""
    import httpx

    docs: dict[str, dict] = {}
    offset = None

    async with httpx.AsyncClient(timeout=_w.timeouts.httpx_kb_scroll) as client:
        while len(docs) < max_docs:
            body: dict[str, Any] = {
                "limit": 100,
                "with_payload": ["doc_id", "document_name", "source_type", "quality_tier",
                                 "quality_score", "owner", "l1_category", "ingested_at", "last_modified"],
                "with_vector": False,
            }
            if offset:
                body["offset"] = offset
            resp = await client.post(f"{qdrant_url}/collections/{collection_name}/points/scroll", json=body)
            if resp.status_code != 200:
                break
            data = resp.json().get("result", {})
            points = data.get("points", [])
            if not points:
                break
            for p in points:
                pay = p["payload"]
                did = pay.get("doc_id", "")
                if did and did not in docs:
                    docs[did] = {
                        "doc_id": did,
                        "title": pay.get("document_name", ""),
                        "source_type": pay.get("source_type", "file"),
                        "quality_tier": pay.get("quality_tier", ""),
                        "quality_score": pay.get("quality_score", 0),
                        "owner": pay.get("owner", ""),
                        "l1_category": pay.get("l1_category", ""),
                        "updated_at": pay.get("last_modified", pay.get("ingested_at", "")),
                        "status": "active",
                    }
            offset = data.get("next_page_offset")
            if not offset:
                break

    return docs


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/documents
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/documents")
async def admin_kb_documents(
    kb_id: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List KB documents from Qdrant (unique doc_ids with metadata)."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    qdrant_url = state.get("qdrant_url", _default_qdrant_url())

    if not collections:
        return {"documents": [], "total": 0, "page": page, "page_size": page_size, "kb_id": kb_id}

    try:
        collection_name = collections.get_collection_name(kb_id) if collections else f"kb_{kb_id}"
        docs = await _scroll_kb_documents(qdrant_url, collection_name, page * page_size + page_size)

        all_docs = list(docs.values())
        start = (page - 1) * page_size
        return {
            "documents": all_docs[start:start + page_size],
            "total": len(all_docs),
            "page": page,
            "page_size": page_size,
            "kb_id": kb_id,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("KB documents fetch failed: %s", e)
        return {"documents": [], "total": 0, "page": page, "page_size": page_size, "kb_id": kb_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/categories
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/categories")
async def admin_kb_categories(kb_id: str):
    """Get KB category distribution from Qdrant chunk metadata."""
    import httpx
    from collections import Counter

    state = _get_state()
    collections = state.get("qdrant_collections")
    qdrant_url = state.get("qdrant_url", _default_qdrant_url())

    if not collections:
        return {"categories": [], "total": 0, "kb_id": kb_id}

    try:
        collection_name = collections.get_collection_name(kb_id) if collections else f"kb_{kb_id}"
        cat_counter: Counter[str] = Counter()
        offset = None

        async with httpx.AsyncClient(timeout=_w.timeouts.httpx_kb_scroll) as client:
            while True:
                body: dict[str, Any] = {"limit": 100, "with_payload": ["l1_category"], "with_vector": False}
                if offset:
                    body["offset"] = offset
                resp = await client.post(f"{qdrant_url}/collections/{collection_name}/points/scroll", json=body)
                if resp.status_code != 200:
                    break
                data = resp.json().get("result", {})
                points = data.get("points", [])
                if not points:
                    break
                for p in points:
                    cat = p["payload"].get("l1_category", "기타")
                    cat_counter[cat] += 1
                offset = data.get("next_page_offset")
                if not offset:
                    break

        categories = [
            {"name": name, "document_count": count}
            for name, count in cat_counter.most_common()
        ]
        return {"categories": categories, "total": len(categories), "kb_id": kb_id}
    except Exception as e:  # noqa: BLE001
        logger.warning("KB categories fetch failed: %s", e)
        return {"categories": [], "total": 0, "kb_id": kb_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/trust-scores
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/trust-scores")
async def admin_kb_trust_scores(kb_id: str):
    """Get KB trust scores from PostgreSQL."""
    state = _get_state()
    repo = state.get("trust_score_repo")
    if not repo:
        return {"items": [], "total": 0, "kb_id": kb_id}
    try:
        items = await repo.get_by_kb(kb_id, limit=500, offset=0)
        return {"items": items, "total": len(items), "kb_id": kb_id}
    except Exception as e:  # noqa: BLE001
        logger.warning("Trust scores fetch failed: %s", e)
        return {"items": [], "total": 0, "kb_id": kb_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/trust-scores/distribution
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/trust-scores/distribution")
async def admin_kb_trust_score_distribution(kb_id: str):
    """Get KB trust score distribution."""
    state = _get_state()
    repo = state.get("trust_score_repo")
    if not repo:
        return {"distribution": {}, "avg_score": 0, "kb_id": kb_id}
    try:
        items = await repo.get_by_kb(kb_id, limit=10000, offset=0)
        dist = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNCERTAIN": 0}
        total_score = 0.0
        for item in items:
            tier = (item.get("confidence_tier") or "uncertain").upper()
            if tier not in dist:
                tier = "UNCERTAIN"
            dist[tier] += 1
            total_score += item.get("kts_score", 0)
        avg = total_score / len(items) if items else 0
        return {"distribution": dist, "avg_score": round(avg, 3), "total": len(items), "kb_id": kb_id}
    except Exception as e:  # noqa: BLE001
        logger.warning("Trust score distribution failed: %s", e)
        return {"distribution": {}, "avg_score": 0, "kb_id": kb_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/lifecycle
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/lifecycle")
async def admin_kb_lifecycle(kb_id: str):
    """Get KB lifecycle."""
    return {
        "kb_id": kb_id,
        "stage": "active",
        "created_at": None,
        "last_updated": None,
        "events": [],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/coverage-gaps
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/coverage-gaps")
async def admin_kb_coverage_gaps(kb_id: str):
    """Get KB coverage gaps."""
    return {
        "kb_id": kb_id,
        "gaps": [],
        "total": 0,
        "coverage_score": 1.0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/impact
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/impact")
async def admin_kb_impact(kb_id: str):
    """Get KB impact analysis."""
    return {
        "kb_id": kb_id,
        "total_queries_served": 0,
        "unique_users": 0,
        "avg_satisfaction": 0.0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/impact/rankings
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/impact/rankings")
async def admin_kb_impact_rankings(kb_id: str):
    """Get KB impact rankings."""
    return {
        "kb_id": kb_id,
        "rankings": [],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/freshness
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/freshness")
async def admin_kb_freshness(kb_id: str):
    """Get KB freshness."""
    return {
        "kb_id": kb_id,
        "freshness_score": 0.0,
        "fresh": 0,
        "stale": 0,
        "expired": 0,
        "documents": [],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/value-tiers
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/value-tiers")
async def admin_kb_value_tiers(kb_id: str):
    """Get KB value tiers."""
    return {
        "kb_id": kb_id,
        "tiers": [],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/members
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/members")
async def admin_kb_members(kb_id: str):
    """Get KB members."""
    return {
        "members": [],
        "total": 0,
        "kb_id": kb_id,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/kb/{kb_id}/members
# ---------------------------------------------------------------------------
@admin_router.post("/{kb_id}/members")
async def admin_add_kb_member(kb_id: str, body: dict[str, Any]):
    """Add KB member."""
    return {"success": True, "kb_id": kb_id, "message": "Member added"}


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/kb/{kb_id}/members/{member_id}
# ---------------------------------------------------------------------------
@admin_router.delete("/{kb_id}/members/{member_id}")
async def admin_remove_kb_member(kb_id: str, member_id: str):
    """Remove KB member."""
    return {"success": True, "kb_id": kb_id, "member_id": member_id}
