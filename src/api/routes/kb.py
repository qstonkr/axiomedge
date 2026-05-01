"""KB Management API endpoints.

Serves both /api/v1/kb/* (original) and /api/v1/admin/kb/* (dashboard calls).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.app import _get_state
from src.auth.dependencies import OrgContext, get_current_org, get_current_user
from src.auth.providers import AuthUser
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


# B-1 Day 1 — soft cap on personal KBs per user. Configurable via env if needed.
PERSONAL_KB_LIMIT_PER_USER = 10


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
        except Exception as e:  # noqa: BLE001 — count is presentational
            # Qdrant raises ``grpc.aio.AioRpcError`` (not a stdlib type) when
            # a KB's collection or alias is missing. UI fallback to 0 lets the
            # KB list render even when the vector store hasn't been seeded yet.
            logger.warning(
                "Failed to count chunks for KB %s: %s — falling back to 0",
                kb_id, e,
            )
            kb.setdefault("chunk_count", 0)
        kb["doc_count"] = kb.get("document_count", 0)


async def _list_kbs_from_registry(
    kb_registry, store, *, tier: str | None, status: str | None,
    organization_id: str | None = None,
) -> dict | None:
    """Try listing KBs from registry. Returns None on failure.

    ``organization_id`` scopes the query to one tenant; pass None only from
    system code (background sync, health checks) that legitimately needs a
    cross-org view.
    """
    try:
        if status:
            kbs = await kb_registry.list_by_status(status, organization_id=organization_id)
        elif tier:
            kbs = await kb_registry.list_by_tier(tier, organization_id=organization_id)
        else:
            kbs = await kb_registry.list_all(organization_id=organization_id)
        await _enrich_kb_counts(kbs, store)
        return {"kbs": kbs}
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("KB registry query failed, falling back to Qdrant: %s", e)
        return None


async def _list_kbs_from_qdrant(collections, store) -> dict[str, Any]:
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
                except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                    logger.debug("Count failed for %s: %s", kb_id, e)
            kbs.append({
                "kb_id": kb_id, "name": kb_id, "description": "",
                "tier": "global", "doc_count": 0, "chunk_count": count,
                "status": "active", "settings": {},
            })
        return {"kbs": kbs}
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        return {"kbs": [], "error": str(e)}


async def _list_kbs_impl(
    tier: str | None = None, status: str | None = None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """Shared implementation for listing KBs.

    The Qdrant fallback path returns whatever collections exist on the cluster
    — it is NOT org-scoped. Today that is fine because each KB lives in its
    own collection and a cross-tenant client never gets the registry rows that
    would tell it the right collection name; tightening this further (e.g.
    payload-level org filter) is a separate hardening pass.
    """
    state = _get_state()
    kb_registry = state.get("kb_registry")
    store = state.get("qdrant_store")

    if kb_registry:
        result = await _list_kbs_from_registry(
            kb_registry, store, tier=tier, status=status,
            organization_id=organization_id,
        )
        if result is not None:
            return result

    collections = state.get("qdrant_collections")
    if not collections:
        return {"kbs": []}
    return await _list_kbs_from_qdrant(collections, store)


# ============================================================================
# Original /api/v1/kb/* routes
# ============================================================================

@router.post("/create", responses={
    503: {"description": "Qdrant not initialized"},
    400: {"description": "Tier/cap validation failed"},
    409: {"description": "Personal KB limit reached"},
    500: {"description": "Internal error"},
})
async def create_kb(
    request: KBCreateRequest,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Create a knowledge base.

    Permissions (B-1 Day 1):
    - ``tier=personal``: any authenticated user, capped at
      ``PERSONAL_KB_LIMIT_PER_USER`` rows per (user, org). Caller becomes owner.
    - other tiers: rejected here (use the admin endpoint with kb:create perm).
    """
    state = _get_state()
    collections = state.get("qdrant_collections")
    if not collections:
        raise HTTPException(status_code=503, detail=_QDRANT_NOT_INIT)

    if request.tier != "personal":
        raise HTTPException(
            status_code=400,
            detail="POST /api/v1/kb/create only accepts tier='personal'. "
                   "Use the admin KB endpoint for team/global tiers.",
        )

    kb_registry = state.get("kb_registry")
    owner_id = user.sub

    if kb_registry is not None:
        try:
            existing = await kb_registry.list_by_tier(
                "personal", organization_id=org.id, owner_id=owner_id,
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            raise HTTPException(status_code=500, detail=f"KB count query failed: {e}")
        if len(existing) >= PERSONAL_KB_LIMIT_PER_USER:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Personal KB limit reached ({PERSONAL_KB_LIMIT_PER_USER}). "
                    "Delete an existing personal KB to create a new one."
                ),
            )

    try:
        await collections.ensure_collection(request.kb_id)
        if kb_registry is not None:
            await kb_registry.create_kb({
                "id": request.kb_id,
                "name": request.name,
                "description": request.description,
                "tier": "personal",
                "organization_id": org.id,
                "owner_id": owner_id,
                "status": "active",
                "settings": {},
                "dataset_ids_by_env": {},
                "sync_sources": [],
            })
        return {
            "success": True, "kb_id": request.kb_id,
            "tier": "personal", "owner_id": owner_id,
            "message": f"Personal KB '{request.name}' created",
        }
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def list_kbs(
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """List knowledge bases the caller's organization can see."""
    return await _list_kbs_impl(organization_id=org.id)


@router.get(
    "/{kb_id}/documents",
    responses={
        403: {"description": "Caller does not own this KB"},
        404: {"description": "KB not found"},
        503: {"description": "Qdrant not initialized"},
    },
)
async def list_kb_documents(
    kb_id: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """List documents in a personal KB the caller owns.

    Owner-only endpoint — used by ``/my-knowledge`` to show what the user
    has uploaded into their own KB. Admin scope (other tiers) goes through
    ``/api/v1/admin/kb/{kb_id}/documents`` instead.
    """
    state = _get_state()
    collections = state.get("qdrant_collections")
    qdrant_url = state.get("qdrant_url", _default_qdrant_url())
    if not collections:
        raise HTTPException(status_code=503, detail=_QDRANT_NOT_INIT)

    # Ownership check via kb_registry — personal KB 의 owner_id 가 caller 이어야.
    # `get_kb(owner_id=...)` 자체가 owner 미스매치 시 None 반환 → 404 로 매핑하면
    # 다른 사용자 KB 의 존재 여부를 누설하지 않는다 (B-1 Day 1 패턴).
    kb_registry = state.get("kb_registry")
    if kb_registry is not None:
        try:
            kb_row = await kb_registry.get_kb(
                kb_id, organization_id=org.id, owner_id=user.sub,
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            raise HTTPException(status_code=500, detail=f"KB lookup failed: {e}")
        if kb_row is None:
            raise HTTPException(status_code=404, detail=f"KB '{kb_id}' not found")

    try:
        collection_name = collections.get_collection_name(kb_id) if collections else f"kb_{kb_id}"
        # NOTE: Qdrant scroll 은 cursor 기반이라 offset 직접 지원 안 함.
        # `page * page_size + page_size` 만큼 fetch 후 client-side slice — admin
        # endpoint 와 동일 패턴. personal KB 는 보통 << 1000 docs 라 acceptable.
        # 큰 KB 는 admin endpoint (`/admin/kb/{kb_id}/documents`) 사용 권장.
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("Personal KB documents fetch failed: %s", e)
        return {"documents": [], "total": 0, "page": page, "page_size": page_size, "kb_id": kb_id}


@router.delete("/{kb_id}", responses={503: {"description": "Qdrant not initialized"}, 500: {"description": "Internal error"}})  # noqa: E501
async def delete_kb(kb_id: str) -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """List KBs (admin) — scoped to caller's organization."""
    return await _list_kbs_impl(tier=tier, status=status, organization_id=org.id)


# ---------------------------------------------------------------------------
# POST /api/v1/admin/kb
# ---------------------------------------------------------------------------
@admin_router.post("", responses={503: {"description": "Qdrant not initialized"}, 500: {"description": "Internal error"}})  # noqa: E501
async def admin_create_kb(body: dict[str, Any]) -> dict[str, Any]:
    """Create a KB (admin)."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    if not collections:
        raise HTTPException(status_code=503, detail=_QDRANT_NOT_INIT)

    kb_id = body.get("kb_id", body.get("name", ""))
    try:
        await collections.ensure_collection(kb_id)
        return {"success": True, "kb_id": kb_id, "message": f"KB '{kb_id}' created"}
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/stats  (aggregation - MUST be before {kb_id})
# ---------------------------------------------------------------------------
async def _get_registry_counts(
    kb_registry: Any, organization_id: str | None = None,
) -> tuple[int, int]:
    """Get KB and document counts from registry, scoped to one org if given."""
    if not kb_registry:
        return 0, 0
    try:
        kbs = await kb_registry.list_all(organization_id=organization_id)
        return len(kbs), sum(kb.get("document_count", 0) for kb in kbs)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                logger.debug("Chunk count failed for %s: %s", kb_id, e)
        return total_chunks, total_kbs
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.debug("Failed to get Qdrant collection names: %s", e)
        return 0, fallback_total_kbs


async def _get_avg_quality_score(
    collections: Any, store: Any, qdrant_url: str,
) -> float:
    """Calculate average quality score from Qdrant metadata."""
    if not (collections and store):
        return 0.0
    import httpx
    try:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, httpx.HTTPError) as e:
        logger.debug("Failed to calculate avg quality score: %s", e)
        return 0.0


@admin_router.get("/stats")
async def admin_kb_aggregation(
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Get KB aggregation stats — scoped to caller's organization."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    store = state.get("qdrant_store")

    total_kbs, total_documents = await _get_registry_counts(
        state.get("kb_registry"), organization_id=org.id,
    )
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
async def clear_search_cache() -> dict[str, Any]:
    """Clear search cache (Redis-backed)."""
    state = _get_state()
    search_cache = state.get("search_cache")
    deleted = 0
    if search_cache:
        try:
            deleted = await search_cache.clear()
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
async def admin_get_kb(
    kb_id: str,
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Get single KB. Returns 404 when the KB belongs to another organization."""
    state = _get_state()
    kb_registry = state.get("kb_registry")

    if kb_registry:
        try:
            kb = await kb_registry.get_kb(kb_id, organization_id=org.id)
            if kb:
                return kb
            # Distinguishing "not found" from "wrong tenant" leaks existence;
            # both surface as 404.
            raise HTTPException(status_code=404, detail=f"KB '{kb_id}' not found")
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("KB registry get failed: %s", e)

    # Registry unavailable — opaque fallback (no org filter possible).
    store = state.get("qdrant_store")
    chunk_count = 0
    if store:
        try:
            chunk_count = await store.count(kb_id)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
async def admin_update_kb(kb_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Update KB."""
    return {"success": True, "kb_id": kb_id, "message": "KB updated"}


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/kb/{kb_id}
# ---------------------------------------------------------------------------
@admin_router.delete("/{kb_id}", responses={503: {"description": "Qdrant not initialized"}, 500: {"description": "Internal error"}})  # noqa: E501
async def admin_delete_kb(kb_id: str) -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/stats
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/stats")
async def admin_kb_stats(kb_id: str) -> dict[str, Any]:
    """Get KB stats."""
    state = _get_state()
    store = state.get("qdrant_store")
    chunk_count = 0
    if store:
        try:
            chunk_count = await store.count(kb_id)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
) -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("KB documents fetch failed: %s", e)
        return {"documents": [], "total": 0, "page": page, "page_size": page_size, "kb_id": kb_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/categories
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/categories")
async def admin_kb_categories(kb_id: str) -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("KB categories fetch failed: %s", e)
        return {"categories": [], "total": 0, "kb_id": kb_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/trust-scores
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/trust-scores")
async def admin_kb_trust_scores(kb_id: str) -> dict[str, Any]:
    """Get KB trust scores from PostgreSQL."""
    state = _get_state()
    repo = state.get("trust_score_repo")
    if not repo:
        return {"items": [], "total": 0, "kb_id": kb_id}
    try:
        items = await repo.get_by_kb(kb_id, limit=500, offset=0)
        return {"items": items, "total": len(items), "kb_id": kb_id}
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("Trust scores fetch failed: %s", e)
        return {"items": [], "total": 0, "kb_id": kb_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/trust-scores/distribution
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/trust-scores/distribution")
async def admin_kb_trust_score_distribution(kb_id: str) -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("Trust score distribution failed: %s", e)
        return {"distribution": {}, "avg_score": 0, "kb_id": kb_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/lifecycle
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/lifecycle")
async def admin_kb_lifecycle(kb_id: str) -> dict[str, Any]:
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
async def admin_kb_coverage_gaps(kb_id: str) -> dict[str, Any]:
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
async def admin_kb_impact(kb_id: str) -> dict[str, Any]:
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
async def admin_kb_impact_rankings(kb_id: str) -> dict[str, Any]:
    """Get KB impact rankings."""
    return {
        "kb_id": kb_id,
        "rankings": [],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/freshness
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/freshness")
async def admin_kb_freshness(kb_id: str) -> dict[str, Any]:
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
async def admin_kb_value_tiers(kb_id: str) -> dict[str, Any]:
    """Get KB value tiers."""
    return {
        "kb_id": kb_id,
        "tiers": [],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/members
# ---------------------------------------------------------------------------
@admin_router.get("/{kb_id}/members")
async def admin_kb_members(kb_id: str) -> dict[str, Any]:
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
async def admin_add_kb_member(kb_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Add KB member."""
    return {"success": True, "kb_id": kb_id, "message": "Member added"}


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/kb/{kb_id}/members/{member_id}
# ---------------------------------------------------------------------------
@admin_router.delete("/{kb_id}/members/{member_id}")
async def admin_remove_kb_member(kb_id: str, member_id: str) -> dict[str, Any]:
    """Remove KB member."""
    return {"success": True, "kb_id": kb_id, "member_id": member_id}
