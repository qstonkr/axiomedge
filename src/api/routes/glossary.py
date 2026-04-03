"""Glossary API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from src.api.app import _get_state
from src.config_weights import weights as _w

logger = logging.getLogger(__name__)

_NO_DB = HTTPException(status_code=503, detail="No DB connection")

_EXACT_MATCH_THRESHOLD = _w.similarity.exact_match_threshold
router = APIRouter(prefix="/api/v1/admin/glossary", tags=["Glossary"])


async def _check_not_global_standard(repo: Any, term_id: str) -> dict[str, Any]:
    """Check term exists and is NOT a global standard (read-only).

    Global standards (scope='global', source from CSV import) are read-only.
    They can only be updated via CSV re-import.
    """
    existing = await repo.get_by_id(term_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Term not found")
    if existing.get("scope") == "global" and existing.get("source") not in ("manual", "auto_discovered"):
        raise HTTPException(
            status_code=403,
            detail="글로벌 표준 용어/단어는 수정/삭제할 수 없습니다. CSV 재임포트로만 갱신 가능합니다."
        )
    return existing


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
        except Exception as e:
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
    except Exception as e:
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
    except Exception as e:
        logger.warning("Source stats failed: %s", e)
        return {"sources": {}, "error": str(e)}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/similarity-distribution
# (MUST be before /{term_id} to avoid path capture)
# ---------------------------------------------------------------------------
@router.get("/similarity-distribution")
async def get_similarity_distribution():
    """Get similarity score distribution with RapidFuzz sampling (500 samples, top-K matching)."""
    import numpy as np

    state = _get_state()
    repo = state.get("glossary_repo")
    if not repo:
        return {"distribution": [], "total_pairs": 0, "mean_similarity": 0.0, "sample_size": 0}

    try:
        total_approved = await repo.count_by_kb(kb_id="all", status="approved")
        total_pending = await repo.count_by_kb(kb_id="all", status="pending")

        # Sample size and random sampling
        SAMPLE_SIZE = 500
        STANDARD_POOL = 5000

        # Random sampling via SQL OFFSET with random spread
        import random
        pending_total = await repo.count_by_kb(kb_id="all", status="pending")
        approved_total = await repo.count_by_kb(kb_id="all", status="approved")

        if pending_total > 0:
            # Random offsets across the full range
            max_offset = max(0, pending_total - SAMPLE_SIZE)
            rand_offset = random.randint(0, max_offset) if max_offset > 0 else 0
            sample_terms = await repo.list_by_kb(kb_id="all", status="pending", limit=SAMPLE_SIZE, offset=rand_offset)
            sample_source = "pending"
        else:
            # Sample from approved with random offset
            max_offset = max(0, approved_total - SAMPLE_SIZE)
            rand_offset = random.randint(0, max_offset) if max_offset > 0 else 0
            sample_terms = await repo.list_by_kb(kb_id="all", status="approved", limit=SAMPLE_SIZE, offset=rand_offset)
            sample_source = "approved"

        # Standard terms: also random sample for fair comparison
        std_max_offset = max(0, approved_total - STANDARD_POOL)
        std_rand_offset = random.randint(0, std_max_offset) if std_max_offset > 0 else 0
        standard_terms = await repo.list_by_kb(kb_id="all", status="approved", limit=STANDARD_POOL, offset=std_rand_offset)
        standard_names = [t["term"] for t in standard_terms if t.get("term")]

        if not sample_terms or not standard_names:
            return {
                "distribution": [
                    {"status": "approved", "count": total_approved},
                    {"status": "pending", "count": total_pending},
                ],
                "total_pairs": 0,
                "mean_similarity": 0.0,
                "sample_size": 0,
                "standard_count": len(standard_names),
            }

        # RapidFuzz batch similarity
        from rapidfuzz import fuzz, process

        rf_scores = []
        jac_scores = []

        # Build UNIQUE standard terms (deduplicated by lower(term))
        seen_terms: dict[str, dict] = {}  # lower(term) -> full term dict
        for t in standard_terms:
            term_lower = t.get("term", "").lower()
            if term_lower and term_lower not in seen_terms:
                seen_terms[term_lower] = t
        unique_standard = list(seen_terms.values())
        unique_standard_names = [t["term"] for t in unique_standard]

        # Build term->definition map for definition-based comparison
        std_definitions = {}
        for t in unique_standard:
            defn = (t.get("definition") or t.get("term_ko") or "").strip()
            if defn:
                std_definitions[t["term"]] = defn

        # Build sample with unique terms only
        sample_seen = set()
        unique_sample = []
        for t in sample_terms:
            term_lower = t.get("term", "").lower()
            if term_lower and term_lower not in sample_seen:
                sample_seen.add(term_lower)
                unique_sample.append(t)
        sample_terms = unique_sample

        # Exclude sample terms from comparison set
        comparison_names = [n for n in unique_standard_names if n.lower() not in sample_seen]
        comparison_definitions = [std_definitions.get(n, "") for n in comparison_names]
        # Filter to only terms that have definitions
        comp_with_def = [(n, d) for n, d in zip(comparison_names, comparison_definitions) if d]

        if not comparison_names:
            comparison_names = unique_standard_names

        def_scores = []  # Definition-based similarity

        for term_data in sample_terms[:SAMPLE_SIZE]:
            query = term_data.get("term", "")
            if not query:
                continue

            # 1) RapidFuzz term-name similarity (exclude exact self)
            rf_result = process.extractOne(query, comparison_names, scorer=fuzz.WRatio)
            if rf_result:
                score = rf_result[1] / 100.0
                if score >= _EXACT_MATCH_THRESHOLD and rf_result[0].lower() == query.lower():
                    results = process.extract(query, comparison_names, scorer=fuzz.WRatio, limit=2)
                    if len(results) > 1:
                        score = results[1][1] / 100.0
                rf_scores.append(score)

            # 2) Jaccard n-gram (trigram)
            q_ngrams = set(query[i:i+3] for i in range(max(1, len(query)-2)))
            best_jac = 0.0
            for std in comparison_names[:1000]:
                if std.lower() == query.lower():
                    continue
                s_ngrams = set(std[i:i+3] for i in range(max(1, len(std)-2)))
                if q_ngrams and s_ngrams:
                    intersection = len(q_ngrams & s_ngrams)
                    union = len(q_ngrams | s_ngrams)
                    if union > 0:
                        jac = intersection / union
                        if jac > best_jac:
                            best_jac = jac
            jac_scores.append(best_jac)

            # 3) Definition-based similarity
            query_def = (term_data.get("definition") or term_data.get("term_ko") or "").strip()
            if query_def and comp_with_def:
                comp_def_texts = [d for _, d in comp_with_def]
                def_result = process.extractOne(query_def, comp_def_texts, scorer=fuzz.WRatio)
                if def_result:
                    def_score = def_result[1] / 100.0
                    # Exclude exact self-definition
                    if def_score >= _EXACT_MATCH_THRESHOLD:
                        def_results = process.extract(query_def, comp_def_texts, scorer=fuzz.WRatio, limit=2)
                        if len(def_results) > 1:
                            def_score = def_results[1][1] / 100.0
                    def_scores.append(def_score)
                else:
                    def_scores.append(0.0)
            else:
                def_scores.append(0.0)

        # 4) Dense cosine similarity via embedding
        dense_scores = []
        embedder = state.get("embedder")
        if embedder:
            try:
                import asyncio as _asyncio

                # Batch embed sample terms
                sample_texts = [t.get("term", "") for t in sample_terms[:SAMPLE_SIZE] if t.get("term")]
                comp_texts = comparison_names[:1000]  # Limit for performance

                sample_vecs = await _asyncio.to_thread(
                    embedder.encode, sample_texts, True, False, False
                )
                comp_vecs = await _asyncio.to_thread(
                    embedder.encode, comp_texts, True, False, False
                )

                s_dense = sample_vecs.get("dense_vecs", [])
                c_dense = comp_vecs.get("dense_vecs", [])

                if s_dense and c_dense:
                    s_matrix = np.array(s_dense, dtype=np.float32)
                    c_matrix = np.array(c_dense, dtype=np.float32)

                    # L2 normalize
                    s_norms = np.linalg.norm(s_matrix, axis=1, keepdims=True)
                    c_norms = np.linalg.norm(c_matrix, axis=1, keepdims=True)
                    s_matrix = s_matrix / np.clip(s_norms, 1e-12, None)
                    c_matrix = c_matrix / np.clip(c_norms, 1e-12, None)

                    # Cosine similarity matrix [sample x comparison]
                    sim_matrix = s_matrix @ c_matrix.T

                    # For each sample, find best match (excluding self)
                    for i, term_data in enumerate(sample_terms[:len(s_dense)]):
                        query_lower = term_data.get("term", "").lower()
                        row = sim_matrix[i]
                        # Mask self-matches
                        for j, cn in enumerate(comp_texts[:len(c_dense)]):
                            if cn.lower() == query_lower:
                                row[j] = -1.0
                        best = float(np.max(row))
                        dense_scores.append(max(0.0, best))
            except Exception as e:
                logger.warning("Dense similarity computation failed: %s", e)

        # Compute stats
        def _stats(scores):
            if not scores:
                return {"count": 0, "mean": 0, "p50": 0, "p90": 0, "p95": 0, "min": 0, "max": 0}
            arr = np.array(scores)
            return {
                "count": len(arr),
                "mean": float(np.mean(arr)),
                "p50": float(np.percentile(arr, 50)),
                "p90": float(np.percentile(arr, 90)),
                "p95": float(np.percentile(arr, 95)),
                "min": float(np.min(arr)),
                "max": float(np.max(arr)),
            }

        return {
            "distribution": [
                {"status": "approved", "count": total_approved},
                {"status": "pending", "count": total_pending},
            ],
            "total_pairs": len(rf_scores),
            "mean_similarity": float(np.mean(rf_scores)) if rf_scores else 0.0,
            "sample_size": len(sample_terms[:SAMPLE_SIZE]),
            "sample_source": sample_source,
            "standard_count": len(comparison_names),
            "score_stats": {
                "rapidfuzz": _stats(rf_scores),
                "jaccard": _stats(jac_scores),
                "definition": _stats(def_scores),
                "dense_cosine": _stats(dense_scores),
            },
            "rapidfuzz_scores": rf_scores[:200],
            "jaccard_scores": jac_scores[:200],
            "definition_scores": def_scores[:200],
            "dense_cosine_scores": dense_scores[:200],
        }
    except Exception as e:
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
        except Exception as e:
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
        except Exception as e:
            logger.warning("Glossary repo get failed: %s", e)
    raise HTTPException(status_code=404, detail="Term not found")


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
        except Exception as e:
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
        except Exception as e:
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
                raise HTTPException(status_code=404, detail="Term not found")
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
        except Exception as e:
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
                raise HTTPException(status_code=404, detail="Term not found")
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
        except Exception as e:
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
        except Exception as e:
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
                raise HTTPException(status_code=404, detail="Term not found")
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
        except Exception as e:
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
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
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
        except Exception as e:
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
            # Get all terms of this type, then bulk delete
            terms = await repo.list_by_kb(kb_id=kb_id, term_type=term_type, limit=10000, offset=0)
            if terms:
                term_ids = [t["id"] for t in terms]
                deleted = await repo.bulk_delete(term_ids)
                return {"success": True, "deleted": deleted, "term_type": term_type}
            return {"success": True, "deleted": 0, "term_type": term_type}
        except Exception as e:
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
                raise HTTPException(status_code=404, detail="Term not found")
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
        except Exception as e:
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
                raise HTTPException(status_code=404, detail="Term not found")
            synonyms = existing.get("synonyms", [])
            return {
                "term_id": term_id,
                "term": existing.get("term", ""),
                "synonyms": synonyms,
            }
        except HTTPException:
            raise
        except Exception as e:
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
                raise HTTPException(status_code=404, detail="Term not found")
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
        except Exception as e:
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
            syn_record = await repo.get_by_id(syn_id)
            if not syn_record:
                errors.append(f"{syn_id}: not found")
                continue
            base_term_id = syn_record.get("related_terms", [None])[0] if syn_record.get("related_terms") else None
            synonym_text = syn_record.get("term", "")
            if base_term_id:
                # Add synonym to the base term
                base_term = await repo.get_by_id(base_term_id)
                if base_term:
                    existing_synonyms = base_term.get("synonyms", [])
                    if synonym_text and synonym_text not in existing_synonyms:
                        existing_synonyms.append(synonym_text)
                    await repo.save({
                        "id": base_term_id,
                        "kb_id": base_term["kb_id"],
                        "term": base_term["term"],
                        "synonyms": existing_synonyms,
                    })
            # Mark synonym record as approved
            await repo.save({
                "id": syn_id,
                "kb_id": syn_record["kb_id"],
                "term": syn_record["term"],
                "status": "approved",
            })
            approved_count += 1
        except Exception as e:
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
        except Exception as e:
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
        except Exception as e:
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
        except Exception as e:
            logger.warning("Glossary similarity-cleanup failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Similarity cleanup failed: {e}")
    return {"success": True, "removed": 0}


# (similarity-distribution GET moved above /{term_id} to avoid path capture)
