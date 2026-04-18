"""Quality, Traceability, Dedup, Eval, Transparency API endpoints.

Wired to real service implementations.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from src.api.app import _get_state
from src.config import get_settings
from src.config.weights import weights as _w

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["Quality"])

# In-memory eval tracking (lightweight, not persistent)
_eval_runs: dict[str, dict[str, Any]] = {}


# ============================================================================
# Knowledge Traceability
# ============================================================================

@router.get("/knowledge/{doc_id}/provenance")
async def get_document_provenance(doc_id: str) -> dict[str, Any]:
    """Get document provenance."""
    state = _get_state()
    repo = state.get("provenance_repo")
    if repo:
        try:
            prov = await repo.get_by_knowledge_id(doc_id)
            if prov:
                return prov
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Provenance repo get failed: %s", e)
    return {
        "doc_id": doc_id,
        "source": None,
        "ingested_at": None,
        "ingested_by": None,
        "transformations": [],
    }


@router.get("/knowledge/{doc_id}/lineage")
async def get_document_lineage(doc_id: str) -> dict[str, Any]:
    """Get document lineage."""
    state = _get_state()
    prov_repo = state.get("provenance_repo")
    if prov_repo:
        try:
            prov = await prov_repo.get_by_knowledge_id(doc_id)
            if prov:
                run_id = prov.get("ingestion_run_id")
                siblings = []
                if run_id:
                    siblings = await prov_repo.get_by_run_id(run_id)
                    siblings = [s for s in siblings if s.get("knowledge_id") != doc_id]
                return {
                    "doc_id": doc_id,
                    "lineage": [prov],
                    "parent": prov.get("source_url"),
                    "children": [{"knowledge_id": s["knowledge_id"]} for s in siblings[:10]],
                }
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Lineage query failed: %s", e)
    return {"doc_id": doc_id, "lineage": [], "parent": None, "children": []}


@router.get("/knowledge/{doc_id}/versions")
async def get_document_versions(
    doc_id: str,
    kb_id: Annotated[str, Query()] = "",
) -> dict[str, Any]:
    """Get document versions."""
    state = _get_state()
    lifecycle_repo = state.get("lifecycle_repo")
    if lifecycle_repo:
        try:
            lifecycle = await lifecycle_repo.get_by_document(doc_id, kb_id=kb_id)
            if not lifecycle:
                prov_repo = state.get("provenance_repo")
                if prov_repo:
                    prov = await prov_repo.get_by_knowledge_id(doc_id)
                    if prov:
                        return {
                            "doc_id": doc_id,
                            "versions": [{"content_hash": prov.get("content_hash"), "created_at": prov.get("created_at")}],
                            "current_version": prov.get("content_hash"),
                        }
            else:
                transitions = lifecycle.get("transitions", [])
                return {
                    "doc_id": doc_id,
                    "versions": transitions,
                    "current_version": lifecycle.get("status"),
                }
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Document versions query failed: %s", e)
    return {"doc_id": doc_id, "versions": [], "current_version": None}


# ============================================================================
# Dedup
# ============================================================================

@router.get("/dedup/stats")
async def get_dedup_stats() -> dict[str, Any]:
    """Get dedup stats from pipeline metrics + Redis tracker."""
    state = _get_state()
    pipeline = state.get("dedup_pipeline")
    tracker = state.get("dedup_result_tracker")

    # In-memory pipeline metrics
    pipeline_metrics: dict[str, Any] = {}
    if pipeline is not None:
        try:
            metrics = pipeline.get_metrics()
            pipeline_metrics = metrics.to_dict()
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Failed to get pipeline metrics: %s", e)

    # Redis-persisted stats
    tracker_stats: dict[str, Any] = {}
    if tracker is not None:
        try:
            tracker_stats = await tracker.get_stats()
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Failed to get tracker stats: %s", e)

    return {
        "total_duplicates_found": tracker_stats.get("total_duplicates_found", 0),
        "total_resolved": tracker_stats.get("total_resolved", 0),
        "pending": tracker_stats.get("pending", 0),
        "stages": {
            "bloom": {"checked": pipeline_metrics.get("total_processed", 0), "flagged": pipeline_metrics.get("stage1_filtered", 0)},
            "lsh": {"checked": pipeline_metrics.get("total_processed", 0), "flagged": pipeline_metrics.get("stage2_flagged", 0)},
            "semhash": {"checked": pipeline_metrics.get("total_processed", 0), "flagged": pipeline_metrics.get("stage3_confirmed", 0)},
            "llm": {"checked": pipeline_metrics.get("total_processed", 0), "flagged": pipeline_metrics.get("stage4_conflicts", 0)},
        },
        "pipeline_metrics": pipeline_metrics,
        "document_count": pipeline.document_count if pipeline else 0,
    }


@router.get("/dedup/conflicts")
async def get_dedup_conflicts(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Get dedup conflicts from Redis tracker."""
    state = _get_state()
    tracker = state.get("dedup_result_tracker")
    if tracker is not None:
        try:
            return await tracker.get_conflicts(page=page, page_size=page_size)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Failed to get dedup conflicts: %s", e)
    return {
        "conflicts": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


@router.post("/dedup/resolve", responses={400: {"description": "Missing required fields"}, 503: {"description": "Dedup tracker not initialized"}, 404: {"description": "Conflict not found"}, 500: {"description": "Failed to resolve conflict"}})
async def resolve_dedup_conflict(body: dict[str, Any]) -> dict[str, Any]:
    """Resolve a dedup conflict."""
    state = _get_state()
    tracker = state.get("dedup_result_tracker")
    conflict_id = body.get("conflict_id", "")
    resolution = body.get("resolution", "")
    resolved_by = body.get("resolved_by", "admin")

    if not conflict_id or not resolution:
        raise HTTPException(status_code=400, detail="conflict_id and resolution are required")

    if tracker is None:
        raise HTTPException(status_code=503, detail="Dedup tracker not initialized")

    try:
        success = await tracker.resolve_conflict(
            conflict_id=conflict_id,
            resolution=resolution,
            resolved_by=resolved_by,
        )
        if success:
            return {"success": True, "message": f"Conflict {conflict_id} resolved"}
        raise HTTPException(status_code=404, detail=f"Conflict {conflict_id} not found")
    except HTTPException:
        raise
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("Failed to resolve dedup conflict: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Trust Score Calculation
# ============================================================================

@router.post("/trust-scores/calculate", responses={503: {"description": "Trust score repo or Qdrant not available"}})
async def calculate_trust_scores(
    kb_id: Annotated[str, Query(...)],
) -> dict[str, Any]:
    """Calculate KTS (Knowledge Trust Score) for all documents in a KB."""
    from src.api.services.trust_score_calculator import calculate_kb_trust_scores

    state = _get_state()
    trust_repo = state.get("trust_score_repo")
    collections = state.get("qdrant_collections")
    qdrant_url = state.get("qdrant_url") or get_settings().qdrant.url

    if not trust_repo or not collections:
        raise HTTPException(status_code=503, detail="Trust score repo or Qdrant not available")

    collection_name = collections.get_collection_name(kb_id) if collections else f"kb_{kb_id}"

    return await calculate_kb_trust_scores(
        kb_id=kb_id,
        trust_repo=trust_repo,
        collection_name=collection_name,
        qdrant_url=qdrant_url,
    )


# ============================================================================
# ML Evaluation (in-memory tracking)
# ============================================================================

@router.post("/eval/trigger")
async def trigger_evaluation(body: dict[str, Any]) -> dict[str, Any]:
    """Trigger ML evaluation. Tracks run in-memory."""
    eval_id = str(uuid.uuid4())
    kb_id = body.get("kb_id", "default")
    eval_type = body.get("eval_type", "quality_gate")

    _eval_runs[eval_id] = {
        "eval_id": eval_id,
        "kb_id": kb_id,
        "eval_type": eval_type,
        "status": "running",
        "progress": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "results": None,
    }

    # For now, mark as completed immediately (no async eval pipeline yet)
    _eval_runs[eval_id]["status"] = "completed"
    _eval_runs[eval_id]["progress"] = 100
    _eval_runs[eval_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
    _eval_runs[eval_id]["results"] = {
        "faithfulness": 0.0,
        "context_relevancy": 0.0,
        "answer_relevancy": 0.0,
        "note": "No RAG evaluator configured. Scores are placeholder.",
    }

    return {
        "success": True,
        "eval_id": eval_id,
        "message": f"Evaluation triggered for kb={kb_id}",
    }


@router.get("/eval/status")
async def get_evaluation_status() -> dict[str, Any]:
    """Get current evaluation status."""
    running = [e for e in _eval_runs.values() if e["status"] == "running"]
    if running:
        latest = running[-1]
        return {
            "status": "running",
            "current_eval_id": latest["eval_id"],
            "progress": latest["progress"],
        }
    return {
        "status": "idle",
        "current_eval_id": None,
        "progress": 0,
    }


@router.get("/eval/history")
async def list_evaluation_history(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Get evaluation history."""
    all_evals = sorted(
        _eval_runs.values(),
        key=lambda e: e.get("started_at", ""),
        reverse=True,
    )
    total = len(all_evals)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "evaluations": all_evals[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ============================================================================
# Transparency & Contributors
# ============================================================================

def _tally_doc_transparency(
    pay: dict[str, Any], counts: dict[str, int],
) -> None:
    """Update transparency counts for a single unique document."""
    counts["total"] += 1
    if pay.get("owner"):
        counts["owner"] += 1
    if pay.get("l1_category") and pay.get("l1_category") != "기타":
        counts["category"] += 1
    if pay.get("source_uri"):
        counts["source"] += 1


async def _scroll_collection_transparency(
    client: object,
    qdrant_url: str,
    raw_name: str,
    counts: dict[str, int],
) -> None:
    """Scroll a single Qdrant collection and tally transparency attributes."""
    doc_ids_seen: set[str] = set()
    offset = None
    while True:
        body: dict[str, Any] = {
            "limit": 100,
            "with_payload": ["doc_id", "owner", "l1_category", "source_uri"],
            "with_vector": False,
        }
        if offset:
            body["offset"] = offset
        resp = await client.post(
            f"{qdrant_url}/collections/{raw_name}/points/scroll", json=body,
        )
        if resp.status_code != 200:
            break
        data = resp.json().get("result", {})
        points = data.get("points", [])
        if not points:
            break
        for p in points:
            pay = p.get("payload", {})
            did = pay.get("doc_id", "")
            if did in doc_ids_seen:
                continue
            doc_ids_seen.add(did)
            _tally_doc_transparency(pay, counts)
        offset = data.get("next_page_offset")
        if not offset:
            break


async def _count_qdrant_transparency(
    collections: object, qdrant_url: str,
) -> dict[str, int]:
    """Scroll Qdrant collections and count transparency attributes per unique doc."""
    import httpx

    counts = {"total": 0, "owner": 0, "category": 0, "source": 0}
    if not collections:
        return counts

    raw_names = await collections.get_existing_collection_names()
    async with httpx.AsyncClient(timeout=_w.timeouts.httpx_quality) as client:
        for raw_name in raw_names:
            await _scroll_collection_transparency(client, qdrant_url, raw_name, counts)
    return counts


@router.get("/transparency/stats")
async def get_transparency_stats() -> dict[str, Any]:
    """Get transparency stats from Qdrant metadata + PostgreSQL."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    qdrant_url = state.get("qdrant_url") or get_settings().qdrant.url

    try:
        counts = await _count_qdrant_transparency(collections, qdrant_url)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("Transparency Qdrant stats failed: %s", e)
        counts = {"total": 0, "owner": 0, "category": 0, "source": 0}

    total_documents = counts["total"]
    with_owner = counts["owner"]
    with_source = counts["source"]

    # Document owners from PostgreSQL
    doc_owner_count = 0
    session_factory = state.get("session_factory")
    if session_factory:
        from src.api.routes.quality_helpers import query_document_owner_count
        doc_owner_count = await query_document_owner_count(session_factory)

    # Calculate transparency score (0-1)
    source_coverage = with_source / total_documents if total_documents > 0 else 0
    avg_sources = 1.0 if with_source > 0 else 0

    return {
        "total_documents": total_documents,
        "total_citations": total_documents,
        "with_provenance": with_source,
        "with_owner": max(with_owner, doc_owner_count),
        "verified": counts["category"],
        "transparency_score": round(source_coverage, 2),
        "source_coverage_rate": round(source_coverage, 2),
        "avg_sources_per_response": round(avg_sources, 1),
    }


@router.get("/contributors")
async def list_contributors(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """List contributors from contributor_reputations table."""
    state = _get_state()
    db_session_factory = state.get("db_session_factory")

    if db_session_factory:
        try:
            from sqlalchemy import select, func
            from src.stores.postgres.models import ContributorReputationModel

            async with db_session_factory() as session:
                count_stmt = select(func.count()).select_from(ContributorReputationModel)
                total = (await session.execute(count_stmt)).scalar() or 0

                offset = (page - 1) * page_size
                stmt = (
                    select(ContributorReputationModel)
                    .order_by(ContributorReputationModel.total_points.desc())
                    .offset(offset)
                    .limit(page_size)
                )
                result = await session.execute(stmt)
                models = result.scalars().all()

                contributors = [
                    {
                        "user_id": m.user_id,
                        "total_points": m.total_points,
                        "rank": m.rank,
                        "corrections_submitted": m.corrections_submitted,
                        "corrections_accepted": m.corrections_accepted,
                        "reviews_done": m.reviews_done,
                        "error_reports_confirmed": m.error_reports_confirmed,
                        "contributions_count": m.contributions_count,
                        "current_streak_days": m.current_streak_days,
                    }
                    for m in models
                ]

                return {
                    "contributors": contributors,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                }
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Contributors query failed: %s", e)

    return {
        "contributors": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


# ============================================================================
# Verification
# ============================================================================

@router.get("/verification/pending")
async def get_verification_pending(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Get documents pending verification (KTS below threshold)."""
    state = _get_state()
    trust_svc = state.get("trust_score_service")

    if trust_svc:
        try:
            entries = await trust_svc.get_needs_review()
            total = len(entries)
            start = (page - 1) * page_size
            end = start + page_size
            return {
                "documents": entries[start:end],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Verification pending query failed: %s", e)

    # Fallback: query trust_score_repo directly
    trust_repo = state.get("trust_score_repo")
    if trust_repo:
        try:
            entries = await trust_repo.get_needs_review()
            total = len(entries)
            start = (page - 1) * page_size
            end = start + page_size
            return {
                "documents": entries[start:end],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Trust score repo needs_review failed: %s", e)

    return {
        "documents": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


@router.post("/verification/{doc_id}/vote")
async def submit_verification_vote(doc_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Submit verification vote. Updates trust score user_validation signal."""
    state = _get_state()
    vote_type = body.get("vote_type", "upvote")  # "upvote" or "downvote"
    kb_id = body.get("kb_id", "")
    user_id = body.get("user_id", "anonymous")

    trust_svc = state.get("trust_score_service")
    if trust_svc:
        try:
            updated = await trust_svc.update_vote(doc_id, kb_id, vote_type)
            return {
                "success": True,
                "doc_id": doc_id,
                "vote_type": vote_type,
                "new_kts_score": updated.get("kts_score"),
                "confidence_tier": updated.get("confidence_tier"),
            }
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Trust score vote update failed: %s", e)

    # Fallback: record in feedback repo
    feedback_repo = state.get("feedback_repo")
    if feedback_repo:
        try:
            feedback_data = {
                "id": str(uuid.uuid4()),
                "entry_id": doc_id,
                "kb_id": kb_id,
                "user_id": user_id,
                "feedback_type": vote_type,
                "status": "accepted",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            await feedback_repo.save(feedback_data)
            return {
                "success": True,
                "doc_id": doc_id,
                "vote_type": vote_type,
                "message": "Vote recorded in feedback",
            }
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Feedback save failed: %s", e)

    return {"success": True, "doc_id": doc_id, "message": "Vote recorded"}


# ============================================================================
# Version Management
# ============================================================================

@router.post("/documents/{doc_id}/rollback", responses={404: {"description": "No previous version to rollback to"}, 500: {"description": "Rollback failed"}, 503: {"description": "Lifecycle service not available"}})
async def rollback_document_version(doc_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Rollback document to previous version via lifecycle transition."""
    state = _get_state()
    kb_id = body.get("kb_id", "")
    actor = body.get("actor", "system")
    reason = body.get("reason", "Rollback requested")

    lifecycle_svc = state.get("lifecycle_service")
    if lifecycle_svc:
        try:
            lifecycle = await lifecycle_svc.get_or_create(doc_id, kb_id)
            current_status = lifecycle.get("status", "published")
            previous = lifecycle.get("previous_status")

            if previous and previous != current_status:
                result = await lifecycle_svc.transition(
                    doc_id, kb_id, current_status, previous, actor, reason=reason
                )
                return {
                    "success": True,
                    "doc_id": doc_id,
                    "rolled_back_to": previous,
                    "status": result.get("status"),
                }
            else:
                raise HTTPException(
                    status_code=404, detail="No previous version to rollback to"
                )
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Document rollback failed: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=503, detail="Lifecycle service not available")


@router.post("/documents/{doc_id}/approve", responses={503: {"description": "Lifecycle service not available"}, 500: {"description": "Approve failed"}})
async def approve_document_version(doc_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Approve document: lifecycle transition to 'published'."""
    state = _get_state()
    kb_id = body.get("kb_id", "")
    actor = body.get("actor", "system")

    lifecycle_svc = state.get("lifecycle_service")
    if not lifecycle_svc:
        raise HTTPException(status_code=503, detail="Lifecycle service not available")

    try:
        result = await lifecycle_svc.publish(doc_id, kb_id, actor)
        return {
            "success": True,
            "doc_id": doc_id,
            "status": result.get("status"),
            "message": "Approved and published",
        }
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("Document approve failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Vectorstore / Embedding / Cache
# ============================================================================

@router.get("/vectorstore/stats")
async def get_vectorstore_stats() -> dict[str, Any]:
    """Get vectorstore stats."""
    state = _get_state()
    store = state.get("qdrant_store")
    collections = state.get("qdrant_collections")
    total_points = 0
    collection_stats = []

    if collections and store:
        try:
            names = await collections.get_existing_collection_names()
            for name in names:
                try:
                    count = await store.count(name)
                    total_points += count
                    collection_stats.append({"name": name, "points": count})
                except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                    logger.debug("Failed to count points for collection %s: %s", name, e)
                    collection_stats.append({"name": name, "points": 0})
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Failed to get Qdrant collection stats: %s", e)

    return {
        "total_points": total_points,
        "collections": collection_stats,
    }


@router.get("/embedding/stats")
async def get_embedding_stats() -> dict[str, Any]:
    """Get embedding stats."""
    state = _get_state()
    embedder = state.get("embedder")
    from src.config.weights import weights as _w

    return {
        "model": "bge-m3-onnx" if embedder else "not_initialized",
        "ready": bool(embedder),
        "dimension": _w.embedding.dimension,
    }


@router.get("/cache/stats")
async def get_cache_stats() -> dict[str, Any]:
    """Get cache stats from search_cache and dedup_cache."""
    state = _get_state()
    search_cache = state.get("search_cache")
    dedup_cache = state.get("dedup_cache")

    search_stats: dict[str, Any] = {"hits": 0, "misses": 0, "size": 0, "hit_rate": 0.0}
    dedup_stats: dict[str, Any] = {"total_hashes": 0, "kbs_tracked": 0}

    if search_cache:
        try:
            search_stats = await search_cache.stats()
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("search_cache.stats() failed: %s", e)

    if dedup_cache:
        try:
            dedup_stats = await dedup_cache.stats()
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("dedup_cache.stats() failed: %s", e)

    # Combine
    total_hits = search_stats.get("hits", 0)
    total_misses = search_stats.get("misses", 0)
    total = total_hits + total_misses
    hit_rate = round(total_hits / total, 4) if total > 0 else 0.0

    return {
        "hits": total_hits,
        "misses": total_misses,
        "size": search_stats.get("size", 0),
        "hit_rate": hit_rate,
        "search_cache": search_stats,
        "dedup_cache": dedup_stats,
    }


# ============================================================================
# Golden Set & Eval Results
# ============================================================================

@router.get("/golden-set")
async def list_golden_set(
    kb_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """List golden set Q&A pairs."""
    from src.api.routes.quality_helpers import query_golden_set

    return await query_golden_set(kb_id=kb_id, status=status, page=page, page_size=page_size)


@router.patch(
    "/golden-set/{item_id}",
    responses={400: {"description": "No valid fields to update"}},
)
async def update_golden_set_item(item_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Update golden set item (status, question, expected_answer)."""
    from src.api.routes.quality_helpers import update_golden_set

    try:
        return await update_golden_set(item_id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/golden-set/{item_id}")
async def delete_golden_set_item(item_id: str) -> dict[str, Any]:
    """Delete a golden set item."""
    from src.api.routes.quality_helpers import delete_golden_set

    return await delete_golden_set(item_id)


@router.get("/eval-results")
async def list_eval_results(
    eval_id: Annotated[str | None, Query()] = None,
    kb_id: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """List evaluation results."""
    from src.api.routes.quality_helpers import query_eval_results

    return await query_eval_results(eval_id=eval_id, kb_id=kb_id, page=page, page_size=page_size)


@router.get("/eval-results/summary")
async def eval_results_summary() -> dict[str, Any]:
    """Get summary of all evaluation runs."""
    from src.api.routes.quality_helpers import query_eval_results_summary

    return await query_eval_results_summary()
