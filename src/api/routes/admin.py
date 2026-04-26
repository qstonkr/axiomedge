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

from fastapi import APIRouter, HTTPException, Request

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
# Operations Dashboard Summary (B-2)
# ============================================================================

@router.get("/dashboard/summary")
async def admin_dashboard_summary() -> dict:
    """Admin 운영 대시보드용 요약 카운터.

    Streamlit dashboard.py 의 6 카드 지표를 한 번에 묶어 반환:
      - active_kbs: 활성 KB 수
      - total_documents: 모든 KB 의 document_count 합
      - total_chunks: 모든 KB 의 chunk_count 합
      - feedback_pending: pending feedback 수
      - error_reports_pending: pending error report 수
      - search_history_24h: 최근 24h 검색 수

    각 항목은 best-effort — repo 접근 실패해도 나머지는 채워서 반환.
    실패한 카운터는 ``null`` 로 노출 (UI 가 "데이터 없음" 표시).
    """
    state = _get_state()
    out: dict[str, Any] = {
        "active_kbs": None,
        "total_documents": None,
        "total_chunks": None,
        "feedback_pending": None,
        "error_reports_pending": None,
        "search_history_24h": None,
        "errors": [],
    }

    # KB / docs / chunks — kb_registry 의 list_all + 각 KB 의 document_count
    kb_registry = state.get("kb_registry")
    if kb_registry is not None:
        try:
            kbs = await kb_registry.list_all()
            active = [k for k in kbs if k.get("status") == "active"]
            out["active_kbs"] = len(active)
            out["total_documents"] = sum(
                int(k.get("document_count") or 0) for k in active
            )
            out["total_chunks"] = sum(
                int(k.get("chunk_count") or 0) for k in active
            )
        except Exception as e:  # noqa: BLE001 — best effort
            out["errors"].append(f"kb_registry: {type(e).__name__}: {e}")

    # Feedback pending — feedback_repo
    feedback_repo = state.get("feedback_repo")
    if feedback_repo is not None:
        try:
            count_fn = getattr(feedback_repo, "count", None)
            if count_fn:
                out["feedback_pending"] = await count_fn(status="pending")
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"feedback_repo: {type(e).__name__}: {e}")

    # Error reports pending — error_report_repo
    error_repo = state.get("error_report_repo")
    if error_repo is not None:
        try:
            list_fn = getattr(error_repo, "list_recent", None) or getattr(
                error_repo, "list_all", None,
            )
            if list_fn:
                rows = await list_fn(status="pending", limit=500)
                # rows 가 list 또는 dict {items} 둘 다 가능
                if isinstance(rows, dict):
                    rows = rows.get("items") or rows.get("reports") or []
                out["error_reports_pending"] = len(rows or [])
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"error_report_repo: {type(e).__name__}: {e}")

    # Search history 24h — usage_log_repo (state key is "usage_log_repo",
    # backed by UsageLogRepository which exposes get_analytics(days=...).
    # 24h ≒ days=1 — total_searches in the last day.)
    usage_log_repo = state.get("usage_log_repo")
    if usage_log_repo is not None:
        try:
            get_analytics = getattr(usage_log_repo, "get_analytics", None)
            if get_analytics:
                analytics = await get_analytics(days=1)
                out["search_history_24h"] = int(analytics.get("total_searches") or 0)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"usage_log_repo: {type(e).__name__}: {e}")

    return out


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

