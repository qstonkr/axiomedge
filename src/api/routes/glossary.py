"""Glossary API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from src.api.app import _get_state
from src.config_weights import weights as _w

# Import helpers and re-export for backward compatibility
from src.api.routes.glossary_helpers import (  # noqa: F401 — re-exports
    _TERM_NOT_FOUND,
    _approve_single_synonym,
    _check_not_global_standard,
    compute_similarity_distribution,
)

logger = logging.getLogger(__name__)

_NO_DB = HTTPException(status_code=503, detail="No DB connection")

_EXACT_MATCH_THRESHOLD = _w.similarity.exact_match_threshold
router = APIRouter(prefix="/api/v1/admin/glossary", tags=["Glossary"])


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary
# ---------------------------------------------------------------------------
@router.get("")
async def list_glossary_terms(
    kb_id: Annotated[str, Query()] = "all",
    status: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
    term_type: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """List glossary terms."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            offset = (page - 1) * page_size
            terms = await repo.list_by_kb(
                kb_id=kb_id, status=status, scope=scope,
                term_type=term_type, limit=page_size, offset=offset,
            )
            total = await repo.count_by_kb(kb_id=kb_id, status=status, scope=scope, term_type=term_type)
            return {"terms": terms, "total": total, "page": page, "page_size": page_size}
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary repo query failed: %s", e)
    return {"terms": [], "total": 0, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/domain-stats
# (MUST be before /{term_id} to avoid path capture)
# ---------------------------------------------------------------------------
@router.get("/domain-stats")
async def get_domain_stats():
    """Get term count by domain_name (전체 데이터 DB 집계)."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if not repo:
        return {"domains": {}}
    try:
        from sqlalchemy import func, select
        from src.database.models import GlossaryTermModel
        async with await repo._get_session() as session:
            stmt = (
                select(GlossaryTermModel.domain_name, func.count())
                .where(GlossaryTermModel.domain_name.isnot(None))
                .where(GlossaryTermModel.domain_name != "")
                .group_by(GlossaryTermModel.domain_name)
                .order_by(func.count().desc())
            )
            result = await session.execute(stmt)
            domains = {row[0]: row[1] for row in result.all()}
            return {"domains": domains, "total_domains": len(domains)}
    except Exception as e:  # noqa: BLE001
        logger.warning("Domain stats failed: %s", e)
        return {"domains": {}, "error": str(e)}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/source-stats
# ---------------------------------------------------------------------------
@router.get("/source-stats")
async def get_source_stats():
    """Get term count by kb_id/source (표준분류별, 전체 DB 집계)."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if not repo:
        return {"sources": {}}
    try:
        from sqlalchemy import func, select
        from src.database.models import GlossaryTermModel
        async with await repo._get_session() as session:
            stmt = (
                select(GlossaryTermModel.kb_id, func.count())
                .group_by(GlossaryTermModel.kb_id)
                .order_by(func.count().desc())
            )
            result = await session.execute(stmt)
            sources = {row[0]: row[1] for row in result.all()}
            return {"sources": sources, "total_sources": len(sources)}
    except Exception as e:  # noqa: BLE001
        logger.warning("Source stats failed: %s", e)
        return {"sources": {}, "error": str(e)}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/similarity-distribution
# (MUST be before /{term_id} to avoid path capture)
# ---------------------------------------------------------------------------
@router.get("/similarity-distribution")
async def get_similarity_distribution():
    """Get similarity score distribution with RapidFuzz sampling (500 samples, top-K matching)."""
    state = _get_state()
    try:
        return await compute_similarity_distribution(state, _EXACT_MATCH_THRESHOLD)
    except Exception as e:  # noqa: BLE001
        logger.warning("Glossary similarity-distribution failed: %s", e)
        return {"distribution": [], "total_pairs": 0, "mean_similarity": 0.0, "sample_size": 0, "error": str(e)}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/discovered-synonyms
# (MUST be before /{term_id} to avoid path capture)
# ---------------------------------------------------------------------------
@router.get("/discovered-synonyms")
async def list_discovered_synonyms_early(
    status: Annotated[str, Query()] = "pending",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """List auto-discovered synonym candidates."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            terms = await repo.list_by_kb(
                kb_id="all", status=status, limit=page_size, offset=(page - 1) * page_size,
            )
            discovered = [t for t in terms if t.get("source") == "auto_discovered"]
            return {"synonyms": discovered, "total": len(discovered), "page": page}
        except Exception as e:  # noqa: BLE001
            logger.warning("Discovered synonyms query failed: %s", e)
    return {"synonyms": [], "total": 0, "page": page}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/{term_id}
# ---------------------------------------------------------------------------
@router.get(
    "/{term_id}",
    responses={404: {"description": "Term not found"}},
)
async def get_glossary_term(term_id: str):
    """Get single glossary term."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            term = await repo.get_by_id(term_id)
            if term:
                return term
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary repo get failed: %s", e)
    raise HTTPException(status_code=404, detail=_TERM_NOT_FOUND)


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary
# ---------------------------------------------------------------------------
@router.post(
    "",
    responses={500: {"description": "Failed to create term"}},
)
async def create_glossary_term(body: dict[str, Any]):
    """Create a glossary term."""
    state = _get_state()
    repo = state.get("glossary_repo")
    term_id = body.get("id") or str(uuid.uuid4())
    if repo:
        try:
            term_data = dict(body)
            term_data.setdefault("id", term_id)
            await repo.save(term_data)
            return {"success": True, "term_id": term_id, "message": "Term created"}
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary repo save failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to create term: {e}")
    return {"success": True, "term_id": term_id, "message": "Term created (stub - no DB)"}


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/glossary/{term_id}
# ---------------------------------------------------------------------------
@router.patch(
    "/{term_id}",
    responses={
        403: {"description": "Global standard term is read-only"},
        404: {"description": "Term not found"},
        500: {"description": "Failed to update term"},
    },
)
async def update_glossary_term(term_id: str, body: dict[str, Any]):
    """Update a glossary term. Global standards are read-only."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            existing = await _check_not_global_standard(repo, term_id)
            term_data = dict(body)
            term_data["id"] = term_id
            term_data.setdefault("kb_id", existing["kb_id"])
            term_data.setdefault("term", existing["term"])
            await repo.save(term_data)
            return {"success": True, "term_id": term_id, "message": "Term updated"}
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary repo update failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to update term: {e}")
    return {"success": True, "term_id": term_id, "message": "Term updated (stub - no DB)"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/{term_id}/approve
# ---------------------------------------------------------------------------
@router.post(
    "/{term_id}/approve",
    responses={
        404: {"description": "Term not found"},
        500: {"description": "Failed to approve term"},
    },
)
async def approve_glossary_term(term_id: str, body: dict[str, Any]):
    """Approve a glossary term."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            existing = await repo.get_by_id(term_id)
            if not existing:
                raise HTTPException(status_code=404, detail=_TERM_NOT_FOUND)
            from datetime import UTC, datetime
            update_data = {
                "id": term_id,
                "kb_id": existing["kb_id"],
                "term": existing["term"],
                "status": "approved",
                "approved_by": body.get("approved_by"),
                "approved_at": datetime.now(UTC),
            }
            await repo.save(update_data)
            return {"success": True, "term_id": term_id, "status": "approved"}
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary repo approve failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to approve term: {e}")
    return {"success": True, "term_id": term_id, "status": "approved"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/{term_id}/reject
# ---------------------------------------------------------------------------
@router.post(
    "/{term_id}/reject",
    responses={
        404: {"description": "Term not found"},
        500: {"description": "Failed to reject term"},
    },
)
async def reject_glossary_term(term_id: str, body: dict[str, Any]):
    """Reject a glossary term."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            existing = await repo.get_by_id(term_id)
            if not existing:
                raise HTTPException(status_code=404, detail=_TERM_NOT_FOUND)
            update_data = {
                "id": term_id,
                "kb_id": existing["kb_id"],
                "term": existing["term"],
                "status": "rejected",
            }
            await repo.save(update_data)
            return {"success": True, "term_id": term_id, "status": "rejected"}
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary repo reject failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to reject term: {e}")
    return {"success": True, "term_id": term_id, "status": "rejected"}


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/glossary/{term_id}
# ---------------------------------------------------------------------------
@router.delete(
    "/{term_id}",
    responses={
        403: {"description": "Global standard term is read-only"},
        404: {"description": "Term not found"},
    },
)
async def delete_glossary_term(term_id: str):
    """Delete a glossary term. Global standards are read-only."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            await _check_not_global_standard(repo, term_id)
            deleted = await repo.delete(term_id)
            return {"success": deleted, "term_id": term_id}
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary repo delete failed: %s", e)
    return {"success": True, "term_id": term_id}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/{term_id}/promote-global
# ---------------------------------------------------------------------------
@router.post(
    "/{term_id}/promote-global",
    responses={
        404: {"description": "Term not found"},
        500: {"description": "Failed to promote term"},
    },
)
async def promote_glossary_term_to_global(term_id: str):
    """Promote a glossary term to global scope."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            existing = await repo.get_by_id(term_id)
            if not existing:
                raise HTTPException(status_code=404, detail=_TERM_NOT_FOUND)
            update_data = {
                "id": term_id,
                "kb_id": existing["kb_id"],
                "term": existing["term"],
                "scope": "global",
            }
            await repo.save(update_data)
            return {"success": True, "term_id": term_id, "scope": "global"}
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary repo promote failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to promote term: {e}")
    return {"success": True, "term_id": term_id, "scope": "global"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/import-csv
# ---------------------------------------------------------------------------
@router.post(
    "/import-csv",
    responses={
        400: {"description": "No files provided"},
        503: {"description": "Glossary repository not initialized"},
    },
)
async def import_glossary_csv(
    file: Annotated[UploadFile | None, File()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
    encoding: Annotated[str, Query()] = "utf-8",
    term_type: Annotated[str, Query()] = "term",
    kb_id: Annotated[str, Query()] = "global-standard",
):
    """Import glossary terms from one or multiple CSV files."""
    from src.api.services.glossary_import_service import import_csv

    state = _get_state()
    repo = state.get("glossary_repo")
    if not repo:
        raise HTTPException(status_code=503, detail="Glossary repository not initialized")

    upload_files: list[UploadFile] = []
    if file is not None:
        upload_files.append(file)
    if files is not None:
        upload_files.extend(files)

    if not upload_files:
        raise HTTPException(status_code=400, detail="No files provided")

    result = await import_csv(repo, upload_files, encoding=encoding, kb_id=kb_id)

    # Cache invalidation after glossary import
    search_cache = state.get("search_cache")
    if search_cache:
        try:
            await search_cache.clear()
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to clear search cache after glossary import: %s", e)

    return result


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/glossary/by-type/{term_type}
# ---------------------------------------------------------------------------
@router.delete(
    "/by-type/{term_type}",
    responses={500: {"description": "Failed to delete by type"}},
)
async def delete_glossary_by_type(
    term_type: str,
    kb_id: Annotated[str, Query()] = "global-standard",
):
    """Delete glossary terms by type."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            terms = await repo.list_by_kb(kb_id=kb_id, term_type=term_type, limit=10000, offset=0)
            if terms:
                term_ids = [t["id"] for t in terms]
                deleted = await repo.bulk_delete(term_ids)
                return {"success": True, "deleted": deleted, "term_type": term_type}
            return {"success": True, "deleted": 0, "term_type": term_type}
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary repo delete-by-type failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to delete by type: {e}")
    return {"success": True, "deleted": 0, "term_type": term_type}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/add-synonym
# ---------------------------------------------------------------------------
@router.post(
    "/add-synonym",
    responses={
        400: {"description": "Missing required fields"},
        404: {"description": "Term not found"},
        500: {"description": "Failed to add synonym"},
    },
)
async def add_synonym_to_standard(body: dict[str, Any]):
    """Add synonym to a standard term."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            term_id = body.get("term_id") or body.get("standard_term_id")
            synonym = body.get("synonym", "").strip()
            if not term_id or not synonym:
                raise HTTPException(status_code=400, detail="term_id and synonym are required")
            existing = await repo.get_by_id(term_id)
            if not existing:
                raise HTTPException(status_code=404, detail=_TERM_NOT_FOUND)
            synonyms = existing.get("synonyms", [])
            if synonym not in synonyms:
                synonyms.append(synonym)
            update_data = {
                "id": term_id,
                "kb_id": existing["kb_id"],
                "term": existing["term"],
                "synonyms": synonyms,
            }
            await repo.save(update_data)
            return {"success": True, "message": "Synonym added"}
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary repo add-synonym failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to add synonym: {e}")
    return {"success": True, "message": "Synonym added (stub - no DB)"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/{term_id}/synonyms
# ---------------------------------------------------------------------------
@router.get(
    "/{term_id}/synonyms",
    responses={
        404: {"description": "Term not found"},
        500: {"description": "Failed to list synonyms"},
        503: {"description": "No DB connection"},
    },
)
async def list_synonyms(term_id: str):
    """List synonyms for a glossary term."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            existing = await repo.get_by_id(term_id)
            if not existing:
                raise HTTPException(status_code=404, detail=_TERM_NOT_FOUND)
            synonyms = existing.get("synonyms", [])
            return {
                "term_id": term_id,
                "term": existing.get("term", ""),
                "synonyms": synonyms,
            }
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary repo list-synonyms failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to list synonyms: {e}")
    raise _NO_DB


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/glossary/{term_id}/synonyms/{synonym}
# ---------------------------------------------------------------------------
@router.delete(
    "/{term_id}/synonyms/{synonym}",
    responses={
        404: {"description": "Term or synonym not found"},
        500: {"description": "Failed to remove synonym"},
        503: {"description": "No DB connection"},
    },
)
async def remove_synonym(term_id: str, synonym: str):
    """Remove a synonym from a glossary term."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            existing = await repo.get_by_id(term_id)
            if not existing:
                raise HTTPException(status_code=404, detail=_TERM_NOT_FOUND)
            synonyms = existing.get("synonyms", [])
            if synonym not in synonyms:
                raise HTTPException(status_code=404, detail=f"Synonym '{synonym}' not found on term")
            synonyms.remove(synonym)
            update_data = {
                "id": term_id,
                "kb_id": existing["kb_id"],
                "term": existing["term"],
                "synonyms": synonyms,
            }
            await repo.save(update_data)
            return {"success": True, "message": f"Synonym '{synonym}' removed", "remaining_synonyms": synonyms}
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary repo remove-synonym failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to remove synonym: {e}")
    raise _NO_DB


# (discovered-synonyms GET moved above /{term_id} to avoid path capture)


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/discovered-synonyms/approve
# ---------------------------------------------------------------------------
@router.post(
    "/discovered-synonyms/approve",
    responses={
        400: {"description": "Missing synonym_ids"},
        503: {"description": "No DB connection"},
    },
)
async def approve_discovered_synonyms(body: dict[str, Any]):
    """Approve one or more discovered synonym candidates.

    Approving means: add the synonym to the base term's synonyms list,
    then delete (or mark approved) the discovered synonym record.
    """
    state = _get_state()
    repo = state.get("glossary_repo")
    if not repo:
        raise _NO_DB

    synonym_ids = body.get("synonym_ids", [])
    if not synonym_ids:
        raise HTTPException(status_code=400, detail="synonym_ids list is required")

    approved_count = 0
    errors: list[str] = []
    for syn_id in synonym_ids:
        try:
            ok = await _approve_single_synonym(repo, syn_id, errors)
            if ok:
                approved_count += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{syn_id}: {e}")

    return {"success": approved_count > 0, "approved": approved_count, "errors": errors}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/discovered-synonyms/reject
# ---------------------------------------------------------------------------
@router.post(
    "/discovered-synonyms/reject",
    responses={
        400: {"description": "Missing synonym_ids"},
        503: {"description": "No DB connection"},
    },
)
async def reject_discovered_synonyms(body: dict[str, Any]):
    """Reject one or more discovered synonym candidates."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if not repo:
        raise _NO_DB

    synonym_ids = body.get("synonym_ids", [])
    if not synonym_ids:
        raise HTTPException(status_code=400, detail="synonym_ids list is required")

    rejected_count = 0
    errors: list[str] = []
    for syn_id in synonym_ids:
        try:
            syn_record = await repo.get_by_id(syn_id)
            if not syn_record:
                errors.append(f"{syn_id}: not found")
                continue
            await repo.save({
                "id": syn_id,
                "kb_id": syn_record["kb_id"],
                "term": syn_record["term"],
                "status": "rejected",
            })
            rejected_count += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{syn_id}: {e}")

    return {"success": rejected_count > 0, "rejected": rejected_count, "errors": errors}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/similarity-check
# ---------------------------------------------------------------------------
@router.post(
    "/similarity-check",
    responses={500: {"description": "Similarity check failed"}},
)
async def check_pending_similarity(
    threshold: Annotated[float, Query()] = 0.7,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=1000)] = 500,
):
    """Check pending term similarity. Returns pairs of pending terms that look similar to approved terms."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            offset = (page - 1) * page_size
            pending = await repo.list_by_kb(kb_id="all", status="pending", limit=page_size, offset=offset)
            total = await repo.count_by_kb(kb_id="all", status="pending")
            # Basic string-match similarity pairs (full embedding similarity would need embedder)
            pairs: list[dict[str, Any]] = []
            for term in pending:
                matches = await repo.search(kb_id="all", query=term["term"], limit=3)
                for m in matches:
                    if m["id"] != term["id"]:
                        pairs.append({
                            "pending_term": term["term"],
                            "pending_id": term["id"],
                            "matched_term": m["term"],
                            "matched_id": m["id"],
                            "matched_status": m["status"],
                        })
            return {"pairs": pairs, "total": total, "page": page, "page_size": page_size, "threshold": threshold}
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary similarity-check failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Similarity check failed: {e}")
    return {"pairs": [], "total": 0, "page": page, "page_size": page_size, "threshold": threshold}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/similarity-cleanup
# ---------------------------------------------------------------------------
@router.post(
    "/similarity-cleanup",
    responses={500: {"description": "Similarity cleanup failed"}},
)
async def cleanup_pending_by_similarity(
    threshold: Annotated[float, Query()] = 0.7,
    body: dict[str, Any] | None = None,
):
    """Cleanup pending terms by similarity. Removes pending terms that duplicate approved ones."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            term_ids_to_remove = (body or {}).get("term_ids", [])
            if term_ids_to_remove:
                removed = await repo.bulk_delete(term_ids_to_remove)
                return {"success": True, "removed": removed}
            return {"success": True, "removed": 0}
        except Exception as e:  # noqa: BLE001
            logger.warning("Glossary similarity-cleanup failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Similarity cleanup failed: {e}")
    return {"success": True, "removed": 0}


# (similarity-distribution GET moved above /{term_id} to avoid path capture)
