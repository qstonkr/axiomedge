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
    """Search for experts by matching topic keywords + document ownership.

    Two data sources, deduplicated by ``user_id``:
      1. ``topic_owners`` — admin-curated SME mapping (often empty in MVP)
      2. ``document_owners`` — auto-extracted from ingested docs (실데이터 풍부)

    kb_id 없이 호출되면 모든 KB 의 owner 데이터를 합쳐서 검색. UI 가
    "전체" 필터일 때도 결과가 나오도록 — 빈 결과로 silent fail 하지 않게.
    """
    state = _get_state()
    query_lower = query.lower()
    matched_experts: list[dict[str, Any]] = []
    seen_users: set[str] = set()
    errors: list[str] = []

    # ── 1. topic_owner 매칭 (admin-curated) ──
    topic_repo = state.get("topic_owner_repo")
    if topic_repo and kb_id:
        try:
            topics = await topic_repo.get_by_kb(kb_id)
            for t in topics:
                keywords = [k.lower() for k in t.get("topic_keywords", [])]
                topic_name_lower = t.get("topic_name", "").lower()
                if query_lower in topic_name_lower or any(query_lower in k for k in keywords):
                    user_id = t.get("sme_user_id")
                    if user_id and user_id not in seen_users:
                        seen_users.add(user_id)
                        matched_experts.append({
                            "id": user_id,
                            "user_id": user_id,
                            "name": user_id,
                            "topic_name": t.get("topic_name"),
                            "kb_id": t.get("kb_id"),
                            "source": "topic_owner",
                        })
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("topic_owner expert search failed: %s", e)
            errors.append(f"topic_owner: {type(e).__name__}: {e}")

    # ── 2. document_owner 매칭 (auto-extracted, MVP 의 주 데이터원) ──
    # owner_user_id 가 한국어 이름인 경우가 많아 부분 일치로 매칭. document
    # 수까지 묶어서 카드에 노출.
    doc_repo = state.get("doc_owner_repo")
    if doc_repo:
        try:
            if kb_id:
                doc_owners = await doc_repo.get_by_kb(kb_id, limit=2000)
            else:
                # KB 별로 모은다 — 전체 KB 순회. KB 리스트는 kb_registry 에서.
                kb_registry = state.get("kb_registry")
                doc_owners = []
                if kb_registry:
                    all_kbs = await kb_registry.list_all()
                    for k in all_kbs:
                        if k.get("status") != "active":
                            continue
                        kid = k.get("id") or k.get("kb_id")
                        if not kid:
                            continue
                        try:
                            doc_owners.extend(await doc_repo.get_by_kb(kid, limit=500))
                        except Exception as e:  # noqa: BLE001 — 개별 KB 실패는 부분 결과로
                            errors.append(f"doc_owner kb={kid}: {type(e).__name__}: {e}")
            # owner_user_id 별로 묶어서 doc 수 표시
            by_user: dict[str, dict[str, Any]] = {}
            for o in doc_owners:
                user_id = o.get("owner_user_id") or ""
                if not user_id:
                    continue
                if query_lower not in user_id.lower():
                    continue
                if user_id in seen_users:
                    continue
                entry = by_user.setdefault(user_id, {
                    "id": user_id,
                    "user_id": user_id,
                    "name": user_id,
                    "kb_id": o.get("kb_id"),
                    "source": "document_owner",
                    "documents": [],
                })
                entry["documents"].append({
                    "document_id": o.get("document_id"),
                    "kb_id": o.get("kb_id"),
                    "ownership_type": o.get("ownership_type"),
                })
            for user_id, entry in by_user.items():
                seen_users.add(user_id)
                matched_experts.append(entry)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("document_owner expert search failed: %s", e)
            errors.append(f"document_owner: {type(e).__name__}: {e}")

    response: dict[str, Any] = {
        "experts": matched_experts,
        "total": len(matched_experts),
        "query": query,
    }
    if errors:
        # 부분 실패도 디버깅 가능하도록 노출 (chat 의 failure_reason 패턴과 동일)
        response["partial_errors"] = errors
    return response
