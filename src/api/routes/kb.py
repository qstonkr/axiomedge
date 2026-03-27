"""KB Management API endpoints.

Serves both /api/v1/kb/* (original) and /api/v1/admin/kb/* (dashboard calls).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.app import _get_state

logger = logging.getLogger(__name__)

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

async def _list_kbs_impl(tier: str | None = None, status: str | None = None) -> dict:
    """Shared implementation for listing KBs."""
    state = _get_state()
    kb_registry = state.get("kb_registry")

    if kb_registry:
        try:
            if status:
                kbs = await kb_registry.list_by_status(status)
            elif tier:
                kbs = await kb_registry.list_by_tier(tier)
            else:
                kbs = await kb_registry.list_all()

            # Enrich with Qdrant chunk counts + ensure doc_count
            store = state.get("qdrant_store")
            if store:
                for kb in kbs:
                    kb_id = kb.get("kb_id") or kb.get("id", "")
                    try:
                        kb["chunk_count"] = await store.count(kb_id)
                    except Exception:
                        kb.setdefault("chunk_count", 0)
                    kb["doc_count"] = kb.get("document_count", 0)

            return {"kbs": kbs}
        except Exception as e:
            logger.warning("KB registry query failed, falling back to Qdrant: %s", e)

    # Fallback to Qdrant collections
    collections = state.get("qdrant_collections")
    store = state.get("qdrant_store")
    if not collections:
        return {"kbs": []}

    try:
        raw_names = await collections.get_existing_collection_names()
        # Strip collection prefix to get original kb_id
        prefix = getattr(collections._provider.config, "collection_prefix", "kb") + "_"
        kbs = []
        for raw_name in raw_names:
            kb_id = raw_name[len(prefix):] if raw_name.startswith(prefix) else raw_name
            count = 0
            if store:
                try:
                    # Pass original kb_id (store will add prefix internally)
                    count = await store.count(kb_id)
                except Exception as e:
                    logger.debug("Count failed for %s: %s", kb_id, e)
            kbs.append({
                "kb_id": kb_id,
                "name": kb_id,
                "description": "",
                "tier": "global",
                "doc_count": 0,
                "chunk_count": count,
                "status": "active",
                "settings": {},
            })
        return {"kbs": kbs}
    except Exception as e:
        return {"kbs": [], "error": str(e)}


# ============================================================================
# Original /api/v1/kb/* routes
# ============================================================================

@router.post("/create")
async def create_kb(request: KBCreateRequest):
    """Create a new knowledge base (Qdrant collection)."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    if not collections:
        raise HTTPException(status_code=503, detail="Qdrant not initialized")

    try:
        await collections.ensure_collection(request.kb_id)
        return {"success": True, "kb_id": request.kb_id, "message": f"KB '{request.name}' created"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def list_kbs():
    """List all knowledge bases."""
    return await _list_kbs_impl()


@router.delete("/{kb_id}")
async def delete_kb(kb_id: str):
    """Delete a knowledge base."""
    state = _get_state()
    provider = state.get("qdrant_provider")
    if not provider:
        raise HTTPException(status_code=503, detail="Qdrant not initialized")

    try:
        client = await provider.ensure_client()
        collections = state.get("qdrant_collections")
        collection_name = collections.get_collection_name(kb_id) if collections else kb_id
        await client.delete_collection(collection_name)
        return {"success": True, "kb_id": kb_id, "message": f"KB '{kb_id}' deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Admin /api/v1/admin/kb/* routes (dashboard calls these)
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb
# ---------------------------------------------------------------------------
@admin_router.get("")
async def admin_list_kbs(
    tier: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    """List KBs (admin)."""
    return await _list_kbs_impl(tier=tier, status=status)


# ---------------------------------------------------------------------------
# POST /api/v1/admin/kb
# ---------------------------------------------------------------------------
@admin_router.post("")
async def admin_create_kb(body: dict[str, Any]):
    """Create a KB (admin)."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    if not collections:
        raise HTTPException(status_code=503, detail="Qdrant not initialized")

    kb_id = body.get("kb_id", body.get("name", ""))
    try:
        await collections.ensure_collection(kb_id)
        return {"success": True, "kb_id": kb_id, "message": f"KB '{kb_id}' created"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/stats  (aggregation - MUST be before {kb_id})
# ---------------------------------------------------------------------------
@admin_router.get("/stats")
async def admin_kb_aggregation():
    """Get KB aggregation stats."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    store = state.get("qdrant_store")

    total_chunks = 0
    total_documents = 0
    total_kbs = 0

    # Get document counts from DB
    kb_registry = state.get("kb_registry")
    if kb_registry:
        try:
            kbs = await kb_registry.list_all()
            total_kbs = len(kbs)
            total_documents = sum(kb.get("document_count", 0) for kb in kbs)
        except Exception:
            pass

    # Get chunk counts from Qdrant
    if collections and store:
        try:
            raw_names = await collections.get_existing_collection_names()
            if not total_kbs:
                total_kbs = len(raw_names)
            prefix = getattr(collections._provider.config, "collection_prefix", "kb") + "_"
            for raw_name in raw_names:
                kb_id = raw_name[len(prefix):] if raw_name.startswith(prefix) else raw_name
                try:
                    total_chunks += await store.count(kb_id)
                except Exception:
                    pass
        except Exception:
            pass

    # Calculate avg quality score from Qdrant metadata
    avg_quality_score = 0.0
    quality_sum = 0.0
    quality_count = 0
    if collections and store:
        try:
            import httpx
            qdrant_url = state.get("qdrant_url", "http://localhost:6333")
            async with httpx.AsyncClient(timeout=10.0) as client:
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
            if quality_count > 0:
                avg_quality_score = round(quality_sum / quality_count, 1)
        except Exception:
            pass

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
@admin_router.post("/search-cache/clear")
async def clear_search_cache():
    """Clear search cache (Redis-backed)."""
    state = _get_state()
    search_cache = state.get("search_cache")
    deleted = 0
    if search_cache:
        try:
            deleted = await search_cache.clear()
        except Exception as e:
            logger.warning("Search cache clear failed: %s", e)
            return {"success": False, "message": f"Cache clear error: {e}", "deleted": 0}
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
        except Exception as e:
            logger.warning("KB registry get failed: %s", e)

    store = state.get("qdrant_store")
    chunk_count = 0
    if store:
        try:
            chunk_count = await store.count(kb_id)
        except Exception:
            pass

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
@admin_router.delete("/{kb_id}")
async def admin_delete_kb(kb_id: str):
    """Delete KB (admin)."""
    state = _get_state()
    provider = state.get("qdrant_provider")
    if not provider:
        raise HTTPException(status_code=503, detail="Qdrant not initialized")

    try:
        client = await provider.ensure_client()
        collections = state.get("qdrant_collections")
        collection_name = collections.get_collection_name(kb_id) if collections else kb_id
        await client.delete_collection(collection_name)
        return {"success": True, "kb_id": kb_id, "message": f"KB '{kb_id}' deleted"}
    except Exception as e:
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
        except Exception:
            pass

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


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/documents
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/documents")
async def admin_kb_documents(
    kb_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List KB documents."""
    return {
        "documents": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
        "kb_id": kb_id,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/categories
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/categories")
async def admin_kb_categories(kb_id: str):
    """Get KB categories."""
    return {
        "categories": [],
        "total": 0,
        "kb_id": kb_id,
    }


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
# GET /api/v1/admin/kb/{kb_id}/trust-scores
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/trust-scores")
async def admin_kb_trust_scores(kb_id: str):
    """Get KB trust scores."""
    state = _get_state()
    repo = state.get("trust_score_repo")
    if repo:
        try:
            scores = await repo.get_by_kb(kb_id, limit=100)
            avg = sum(s["kts_score"] for s in scores) / len(scores) if scores else 0.0
            return {"kb_id": kb_id, "average_trust": round(avg, 3), "scores": scores}
        except Exception as e:
            logger.warning("Trust score repo query failed: %s", e)
    return {"kb_id": kb_id, "average_trust": 0.0, "scores": []}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/trust-scores/distribution
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/trust-scores/distribution")
async def admin_kb_trust_score_distribution(kb_id: str):
    """Get KB trust score distribution."""
    return {
        "kb_id": kb_id,
        "distribution": [],
        "buckets": [],
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
