"""Admin API endpoints — facade.

Graph routes are in ``_admin_graph.py``, helper functions in ``admin_helpers.py``.
This module keeps Qdrant and config-weight routes plus facade re-exports.
"""

from __future__ import annotations

import asyncio  # noqa: F401 — re-export for test patchability
import json  # noqa: F401 — re-export for backward compat
import logging
import re  # noqa: F401 — re-export for backward compat
from typing import Annotated, Any  # noqa: F401

from fastapi import APIRouter, HTTPException

from fastapi import Query  # noqa: F401 — re-export for backward compat

from src.api.app import _get_state
from src.config import get_settings  # noqa: F401 — re-export for backward compat
from src.config.weights import weights

# Re-export graph router so route_discovery picks up all endpoints
from src.api.routes._admin_graph import router as _graph_router  # noqa: F401

# Re-export helpers for backward compatibility (tests import from admin module)
from src.api.routes.admin_helpers import (  # noqa: F401
    AI_CLASSIFY_PROMPT,
    _GRAPH_INTEGRITY_FAILED,
    _KOREAN_NAME_RE,
    _VALID_LABELS,
    _apply_ai_classifications,
    _apply_single_classification,
    _classify_batch,
    _fetch_ai_classify_candidates,
    _parse_llm_json_response,
    _resolve_llm_client,
)

# Re-export graph route functions for backward compatibility
from src.api.routes._admin_graph import (  # noqa: F401
    graph_stats,
    graph_search,
    find_experts,
    graph_expand,
    graph_integrity_check,
    graph_path,
    graph_communities,
    graph_integrity,
    run_graph_integrity_check,
    graph_impact,
    graph_health,
    graph_timeline,
    graph_cleanup,
    graph_cleanup_analyze,
    graph_ai_classify,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])

# Include graph sub-router routes into this router
for route in _graph_router.routes:
    router.routes.append(route)


# ============================================================================
# Qdrant Collections
# ============================================================================

@router.get("/qdrant/collections")
async def list_collections() -> dict:
    """List Qdrant collections."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    if not collections:
        return {"collections": []}

    try:
        names = await collections.get_existing_collection_names()
        return {"collections": names}
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        return {"collections": [], "error": str(e)}


@router.get("/qdrant/collection/{name}/stats", responses={503: {"description": "Store not initialized"}, 500: {"description": "Internal error"}})  # noqa: E501
async def collection_stats(name: str) -> dict:
    """Get collection statistics."""
    state = _get_state()
    store = state.get("qdrant_store")
    if not store:
        raise HTTPException(status_code=503, detail="Store not initialized")

    try:
        count = await store.count(name)
        return {"collection": name, "point_count": count}
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Config Weights - Hot Reload
# ============================================================================

@router.post("/config/weights")
async def get_config_weights() -> dict:
    """Return current config weights."""
    return weights.to_dict()


@router.put("/config/weights", responses={400: {"description": "Empty body or no valid weight fields matched"}})
async def update_config_weights(body: dict[str, Any]) -> dict:
    """Update specific weight values (partial update).

    Accepts either flat keys ``{"section.field": value}``
    or nested ``{"section": {"field": value}}``.
    """
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")

    applied = weights.update_from_dict(body)
    if not applied:
        raise HTTPException(
            status_code=400,
            detail="No valid weight fields matched. Use \'section.field\' or {\'section\': {\'field\': value}}.",
        )
    logger.info("Config weights updated: %s", applied)
    return {"applied": applied, "current": weights.to_dict()}


@router.post("/config/weights/reset")
async def reset_config_weights() -> dict:
    """Reset all config weights to their defaults."""
    weights.reset()
    logger.info("Config weights reset to defaults")
    return {"status": "reset", "current": weights.to_dict()}
