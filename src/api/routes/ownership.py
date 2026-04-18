"""Ownership API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from src.api.app import _get_state

logger = logging.getLogger(__name__)

# Two routers: one for /admin/ownership, one for /knowledge/experts
admin_router = APIRouter(prefix="/api/v1/admin/ownership", tags=["Ownership"])
knowledge_router = APIRouter(prefix="/api/v1/knowledge/experts", tags=["Ownership"])


# ---------------------------------------------------------------------------
# GET /api/v1/admin/ownership/documents
# ---------------------------------------------------------------------------
@admin_router.get("/documents")
async def list_document_owners(
    kb_id: Annotated[str, Query()],
    status: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """List document owners."""
    state = _get_state()
    repo = state.get("doc_owner_repo")
    if repo:
        try:
            owners = await repo.get_by_kb(kb_id)
            return {"owners": owners, "total": len(owners), "kb_id": kb_id}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Doc owner repo query failed: %s", e)
    return {"owners": [], "total": 0, "kb_id": kb_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/ownership/documents/{document_id}
# ---------------------------------------------------------------------------
@admin_router.get("/documents/{document_id}")
async def get_document_owner(
    document_id: str,
    kb_id: Annotated[str, Query()],
) -> dict[str, Any]:
    """Get document owner."""
    state = _get_state()
    repo = state.get("doc_owner_repo")
    if repo:
        try:
            owner = await repo.get_by_document(document_id, kb_id)
            if owner:
                return owner
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Doc owner repo get failed: %s", e)
    return {
        "document_id": document_id,
        "kb_id": kb_id,
        "owner": None,
        "status": "unassigned",
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/ownership/documents
# ---------------------------------------------------------------------------
@admin_router.post("/documents", responses={500: {"description": "Failed to assign owner"}})
async def assign_document_owner(body: dict[str, Any]) -> dict[str, Any]:
    """Assign document owner."""
    state = _get_state()
    repo = state.get("doc_owner_repo")
    if repo:
        try:
            await repo.save(body)
            return {"success": True, "message": "Owner assigned"}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Doc owner repo save failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to assign owner: {e}")
    return {"success": True, "message": "Owner assigned (stub - no DB)"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/ownership/documents/{document_id}/transfer
# ---------------------------------------------------------------------------
@admin_router.post("/documents/{document_id}/transfer", responses={404: {"description": "Document owner not found"}, 500: {"description": "Failed to transfer ownership"}})  # noqa: E501
async def transfer_ownership(document_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Transfer document ownership."""
    state = _get_state()
    repo = state.get("doc_owner_repo")
    if repo:
        try:
            kb_id = body.get("kb_id", "")
            existing = await repo.get_by_document(document_id, kb_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Document owner not found")
            transfer_data = {
                "document_id": document_id,
                "kb_id": kb_id,
                "owner_user_id": body.get("new_owner_user_id", existing["owner_user_id"]),
                "backup_owner_user_id": body.get("backup_owner_user_id", existing.get("backup_owner_user_id")),
                "ownership_type": existing.get("ownership_type", "assigned"),
            }
            await repo.save(transfer_data)
            return {"success": True, "document_id": document_id, "message": "Ownership transferred"}
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Doc owner repo transfer failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to transfer ownership: {e}")
    return {"success": True, "document_id": document_id, "message": "Ownership transferred (stub - no DB)"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/ownership/documents/{document_id}/verify
# ---------------------------------------------------------------------------
@admin_router.post("/documents/{document_id}/verify", responses={404: {"description": "Document owner not found"}, 500: {"description": "Failed to verify owner"}})  # noqa: E501
async def verify_document_owner(document_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Verify document owner."""
    state = _get_state()
    repo = state.get("doc_owner_repo")
    if repo:
        try:
            kb_id = body.get("kb_id", "")
            existing = await repo.get_by_document(document_id, kb_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Document owner not found")
            verify_data = dict(existing)
            verify_data["ownership_type"] = "verified"
            await repo.save(verify_data)
            return {"success": True, "document_id": document_id, "message": "Verified"}
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Doc owner repo verify failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to verify owner: {e}")
    return {"success": True, "document_id": document_id, "message": "Verified (stub - no DB)"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/ownership/stale
# ---------------------------------------------------------------------------
def _find_stale_owners(all_owners: list[dict], cutoff) -> list[dict]:
    """Filter owners whose updated_at is before cutoff."""
    from datetime import datetime

    stale = []
    for o in all_owners:
        updated_str = o.get("updated_at")
        if not updated_str:
            continue
        if isinstance(updated_str, str):
            updated = datetime.fromisoformat(updated_str)
        else:
            updated = updated_str
        # Normalize timezone awareness for comparison
        if updated.tzinfo is None:
            cutoff_cmp = cutoff.replace(tzinfo=None)
        else:
            cutoff_cmp = cutoff
        if updated < cutoff_cmp:
            stale.append(o)
    return stale


@admin_router.get("/stale")
async def get_stale_owners(
    kb_id: Annotated[str, Query()],
    days_threshold: Annotated[int, Query(ge=1)] = 90,
) -> dict[str, Any]:
    """Get stale owners."""
    state = _get_state()
    repo = state.get("doc_owner_repo")
    if repo:
        try:
            from datetime import UTC, datetime, timedelta
            all_owners = await repo.get_by_kb(kb_id)
            cutoff = datetime.now(UTC) - timedelta(days=days_threshold)
            stale = _find_stale_owners(all_owners, cutoff)
            return {"stale_owners": stale, "total": len(stale), "kb_id": kb_id, "days_threshold": days_threshold}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Doc owner repo stale query failed: %s", e)
    return {"stale_owners": [], "total": 0, "kb_id": kb_id, "days_threshold": days_threshold}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/ownership/availability/{owner_user_id}
# ---------------------------------------------------------------------------
@admin_router.get("/availability/{owner_user_id}")
async def get_owner_availability(owner_user_id: str) -> dict[str, Any]:
    """Get owner availability."""
    state = _get_state()
    repo = state.get("doc_owner_repo")
    if repo:
        try:
            docs = await repo.get_by_owner(owner_user_id)
            return {
                "user_id": owner_user_id,
                "available": True,
                "status": "active",
                "owned_documents": len(docs),
            }
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Doc owner repo availability check failed: %s", e)
    return {"user_id": owner_user_id, "available": True, "status": "active", "owned_documents": 0}


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/ownership/availability/{owner_user_id}
# ---------------------------------------------------------------------------
@admin_router.put("/availability/{owner_user_id}")
async def update_owner_availability(owner_user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Update owner availability."""
    # Availability is a transient property; with doc_owner_repo we can update owner metadata
    state = _get_state()
    repo = state.get("doc_owner_repo")
    if repo:
        try:
            # This is an informational endpoint - owner availability is tracked externally
            return {"success": True, "user_id": owner_user_id}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Owner availability update failed: %s", e)
    return {"success": True, "user_id": owner_user_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/ownership/topics
# ---------------------------------------------------------------------------
@admin_router.get("/topics")
async def list_topic_owners(
    kb_id: Annotated[str, Query()],
) -> dict[str, Any]:
    """List topic owners."""
    state = _get_state()
    repo = state.get("topic_owner_repo")
    if repo:
        try:
            topics = await repo.get_by_kb(kb_id)
            return {"topics": topics, "total": len(topics), "kb_id": kb_id}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Topic owner repo query failed: %s", e)
    return {"topics": [], "total": 0, "kb_id": kb_id}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/ownership/topics
# ---------------------------------------------------------------------------
@admin_router.post("/topics", responses={500: {"description": "Failed to assign topic owner"}})
async def assign_topic_owner(body: dict[str, Any]) -> dict[str, Any]:
    """Assign topic owner."""
    state = _get_state()
    repo = state.get("topic_owner_repo")
    if repo:
        try:
            await repo.save(body)
            return {"success": True, "message": "Topic owner assigned"}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Topic owner repo save failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to assign topic owner: {e}")
    return {"success": True, "message": "Topic owner assigned (stub - no DB)"}


# ---------------------------------------------------------------------------
# GET /api/v1/knowledge/experts/search
# ---------------------------------------------------------------------------
@knowledge_router.get("/search")
async def search_experts(
    query: Annotated[str, Query(max_length=200)],
    kb_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Search for experts by matching topic keywords."""
    state = _get_state()
    topic_repo = state.get("topic_owner_repo")
    if topic_repo and kb_id:
        try:
            topics = await topic_repo.get_by_kb(kb_id)
            query_lower = query.lower()
            matched_experts: list[dict[str, Any]] = []
            seen_users: set[str] = set()
            for t in topics:
                keywords = [k.lower() for k in t.get("topic_keywords", [])]
                topic_name_lower = t.get("topic_name", "").lower()
                if query_lower in topic_name_lower or any(query_lower in k for k in keywords):
                    user_id = t.get("sme_user_id")
                    if user_id and user_id not in seen_users:
                        seen_users.add(user_id)
                        matched_experts.append({
                            "user_id": user_id,
                            "topic_name": t["topic_name"],
                            "kb_id": t["kb_id"],
                        })
            return {"experts": matched_experts, "total": len(matched_experts), "query": query}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Expert search failed: %s", e)
    return {"experts": [], "total": 0, "query": query}
