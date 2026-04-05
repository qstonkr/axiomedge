"""Glossary route helpers — validation, synonym logic, and similarity computation.

Extracted from glossary.py to keep route handlers thin.
All public names are re-exported from glossary.py for backward compatibility.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_TERM_NOT_FOUND = "Term not found"


async def _check_not_global_standard(repo: Any, term_id: str) -> dict[str, Any]:
    """Check term exists and is NOT a global standard (read-only).

    Global standards (scope='global', source from CSV import) are read-only.
    They can only be updated via CSV re-import.
    """
    existing = await repo.get_by_id(term_id)
    if not existing:
        raise HTTPException(status_code=404, detail=_TERM_NOT_FOUND)
    if existing.get("scope") == "global" and existing.get("source") not in ("manual", "auto_discovered"):
        raise HTTPException(
            status_code=403,
            detail="글로벌 표준 용어/단어는 수정/삭제할 수 없습니다. CSV 재임포트로만 갱신 가능합니다."
        )
    return existing


async def _approve_single_synonym(repo: Any, syn_id: str, errors: list[str]) -> bool:
    """Approve a single synonym record. Returns True if approved."""
    syn_record = await repo.get_by_id(syn_id)
    if not syn_record:
        errors.append(f"{syn_id}: not found")
        return False
    base_term_id = syn_record.get("related_terms", [None])[0] if syn_record.get("related_terms") else None
    synonym_text = syn_record.get("term", "")
    if base_term_id:
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
    await repo.save({
        "id": syn_id,
        "kb_id": syn_record["kb_id"],
        "term": syn_record["term"],
        "status": "approved",
    })
    return True


async def compute_similarity_distribution(
    state: Any,
    exact_match_threshold: float,
) -> dict[str, Any]:
    """Compute similarity score distribution with RapidFuzz sampling.

    Heavy business logic extracted from the route handler.
    """
    import numpy as np

    repo = state.get("glossary_repo")
    if not repo:
        return {"distribution": [], "total_pairs": 0, "mean_similarity": 0.0, "sample_size": 0}

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
        max_offset = max(0, pending_total - SAMPLE_SIZE)
        rand_offset = random.randint(0, max_offset) if max_offset > 0 else 0
        sample_terms = await repo.list_by_kb(kb_id="all", status="pending", limit=SAMPLE_SIZE, offset=rand_offset)
        sample_source = "pending"
    else:
        max_offset = max(0, approved_total - SAMPLE_SIZE)
        rand_offset = random.randint(0, max_offset) if max_offset > 0 else 0
        sample_terms = await repo.list_by_kb(kb_id="all", status="approved", limit=SAMPLE_SIZE, offset=rand_offset)
        sample_source = "approved"

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

    rf_scores: list[float] = []
    jac_scores: list[float] = []

    # Build UNIQUE standard terms (deduplicated by lower(term))
    seen_terms: dict[str, dict] = {}
    for t in standard_terms:
        term_lower = t.get("term", "").lower()
        if term_lower and term_lower not in seen_terms:
            seen_terms[term_lower] = t
    unique_standard = list(seen_terms.values())
    unique_standard_names = [t["term"] for t in unique_standard]

    # Build term->definition map for definition-based comparison
    std_definitions: dict[str, str] = {}
    for t in unique_standard:
        defn = (t.get("definition") or t.get("term_ko") or "").strip()
        if defn:
            std_definitions[t["term"]] = defn

    # Build sample with unique terms only
    sample_seen: set[str] = set()
    unique_sample: list[dict] = []
    for t in sample_terms:
        term_lower = t.get("term", "").lower()
        if term_lower and term_lower not in sample_seen:
            sample_seen.add(term_lower)
            unique_sample.append(t)
    sample_terms = unique_sample

    # Exclude sample terms from comparison set
    comparison_names = [n for n in unique_standard_names if n.lower() not in sample_seen]
    comparison_definitions = [std_definitions.get(n, "") for n in comparison_names]
    comp_with_def = [(n, d) for n, d in zip(comparison_names, comparison_definitions) if d]

    if not comparison_names:
        comparison_names = unique_standard_names

    def_scores: list[float] = []

    for term_data in sample_terms[:SAMPLE_SIZE]:
        query = term_data.get("term", "")
        if not query:
            continue

        # 1) RapidFuzz term-name similarity (exclude exact self)
        rf_result = process.extractOne(query, comparison_names, scorer=fuzz.WRatio)
        if rf_result:
            score = rf_result[1] / 100.0
            if score >= exact_match_threshold and rf_result[0].lower() == query.lower():
                results = process.extract(query, comparison_names, scorer=fuzz.WRatio, limit=2)
                if len(results) > 1:
                    score = results[1][1] / 100.0
            rf_scores.append(score)

        # 2) Jaccard n-gram (trigram)
        q_ngrams = {query[i:i+3] for i in range(max(1, len(query)-2))}
        best_jac = 0.0
        for std in comparison_names[:1000]:
            if std.lower() == query.lower():
                continue
            s_ngrams = {std[i:i+3] for i in range(max(1, len(std)-2))}
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
                if def_score >= exact_match_threshold:
                    def_results = process.extract(query_def, comp_def_texts, scorer=fuzz.WRatio, limit=2)
                    if len(def_results) > 1:
                        def_score = def_results[1][1] / 100.0
                def_scores.append(def_score)
            else:
                def_scores.append(0.0)
        else:
            def_scores.append(0.0)

    # 4) Dense cosine similarity via embedding
    dense_scores: list[float] = []
    embedder = state.get("embedder")
    if embedder:
        try:
            import asyncio as _asyncio

            sample_texts = [t.get("term", "") for t in sample_terms[:SAMPLE_SIZE] if t.get("term")]
            comp_texts = comparison_names[:1000]

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

                s_norms = np.linalg.norm(s_matrix, axis=1, keepdims=True)
                c_norms = np.linalg.norm(c_matrix, axis=1, keepdims=True)
                s_matrix = s_matrix / np.clip(s_norms, 1e-12, None)
                c_matrix = c_matrix / np.clip(c_norms, 1e-12, None)

                sim_matrix = s_matrix @ c_matrix.T

                for i, term_data in enumerate(sample_terms[:len(s_dense)]):
                    query_lower = term_data.get("term", "").lower()
                    row = sim_matrix[i]
                    for j, cn in enumerate(comp_texts[:len(c_dense)]):
                        if cn.lower() == query_lower:
                            row[j] = -1.0
                    best = float(np.max(row))
                    dense_scores.append(max(0.0, best))
        except Exception as e:
            logger.warning("Dense similarity computation failed: %s", e)

    # Compute stats
    def _stats(scores: list[float]) -> dict[str, Any]:
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
