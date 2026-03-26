"""Glossary API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging
import unicodedata
import uuid
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from src.api.app import _get_state
from src.nlp.morpheme_analyzer import get_analyzer
from src.nlp.term_normalizer import TermNormalizer

logger = logging.getLogger(__name__)
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
    kb_id: str = Query(default="all"),
    status: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    term_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
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
        seen_terms: dict[str, dict] = {}  # lower(term) → full term dict
        for t in standard_terms:
            term_lower = t.get("term", "").lower()
            if term_lower and term_lower not in seen_terms:
                seen_terms[term_lower] = t
        unique_standard = list(seen_terms.values())
        unique_standard_names = [t["term"] for t in unique_standard]

        # Build term→definition map for definition-based comparison
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
                if score >= 0.999 and rf_result[0].lower() == query.lower():
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
                    if def_score >= 0.999:
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
    status: str = Query(default="pending"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
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
@router.get("/{term_id}")
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
@router.post("")
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
@router.patch("/{term_id}")
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
@router.post("/{term_id}/approve")
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
@router.post("/{term_id}/reject")
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
@router.delete("/{term_id}")
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
@router.post("/{term_id}/promote-global")
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
@router.post("/import-csv")
async def import_glossary_csv(
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    encoding: str = Query(default="utf-8"),
    term_type: str = Query(default="term"),
    kb_id: str = Query(default="global-standard"),
):
    """Import glossary terms from one or multiple CSV files.

    Supports Korean column headers (표준분류, 논리명, 물리명, etc.) and
    auto-detects term_type from 구성정보 (composition_info) column.
    Uses batch inserts for performance on large files (75K+ rows).
    """
    import csv
    import io

    state = _get_state()
    repo = state.get("glossary_repo")
    if not repo:
        return {"success": False, "imported": 0, "skipped": 0, "errors": ["No DB connection"]}

    # Korean column name mapping (P1-5) — extended for actual CSV format
    _KO_COLUMN_MAP = {
        "물리명": "term",
        "논리명": "term_ko",
        "정의": "definition",
        "동의어": "synonyms",
        "약어": "abbreviations",
        "물리의미": "physical_meaning",
        "구성정보": "composition_info",
        "도메인명": "domain_name",
        "표준분류": "source",
        "데이터타입": "data_type",
        "데이터길이": "data_length",
        "데이터소수점": "data_decimal",
    }

    BATCH_SIZE = 500

    morpheme_analyzer = get_analyzer()
    term_normalizer = TermNormalizer()

    # 단일 + 멀티 파일 통합
    upload_files: list[UploadFile] = []
    if file is not None:
        upload_files.append(file)
    if files is not None:
        upload_files.extend(files)

    if not upload_files:
        return {"success": False, "imported": 0, "skipped": 0, "errors": ["No files provided"]}

    total_imported = 0
    total_skipped = 0
    all_errors: list[str] = []
    word_count = 0
    term_count = 0

    for uf in upload_files:
        fname = uf.filename or "unknown.csv"
        try:
            content = await uf.read()
            text = content.decode(encoding)
            reader = csv.DictReader(io.StringIO(text))

            # P1-5: Validate CSV column headers
            if reader.fieldnames and "term" not in reader.fieldnames:
                # Check alternative Korean column names
                has_korean_col = any(k in (reader.fieldnames or []) for k in _KO_COLUMN_MAP)
                if not has_korean_col:
                    all_errors.append(
                        f"{fname}: 필수 컬럼 'term' 또는 '물리명'이 없습니다. "
                        f"발견된 컬럼: {reader.fieldnames}"
                    )
                    continue

            batch: list[dict[str, Any]] = []

            for row_num, row in enumerate(reader, start=2):
                # P1-5: Apply Korean column mapping
                mapped_row: dict[str, Any] = {}
                for k, v in row.items():
                    if k is None:
                        continue
                    mapped_key = _KO_COLUMN_MAP.get(k, k)
                    mapped_row[mapped_key] = v
                row = mapped_row

                term = row.get("term", "").strip()
                if not term:
                    total_skipped += 1
                    continue

                # P0-3: Unicode NFC normalization
                term = unicodedata.normalize("NFC", term)

                # P1-6: Strip Korean particles
                term = morpheme_analyzer.strip_particles(term)

                try:
                    synonyms_raw = row.get("synonyms", "") or ""
                    abbreviations_raw = row.get("abbreviations", "") or ""
                    synonyms = [s.strip() for s in synonyms_raw.split(",") if s.strip()]
                    abbreviations = [a.strip() for a in abbreviations_raw.split(",") if a.strip()]

                    # Auto-enrich from physical_meaning (물리의미)
                    # e.g., SCOR→Score, CNT→Count — 영문 풀네임을 유의어로
                    physical_meaning = (row.get("physical_meaning", "") or "").strip()
                    if physical_meaning:
                        # 물리의미가 영문이면 synonym으로 추가
                        pm_lower = physical_meaning.lower()
                        existing_lower = {s.lower() for s in synonyms}
                        if pm_lower not in existing_lower and pm_lower != term.lower():
                            synonyms.append(physical_meaning)

                    # Auto-enrich: 물리명(term)이 영문 약어이면 abbreviations에
                    # 논리명(term_ko)이 한국어이면 term↔term_ko 양방향 매핑
                    term_ko = (row.get("term_ko", "") or "").strip()
                    if term_ko and term_ko.lower() != term.lower():
                        # 논리명이 있고 물리명과 다르면 → 서로 유의어
                        if term_ko.lower() not in {s.lower() for s in synonyms}:
                            synonyms.append(term_ko)

                    # P2-7: Auto-detect abbreviations
                    if term_normalizer.is_likely_abbreviation(term) and not abbreviations:
                        abbreviations = [term]

                    status = row.get("status", "pending")
                    scope = row.get("scope", "global")

                    # P0-2: Global terms are pre-approved
                    if scope == "global":
                        status = "approved"

                    # Auto-detect term_type from composition_info
                    composition = row.get("composition_info", "").strip()
                    has_composition_col = "composition_info" in row or "구성정보" in row

                    if composition:
                        # 구성정보 있으면: 단어 수로 판별
                        words_in_comp = composition.split()
                        if len(words_in_comp) <= 1:
                            auto_term_type = "word"
                        else:
                            auto_term_type = "term"
                    elif not has_composition_col:
                        # 구성정보 컬럼 자체가 없는 CSV (단어사전 CSV)
                        # → 물리명이 언더스코어 없는 짧은 영문 = word
                        if "_" not in term and len(term) <= 10:
                            auto_term_type = "word"
                        else:
                            auto_term_type = "term"
                    else:
                        # 구성정보 컬럼은 있지만 값이 비어있음
                        auto_term_type = "word"

                    # CSV term_type column overrides auto-detection
                    final_term_type = row.get("term_type", auto_term_type)

                    if final_term_type == "word":
                        word_count += 1
                    else:
                        term_count += 1

                    # Build source value: use 표준분류 if available, else csv_import
                    source_val = row.get("source", "").strip() or "csv_import"

                    # Use 표준분류 as kb_id to preserve all records per standard
                    # e.g., "HBU_전사표준", "GS리테일 전사표준" → separate kb_id
                    effective_kb_id = source_val if source_val != "csv_import" else row.get("kb_id", kb_id)

                    term_data: dict[str, Any] = {
                        "id": str(uuid.uuid4()),
                        "kb_id": effective_kb_id,
                        "term": term,
                        "term_ko": row.get("term_ko", "") or "",
                        "definition": row.get("definition", "") or "",
                        "synonyms": synonyms,
                        "abbreviations": abbreviations,
                        "source": source_val,
                        "status": status,
                        "term_type": final_term_type,
                        "scope": scope,
                        "physical_meaning": row.get("physical_meaning", "") or "",
                        "composition_info": row.get("composition_info", "") or "",
                        "domain_name": row.get("domain_name", "") or "",
                    }
                    batch.append(term_data)

                    if len(batch) >= BATCH_SIZE:
                        try:
                            inserted = await repo.save_batch(batch)
                            total_imported += inserted
                        except Exception as e:
                            all_errors.append(f"{fname} batch ending row {row_num}: {e}")
                        batch = []

                except Exception as e:
                    all_errors.append(f"{fname} Row {row_num}: {e}")

            # Flush remaining batch
            if batch:
                try:
                    inserted = await repo.save_batch(batch)
                    total_imported += inserted
                except Exception as e:
                    all_errors.append(f"{fname} final batch: {e}")

        except Exception as e:
            all_errors.append(f"{fname}: {e}")

    # P2-8: Cache invalidation after glossary import
    search_cache = state.get("search_cache")
    if search_cache:
        try:
            await search_cache.clear()
        except Exception as e:
            logger.warning("Failed to clear search cache after glossary import: %s", e)

    return {
        "success": total_imported > 0,
        "imported": total_imported,
        "skipped": total_skipped,
        "files_processed": len(upload_files),
        "auto_detected_words": word_count,
        "auto_detected_terms": term_count,
        "errors": all_errors[:20],
    }


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/glossary/by-type/{term_type}
# ---------------------------------------------------------------------------
@router.delete("/by-type/{term_type}")
async def delete_glossary_by_type(
    term_type: str,
    kb_id: str = Query(default="global-standard"),
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
@router.post("/add-synonym")
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
@router.get("/{term_id}/synonyms")
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
    raise HTTPException(status_code=503, detail="No DB connection")


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/glossary/{term_id}/synonyms/{synonym}
# ---------------------------------------------------------------------------
@router.delete("/{term_id}/synonyms/{synonym}")
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
    raise HTTPException(status_code=503, detail="No DB connection")


# (discovered-synonyms GET moved above /{term_id} to avoid path capture)


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/discovered-synonyms/approve
# ---------------------------------------------------------------------------
@router.post("/discovered-synonyms/approve")
async def approve_discovered_synonyms(body: dict[str, Any]):
    """Approve one or more discovered synonym candidates.

    Approving means: add the synonym to the base term's synonyms list,
    then delete (or mark approved) the discovered synonym record.
    """
    state = _get_state()
    repo = state.get("glossary_repo")
    if not repo:
        raise HTTPException(status_code=503, detail="No DB connection")

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
@router.post("/discovered-synonyms/reject")
async def reject_discovered_synonyms(body: dict[str, Any]):
    """Reject one or more discovered synonym candidates."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if not repo:
        raise HTTPException(status_code=503, detail="No DB connection")

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
@router.post("/similarity-check")
async def check_pending_similarity(
    threshold: float = Query(default=0.7),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=500, ge=1, le=1000),
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
@router.post("/similarity-cleanup")
async def cleanup_pending_by_similarity(
    threshold: float = Query(default=0.7),
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
