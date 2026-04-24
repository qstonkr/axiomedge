"""Admin graph cleanup + AI classification routes — extracted from _admin_graph.py.

Contains: graph cleanup, cleanup analyze, AI classify endpoints.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from types import ModuleType

from fastapi import APIRouter

from src.stores.neo4j.errors import NEO4J_FAILURE


def _get_admin() -> ModuleType:
    """Late-bound accessor to parent admin module — avoids circular import."""
    import src.api.routes.admin as _admin
    return _admin


def _get_state() -> Any:  # AppState (dict-compatible)
    """Late-bound accessor through parent admin module for test patchability."""
    return _get_admin()._get_state()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


# ============================================================================
# Graph - Cleanup
# ============================================================================

@router.post("/graph/cleanup")
async def graph_cleanup(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run graph quality cleanup: remove placeholders, reclassify mismatches, etc.

    Body (all optional):
        apply (bool): False = dry run (default), True = apply fixes
        kb_id (str): Filter to a single KB
    """
    state = _get_state()
    graph = state.get("graph_repo")

    if not graph:
        return {
            "success": False,
            "error": "Graph repository not available",
            "tasks": [],
            "total_found": 0,
            "total_fixed": 0,
        }

    body = body or {}
    apply = body.get("apply", False)
    kb_id = body.get("kb_id")

    try:
        from scripts.graphrag.graph_cleanup import run_cleanup

        results = await asyncio.to_thread(run_cleanup, apply=apply, kb_id=kb_id)

        total_found = sum(r.get("found", 0) for r in results)
        total_fixed = sum(r.get("fixed", 0) for r in results)

        return {
            "success": True,
            "mode": "apply" if apply else "dry_run",
            "kb_id": kb_id,
            "tasks": results,
            "total_found": total_found,
            "total_fixed": total_fixed,
        }
    except (*NEO4J_FAILURE, ImportError) as e:
        logger.warning("Graph cleanup failed: %s", e)
        return {
            "success": False,
            "error": str(e),
            "tasks": [],
            "total_found": 0,
            "total_fixed": 0,
        }


@router.post("/graph/cleanup/analyze")
async def graph_cleanup_analyze(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Analyze graph quality issues without applying fixes (always dry run)."""

    state = _get_state()
    graph = state.get("graph_repo")

    if not graph:
        return {
            "success": False,
            "error": "Graph repository not available",
            "tasks": [],
            "total_found": 0,
            "total_fixed": 0,
        }

    kb_id = (body or {}).get("kb_id")

    try:
        from scripts.graphrag.graph_cleanup import run_cleanup

        results = await asyncio.to_thread(run_cleanup, apply=False, kb_id=kb_id)

        total_found = sum(r.get("found", 0) for r in results)

        return {
            "success": True,
            "mode": "dry_run",
            "kb_id": kb_id,
            "tasks": results,
            "total_found": total_found,
            "total_fixed": 0,
        }
    except (*NEO4J_FAILURE, ImportError) as e:
        logger.warning("Graph cleanup analysis failed: %s", e)
        return {
            "success": False,
            "error": str(e),
            "tasks": [],
            "total_found": 0,
            "total_fixed": 0,
        }


# ============================================================================
# Graph - AI Classification (LLM-based entity reclassification)
# ============================================================================

@router.post("/graph/cleanup/ai-classify")
async def graph_ai_classify(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """LLM-based entity reclassification using SageMaker EXAONE.

    Body (all optional):
        limit (int): Max nodes to process (default 200)
        apply (bool): False = dry run (default), True = apply reclassification
        kb_id (str): Filter to a single KB
    """
    _admin = _get_admin()
    llm = _admin._resolve_llm_client(_get_state())
    if not llm:
        return {
            "success": False,
            "error": "LLM client not available. Set USE_SAGEMAKER_LLM=true or start Ollama.",
            "candidates": 0,
            "classifications": [],
            "stats": {},
        }

    body = body or {}
    limit = body.get("limit", 200)
    if limit != 0:
        limit = min(max(limit, 10), 10000)
    apply = body.get("apply", False)
    kb_id = body.get("kb_id")

    try:
        candidates = await asyncio.to_thread(
            _admin._fetch_ai_classify_candidates, kb_id, limit,
        )
        if not candidates:
            return {
                "success": True,
                "mode": "apply" if apply else "dry_run",
                "candidates": 0,
                "classifications": [],
                "stats": {"relabeled": 0, "deleted": 0, "skipped": 0},
            }

        batch_size = 30
        all_classifications: list[dict[str, Any]] = []
        for i in range(0, len(candidates), batch_size):
            try:
                batch_result = await _admin._classify_batch(llm, candidates[i : i + batch_size])
                all_classifications.extend(batch_result)
            except (*NEO4J_FAILURE, ImportError) as e:
                logger.warning("AI classify LLM batch %d failed: %s", i // batch_size, e)

        stats: dict[str, int] = {"relabeled": 0, "deleted": 0, "skipped": 0, "errors": 0}
        if apply and all_classifications:
            stats = await asyncio.to_thread(
                _admin._apply_ai_classifications, all_classifications,
            )

        return {
            "success": True,
            "mode": "apply" if apply else "dry_run",
            "candidates": len(candidates),
            "classifications": [
                {
                    "name": c["name"],
                    "current_label": c["current_label"],
                    "new_type": c["type"],
                    "reason": c["reason"],
                    "kb_id": c.get("kb_id"),
                }
                for c in all_classifications
            ],
            "stats": stats,
        }
    except (*NEO4J_FAILURE, ImportError) as e:
        logger.warning("AI classify failed: %s", e)
        return {
            "success": False,
            "error": str(e),
            "candidates": 0,
            "classifications": [],
            "stats": {},
        }