def _json_safe(value: Any) -> Any:
    """``weights.to_dict()`` 에는 ``resilience.retry_on`` 같은 exception class
    tuple 이 들어 있어 그대로 jsonable_encoder 에 넘기면 500. 직렬화 못 하는
    값은 dotted-path 문자열 (또는 type repr) 로 치환."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    # fallback — repr 로 노출 (운영자가 디버깅 가능)
    return repr(value)


@router.post("/config/weights")
async def get_config_weights() -> dict:
    """Return current config weights (JSON-safe)."""
    return _json_safe(weights.to_dict())


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


# =============================================================================
# P0-W1 — Admin endpoints for Streamlit pages (PR-10/C3/C4)
# =============================================================================


@router.get(
    "/feature-flags",
    responses={503: {"description": "feature_flag_repo not initialized"}},
)
async def list_feature_flags() -> list[dict]:
    """List all feature flag rows (admin UI listing)."""
    state = _get_state()
    repo = state.get("feature_flag_repo")
    if repo is None:
        raise HTTPException(503, "feature_flag_repo not initialized")
    return await repo.list_all()


@router.post(
    "/feature-flags",
    responses={503: {"description": "feature_flag_repo not initialized"}},
)
async def upsert_feature_flag(
    body: dict[str, Any], request: Request,
) -> dict:
    """Upsert a feature flag — admin UI toggle save.

    Body: ``{name, scope, enabled, payload}``. Triggers Redis pub/sub
    invalidation if redis is wired (P1-6) + writes ``feature_flag.update``
    audit row (S6).
    """
    state = _get_state()
    repo = state.get("feature_flag_repo")
    if repo is None:
        raise HTTPException(503, "feature_flag_repo not initialized")
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    scope = (body.get("scope") or "_global").strip()
    enabled = bool(body.get("enabled", False))
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        raise HTTPException(400, "payload must be a JSON object")

    redis = None
    try:
        from src.jobs.queue import get_pool
        redis = await get_pool()
    except (ImportError, RuntimeError, OSError, AttributeError):
        redis = None

    # S6 — actor 자동 추출 + audit 기록 세팅 (middleware 가 후처리).
    actor = _resolve_actor_from_request(request)
    request.state.audit = {
        "event_type": "feature_flag.update",
        "knowledge_id": "_global",
        "actor": actor,
        "details": {
            "name": name, "scope": scope, "enabled": enabled,
            "payload_keys": sorted(list(payload.keys())),
        },
    }
    ok = await repo.upsert(
        name=name, scope=scope, enabled=enabled, payload=payload,
        updated_by=actor, redis=redis,
    )
    if not ok:
        raise HTTPException(500, "upsert failed (see server log)")
    return {"name": name, "scope": scope, "enabled": enabled}


@router.get(
    "/audit-logs",
    responses={503: {"description": "audit_log_repo not initialized"}},
)
async def list_audit_logs(
    knowledge_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Recent audit log rows.

    P0-W4: ``event_type`` 가 ``"unauth."`` 같은 prefix 로 끝나면 LIKE
    쿼리로 자동 전환. exact match 의도면 더 구체적인 값을 보내면 됨.
    """
    state = _get_state()
    repo = state.get("audit_log_repo")
    if repo is None:
        raise HTTPException(503, "audit_log_repo not initialized")
    et_prefix = None
    et_exact = None
    if event_type:
        # Trailing "." → prefix mode
        if event_type.endswith("."):
            et_prefix = event_type
        else:
            et_exact = event_type
    return await repo.list_recent(
        knowledge_id=knowledge_id,
        event_type=et_exact,
        event_type_prefix=et_prefix,
        limit=limit,
    )


@router.get(
    "/pipeline/ingestion-runs",
    responses={503: {"description": "ingestion_run_repo not initialized"}},
)
async def list_ingestion_runs(limit: int = 50) -> list[dict]:
    """Recent ingestion run rows for the Ingestion Runs admin page (PR-10)."""
    state = _get_state()
    repo = state.get("ingestion_run_repo")
    if repo is None:
        raise HTTPException(503, "ingestion_run_repo not initialized")
    return await repo.list_recent(limit=max(1, min(limit, 500)))


@router.get(
    "/pipeline/runs/{run_id}/failures",
    responses={503: {"description": "ingestion_failure_repo not initialized"}},
)
async def list_run_failures(run_id: str, limit: int = 1000) -> list[dict]:
    """Failures recorded for a specific ingestion run."""
    state = _get_state()
    repo = state.get("ingestion_failure_repo")
    if repo is None:
        raise HTTPException(503, "ingestion_failure_repo not initialized")
    return await repo.list_by_run(run_id, limit=max(1, min(limit, 5000)))


def _resolve_actor_from_request(request: Request) -> str:
    """S6 — pull authenticated user id from auth middleware state.

    Mirrors ``src/api/middleware/audit_log.py:_resolve_actor`` to keep the
    contract aligned (``auth_user`` 우선, ``user`` legacy fallback).
    """
    for attr in ("auth_user", "user"):
        try:
            user = getattr(request.state, attr, None)
        except (AttributeError, RuntimeError):
            user = None
        if user is None:
            continue
        for key in ("sub", "user_id", "id", "email"):
            value = getattr(user, key, None)
            if value:
                return str(value)
    return "_system"
