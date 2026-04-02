"""Knowledge API Client -- Local version

Simplified httpx REST client pointing to local FastAPI server.
No OAuth2, no retry logic, no routing complexity.
All 145+ public methods preserved for dashboard page compatibility.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import streamlit as st

from services import config as cfg
from services.logging_config import get_logger
from services.validators import sanitize_input, validate_page_params

logger = get_logger(__name__)

PUBLISH_EXECUTE_TIMEOUT_SECONDS = 180


# ---------------------------------------------------------------------------
# HTTP helpers (simplified for local)
# ---------------------------------------------------------------------------

def _client(*, timeout: int | None = None) -> httpx.Client:
    """Synchronous httpx client for local FastAPI server."""
    return httpx.Client(
        base_url=cfg.DASHBOARD_API_URL,
        headers={"Content-Type": "application/json"},
        timeout=timeout or cfg.API_TIMEOUT,
    )


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: int | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Core request helper - simple single-attempt for local."""
    t0 = time.monotonic()
    try:
        with _client(timeout=timeout) as client:
            resp = client.request(method, path, params=params, json=json_body)
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            resp.raise_for_status()
            logger.info("API %s %s -> %s (%.0fms)", method, path, resp.status_code, duration_ms)
            try:
                data = resp.json()
                if isinstance(data, list):
                    return {"items": data}
                return data
            except (ValueError, UnicodeDecodeError):
                return {"error": f"Non-JSON response ({resp.status_code})", "_api_failed": True}
    except httpx.HTTPStatusError as exc:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.warning("API %s %s -> %s (%.0fms)", method, path, exc.response.status_code, duration_ms)
        return {"error": str(exc), "_api_failed": True}
    except httpx.TimeoutException as exc:
        logger.warning("API %s %s -> timeout", method, path)
        return {"error": f"Timeout: {exc}", "_api_failed": True}
    except httpx.RequestError as exc:
        logger.warning("API %s %s -> connection error: %s", method, path, exc)
        return {"error": str(exc), "_api_failed": True}


def _get(path: str, **params: Any) -> dict[str, Any]:
    clean = {k: v for k, v in params.items() if v is not None and k != "use_agents"}
    return _request("GET", path, params=clean)


def _post(path: str, body: dict[str, Any] | None = None, *, retries: int | None = None,
          timeout: int | None = None, **_kwargs: Any) -> dict[str, Any]:
    return _request("POST", path, json_body=body if body is not None else {}, timeout=timeout)


def _put(path: str, body: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
    return _request("PUT", path, json_body=body if body is not None else {})


def _patch(path: str, body: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
    return _request("PATCH", path, json_body=body if body is not None else {})


def _delete(path: str, **_kwargs: Any) -> dict[str, Any]:
    """DELETE request. 204 No Content treated as success."""
    t0 = time.monotonic()
    try:
        with _client() as client:
            resp = client.request("DELETE", path)
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            resp.raise_for_status()
            logger.info("API DELETE %s -> %s (%.0fms)", path, resp.status_code, duration_ms)
            if resp.status_code == 204:
                return {"success": True}
            try:
                data = resp.json()
                if isinstance(data, list):
                    return {"items": data}
                return data
            except (ValueError, UnicodeDecodeError):
                return {"success": True}
    except httpx.HTTPStatusError as exc:
        return {"error": str(exc), "_api_failed": True}
    except httpx.RequestError as exc:
        return {"error": str(exc), "_api_failed": True}


# ---------------------------------------------------------------------------
# Streamlit cache helpers
# ---------------------------------------------------------------------------

def api_failed(result: dict | list | None) -> bool:
    """Check if an API call failed."""
    if not isinstance(result, dict):
        return False
    return bool(result.get("_api_failed"))


# ============================================================================
# KB Management
# ============================================================================

@st.cache_data(ttl=300)
def list_kbs(tier: str | None = None, status: str | None = None) -> dict:
    return _get("/api/v1/admin/kb", tier=tier, status=status)


@st.cache_data(ttl=300)
def get_kb(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/kb/{kb_id}")


def create_kb(body: dict) -> dict:
    return _post("/api/v1/admin/kb", body)


def update_kb(kb_id: str, body: dict) -> dict:
    return _put(f"/api/v1/admin/kb/{kb_id}", body)


def update_kb_publish_strategy(kb_id: str, publish_strategy: str) -> dict:
    current = get_kb(kb_id)
    if api_failed(current):
        return current
    settings = dict(current.get("settings") or {})
    normalized = (publish_strategy or "legacy").strip().lower()
    settings["publish_strategy"] = normalized
    return update_kb(kb_id, {"settings": settings})


def delete_kb(kb_id: str) -> dict:
    return _delete(f"/api/v1/admin/kb/{kb_id}")


@st.cache_data(ttl=300)
def get_kb_stats(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/kb/{kb_id}/stats")


@st.cache_data(ttl=300)
def get_kb_documents(kb_id: str, page: int = 1, page_size: int = 20) -> dict:
    page, page_size = validate_page_params(page, page_size)
    return _get(f"/api/v1/admin/kb/{kb_id}/documents", page=page, page_size=page_size)


def add_kb_member(kb_id: str, body: dict) -> dict:
    return _post(f"/api/v1/admin/kb/{kb_id}/members", body)


def remove_kb_member(kb_id: str, member_id: str) -> dict:
    return _delete(f"/api/v1/admin/kb/{kb_id}/members/{member_id}")


@st.cache_data(ttl=300)
def get_kb_members(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/kb/{kb_id}/members")


# ============================================================================
# KB Management - Categories & Lifecycle
# ============================================================================

@st.cache_data(ttl=300)
def get_kb_categories(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/kb/{kb_id}/categories")


@st.cache_data(ttl=300)
def list_l1_categories() -> dict:
    return _get("/api/v1/admin/categories")


@st.cache_data(ttl=60)
def get_l1_stats() -> dict:
    return _get("/api/v1/admin/categories/stats")


@st.cache_data(ttl=300)
def get_kb_lifecycle(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/kb/{kb_id}/lifecycle")


def get_kb_aggregation() -> dict:
    """KB stats with conditional caching."""
    cache_key = "_kb_aggregation_cache"
    cached = st.session_state.get(cache_key)
    if cached is not None:
        age = time.monotonic() - cached["ts"]
        if age < 300:
            return cached["data"]

    result = _get("/api/v1/admin/kb/stats")

    if not api_failed(result):
        has_data = result.get("total_documents", 0) > 0 or result.get("total_chunks", 0) > 0
        if has_data:
            st.session_state[cache_key] = {"data": result, "ts": time.monotonic()}

    return result


@st.cache_data(ttl=300)
def get_kb_coverage_gaps(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/kb/{kb_id}/coverage-gaps")


# ============================================================================
# Glossary
# ============================================================================

@st.cache_data(ttl=300)
def list_glossary_terms(kb_id: str = "all", status: str | None = None, scope: str | None = None,
                        term_type: str | None = None, page: int = 1, page_size: int = 100) -> dict:
    page, page_size = validate_page_params(page, page_size, max_page_size=500)
    return _get("/api/v1/admin/glossary", kb_id=kb_id, status=status, scope=scope,
                term_type=term_type, page=page, page_size=page_size)


@st.cache_data(ttl=300)
def get_glossary_term(term_id: str) -> dict:
    return _get(f"/api/v1/admin/glossary/{term_id}")


def create_glossary_term(body: dict) -> dict:
    return _post("/api/v1/admin/glossary", body)


def update_glossary_term(term_id: str, body: dict) -> dict:
    return _patch(f"/api/v1/admin/glossary/{term_id}", body)


def approve_glossary_term(term_id: str, approved_by: str) -> dict:
    return _post(f"/api/v1/admin/glossary/{term_id}/approve", {"approved_by": approved_by})


def reject_glossary_term(term_id: str, rejected_by: str, reason: str = "") -> dict:
    return _post(f"/api/v1/admin/glossary/{term_id}/reject", {"approved_by": rejected_by, "reason": reason})


def delete_glossary_term(term_id: str) -> dict:
    return _delete(f"/api/v1/admin/glossary/{term_id}")


def promote_glossary_term_to_global(term_id: str) -> dict:
    return _post(f"/api/v1/admin/glossary/{term_id}/promote-global")


def import_glossary_csv(file_bytes: bytes, filename: str, encoding: str = "utf-8", term_type: str = "term") -> dict:
    """POST /api/v1/admin/glossary/import-csv"""
    path = "/api/v1/admin/glossary/import-csv"
    try:
        t0 = time.monotonic()
        with httpx.Client(base_url=cfg.DASHBOARD_API_URL, timeout=300) as client:
            resp = client.post(
                path,
                params={"encoding": encoding, "term_type": term_type},
                files={"file": (filename, file_bytes, "text/csv")},
            )
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            resp.raise_for_status()
            logger.info("import_glossary_csv -> %s (%.0fms)", resp.status_code, duration_ms)
            return resp.json()
    except Exception as exc:
        logger.error("import_glossary_csv failed: %s", exc)
        return {"error": str(exc), "_api_failed": True}


def delete_glossary_by_type(term_type: str, kb_id: str = "global-standard") -> dict:
    return _request("DELETE", f"/api/v1/admin/glossary/by-type/{term_type}?kb_id={kb_id}", timeout=60)


def add_synonym_to_standard(standard_term_id: str, synonym: str, delete_pending_id: str | None = None) -> dict:
    body: dict = {"standard_term_id": standard_term_id, "synonym": synonym}
    if delete_pending_id:
        body["delete_pending_id"] = delete_pending_id
    return _post("/api/v1/admin/glossary/add-synonym", body)


def check_pending_similarity(threshold: float = 0.7, page: int = 1, page_size: int = 50) -> dict:
    return _request("POST", f"/api/v1/admin/glossary/similarity-check?threshold={threshold}&page={page}&page_size={page_size}",
                    json_body={}, timeout=120)


def cleanup_pending_by_similarity(threshold: float = 0.7, term_ids: list[str] | None = None) -> dict:
    return _request("POST", f"/api/v1/admin/glossary/similarity-cleanup?threshold={threshold}",
                    json_body={"term_ids": term_ids or []}, timeout=120)


def get_similarity_distribution() -> dict:
    return _get("/api/v1/admin/glossary/similarity-distribution")


def list_synonyms(term_id: str) -> dict:
    """GET /api/v1/admin/glossary/{term_id}/synonyms"""
    return _get(f"/api/v1/admin/glossary/{term_id}/synonyms")


def remove_synonym(term_id: str, synonym: str) -> dict:
    """DELETE /api/v1/admin/glossary/{term_id}/synonyms/{synonym}"""
    import urllib.parse
    encoded = urllib.parse.quote(synonym, safe="")
    return _request("DELETE", f"/api/v1/admin/glossary/{term_id}/synonyms/{encoded}")


def list_discovered_synonyms(status: str = "pending", page: int = 1, page_size: int = 50) -> dict:
    """GET /api/v1/admin/glossary/discovered-synonyms"""
    return _get("/api/v1/admin/glossary/discovered-synonyms", status=status, page=page, page_size=page_size)


def approve_discovered_synonyms(synonym_ids: list[str]) -> dict:
    """POST /api/v1/admin/glossary/discovered-synonyms/approve"""
    return _post("/api/v1/admin/glossary/discovered-synonyms/approve", {"synonym_ids": synonym_ids})


def reject_discovered_synonyms(synonym_ids: list[str]) -> dict:
    """POST /api/v1/admin/glossary/discovered-synonyms/reject"""
    return _post("/api/v1/admin/glossary/discovered-synonyms/reject", {"synonym_ids": synonym_ids})


# ============================================================================
# Ownership
# ============================================================================

@st.cache_data(ttl=300)
def list_document_owners(kb_id: str, status: str | None = None) -> dict:
    return _get("/api/v1/admin/ownership/documents", kb_id=kb_id, status=status)


@st.cache_data(ttl=300)
def get_document_owner(document_id: str, kb_id: str) -> dict:
    return _get(f"/api/v1/admin/ownership/documents/{document_id}", kb_id=kb_id)


def assign_document_owner(body: dict) -> dict:
    return _post("/api/v1/admin/ownership/documents", body)


def transfer_ownership(document_id: str, body: dict) -> dict:
    return _post(f"/api/v1/admin/ownership/documents/{document_id}/transfer", body)


def verify_document_owner(document_id: str, body: dict) -> dict:
    return _post(f"/api/v1/admin/ownership/documents/{document_id}/verify", body)


@st.cache_data(ttl=300)
def get_stale_owners(kb_id: str, days_threshold: int = 90) -> dict:
    return _get("/api/v1/admin/ownership/stale", kb_id=kb_id, days_threshold=days_threshold)


@st.cache_data(ttl=300)
def get_owner_availability(owner_user_id: str) -> dict:
    return _get(f"/api/v1/admin/ownership/availability/{owner_user_id}")


def update_owner_availability(owner_user_id: str, body: dict) -> dict:
    return _put(f"/api/v1/admin/ownership/availability/{owner_user_id}", body)


@st.cache_data(ttl=300)
def list_topic_owners(kb_id: str) -> dict:
    return _get("/api/v1/admin/ownership/topics", kb_id=kb_id)


def assign_topic_owner(body: dict) -> dict:
    return _post("/api/v1/admin/ownership/topics", body)


@st.cache_data(ttl=300)
def get_owner_search(query: str, kb_id: str | None = None) -> dict:
    query = sanitize_input(query, max_length=200)
    return _get("/api/v1/knowledge/experts/search", query=query, kb_id=kb_id)


# ============================================================================
# Error Reports
# ============================================================================

@st.cache_data(ttl=60)
def list_error_reports(kb_id: str | None = None, status: str | None = None,
                       page: int = 1, page_size: int = 20) -> dict:
    page, page_size = validate_page_params(page, page_size)
    return _get("/api/v1/admin/error-reports", kb_id=kb_id, status=status, page=page, page_size=page_size)


@st.cache_data(ttl=60)
def get_error_report(report_id: str) -> dict:
    return _get(f"/api/v1/admin/error-reports/{report_id}")


def create_error_report(body: dict) -> dict:
    return _post("/api/v1/knowledge/report-error", body)


@st.cache_data(ttl=60)
def get_error_report_statistics(kb_id: str | None = None, days: int = 30) -> dict:
    return _get("/api/v1/admin/error-reports/statistics", kb_id=kb_id, days=days)


def resolve_error_report(report_id: str, body: dict) -> dict:
    return _post(f"/api/v1/admin/error-reports/{report_id}/resolve", body)


def reject_error_report(report_id: str, body: dict) -> dict:
    return _post(f"/api/v1/admin/error-reports/{report_id}/reject", body)


def escalate_error_report(report_id: str, body: dict) -> dict:
    return _post(f"/api/v1/admin/error-reports/{report_id}/escalate", body)


# ============================================================================
# Feedback
# ============================================================================

@st.cache_data(ttl=60)
def list_feedback(status: str | None = None, feedback_type: str | None = None,
                  page: int = 1, page_size: int = 20) -> dict:
    page, page_size = validate_page_params(page, page_size)
    return _get("/api/v1/admin/feedback/list", status=status, feedback_type=feedback_type,
                page=page, page_size=page_size)


def create_feedback(body: dict) -> dict:
    return _post("/api/v1/knowledge/feedback", body)


def update_feedback(feedback_id: str, body: dict) -> dict:
    feedback_id = sanitize_input(feedback_id, max_length=200)
    if not feedback_id:
        return {"error": "feedback_id must be a non-empty string", "_api_failed": True}
    return _patch(f"/api/v1/admin/feedback/{feedback_id}", body)


@st.cache_data(ttl=60)
def get_feedback_stats() -> dict:
    return _get("/api/v1/admin/feedback/stats")


@st.cache_data(ttl=60)
def get_feedback_workflow_stats() -> dict:
    return _get("/api/v1/admin/feedback/workflow-stats")


@st.cache_data(ttl=300)
def get_learning_artifacts(page: int = 1, page_size: int = 20) -> dict:
    page, page_size = validate_page_params(page, page_size)
    return _get("/api/v1/admin/kb/learning/low-confidence", page=page, page_size=page_size)


# ============================================================================
# Knowledge Ingestion
# ============================================================================

@st.cache_data(ttl=60)
def list_ingestion_runs(kb_id: str | None = None, status: str | None = None,
                        page: int = 1, page_size: int = 20) -> dict:
    page, page_size = validate_page_params(page, page_size)
    return _get("/api/v1/admin/knowledge/ingest/jobs", kb_id=kb_id, status=status,
                page=page, page_size=page_size)


@st.cache_data(ttl=60)
def get_ingestion_run(run_id: str) -> dict:
    return _get(f"/api/v1/admin/knowledge/ingest/status/{run_id}")


def trigger_ingestion(body: dict) -> dict:
    return _post("/api/v1/admin/knowledge/ingest", body)


def cancel_ingestion(run_id: str) -> dict:
    run_id = sanitize_input(run_id, max_length=200)
    if not run_id:
        return {"error": "run_id must be a non-empty string", "_api_failed": True}
    return _post(f"/api/v1/admin/knowledge/ingest/jobs/{run_id}/cancel")


@st.cache_data(ttl=60)
def get_ingestion_stats(kb_id: str | None = None) -> dict:
    return _get("/api/v1/admin/ingestion/stats", kb_id=kb_id)


@st.cache_data(ttl=300)
def list_ingestion_schedules() -> dict:
    return _get("/api/v1/admin/ingestion/schedules")


# ============================================================================
# Knowledge Traceability
# ============================================================================

@st.cache_data(ttl=300)
def get_document_provenance(doc_id: str) -> dict:
    return _get(f"/api/v1/admin/knowledge/{doc_id}/provenance")


@st.cache_data(ttl=300)
def get_document_lineage(doc_id: str) -> dict:
    return _get(f"/api/v1/admin/knowledge/{doc_id}/lineage")


@st.cache_data(ttl=300)
def get_document_versions(doc_id: str) -> dict:
    return _get(f"/api/v1/admin/knowledge/{doc_id}/versions")


# ============================================================================
# Data Sources
# ============================================================================

@st.cache_data(ttl=300)
def list_data_sources() -> dict:
    return _get("/api/v1/admin/data-sources")


@st.cache_data(ttl=300)
def get_data_source(source_id: str) -> dict:
    return _get(f"/api/v1/admin/data-sources/{source_id}")


def create_data_source(body: dict) -> dict:
    return _post("/api/v1/admin/data-sources", body)


def update_data_source(source_id: str, body: dict) -> dict:
    source_id = sanitize_input(source_id, max_length=200)
    if not source_id:
        return {"error": "source_id must be a non-empty string", "_api_failed": True}
    return _put(f"/api/v1/admin/data-sources/{source_id}", body)


def delete_data_source(source_id: str) -> dict:
    return _delete(f"/api/v1/admin/data-sources/{source_id}")


def trigger_data_source_sync(source_id: str, sync_mode: str = "resume") -> dict:
    return _post(f"/api/v1/admin/data-sources/{source_id}/trigger?sync_mode={sync_mode}")


@st.cache_data(ttl=60)
def get_data_source_status(source_id: str) -> dict:
    return _get(f"/api/v1/admin/data-sources/{source_id}/status")


def trigger_file_ingest(body: dict) -> dict:
    return _post("/api/v1/admin/data-sources/file-ingest", body)


def upload_and_ingest(
    file_bytes: bytes, filename: str, kb_id: str, kb_name: str | None = None,
    enable_vision: bool = False, create_new_kb: bool = False,
    tier: str | None = None, organization_id: str | None = None,
) -> dict:
    return upload_and_ingest_multi(
        files=[(filename, file_bytes)], kb_id=kb_id, kb_name=kb_name,
        enable_vision=enable_vision, create_new_kb=create_new_kb,
        tier=tier, organization_id=organization_id,
    )


def upload_and_ingest_multi(
    files: list[tuple[str, bytes]], kb_id: str, kb_name: str | None = None,
    enable_vision: bool = False, create_new_kb: bool = False,
    tier: str | None = None, organization_id: str | None = None,
) -> dict:
    path = "/api/v1/knowledge/file-upload-ingest"
    try:
        form_data: dict[str, str] = {
            "kb_id": kb_id,
            "enable_vision": str(enable_vision).lower(),
            "create_new_kb": str(create_new_kb).lower(),
        }
        if kb_name:
            form_data["kb_name"] = kb_name
        if tier:
            form_data["tier"] = tier
        if organization_id:
            form_data["organization_id"] = organization_id

        if len(files) == 1:
            upload_files: Any = {"file": (files[0][0], files[0][1])}
        else:
            upload_files = [("files", (name, data)) for name, data in files]

        t0 = time.monotonic()
        with httpx.Client(base_url=cfg.DASHBOARD_API_URL, timeout=120) as client:
            resp = client.post(path, data=form_data, files=upload_files)
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            resp.raise_for_status()
            logger.info("upload_and_ingest -> %s (%.0fms, %d files)", resp.status_code, duration_ms, len(files))
            return resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            return {"error": "KB ID already exists.", "_api_failed": True, "_conflict": True}
        return {"error": str(exc), "_api_failed": True}
    except Exception as exc:
        return {"error": str(exc), "_api_failed": True}


# ============================================================================
# ML Evaluation
# ============================================================================

def trigger_evaluation(body: dict) -> dict:
    return _post("/api/v1/admin/eval/trigger", body)


@st.cache_data(ttl=60)
def get_evaluation_status(eval_id: str) -> dict:
    return _get("/api/v1/admin/eval/status")


@st.cache_data(ttl=300)
def list_evaluation_history(page: int = 1, page_size: int = 20) -> dict:
    page, page_size = validate_page_params(page, page_size)
    return _get("/api/v1/admin/eval/history", page=page, page_size=page_size)


# ============================================================================
# Golden Set & Eval Results
# ============================================================================

@st.cache_data(ttl=30)
def list_golden_set(
    kb_id: str | None = None, status: str | None = None,
    page: int = 1, page_size: int = 50,
) -> dict:
    page, page_size = validate_page_params(page, page_size)
    return _get(
        "/api/v1/admin/golden-set",
        kb_id=kb_id, status=status, page=page, page_size=page_size,
    )


def update_golden_set_item(item_id: str, body: dict) -> dict:
    return _patch(f"/api/v1/admin/golden-set/{item_id}", body)


def delete_golden_set_item(item_id: str) -> dict:
    return _delete(f"/api/v1/admin/golden-set/{item_id}")


@st.cache_data(ttl=30)
def list_eval_results(
    eval_id: str | None = None, kb_id: str | None = None,
    page: int = 1, page_size: int = 50,
) -> dict:
    page, page_size = validate_page_params(page, page_size)
    return _get(
        "/api/v1/admin/eval-results",
        eval_id=eval_id, kb_id=kb_id, page=page, page_size=page_size,
    )


@st.cache_data(ttl=30)
def get_eval_results_summary() -> dict:
    return _get("/api/v1/admin/eval-results/summary")


# ============================================================================
# Hub Search
# ============================================================================

@st.cache_data(ttl=300)
def hub_search(query: str, kb_ids: list[str] | None = None, tier_filter: list[str] | None = None,
               top_k: int = 5, mode: str | None = None,
               group_id: str | None = None, group_name: str | None = None) -> dict:
    query = sanitize_input(query, max_length=500)
    body: dict[str, Any] = {"query": query, "top_k": min(max(1, top_k), 50)}
    kb_filter: dict[str, Any] = {}
    if kb_ids is not None:
        kb_filter["kb_ids"] = kb_ids
    if tier_filter is not None:
        kb_filter["tier"] = tier_filter
    if kb_filter:
        body["kb_filter"] = kb_filter
    if group_id:
        body["group_id"] = group_id
    if group_name:
        body["group_name"] = group_name
    if mode is not None:
        body["mode"] = mode
    return _post("/api/v1/search/hub", body)


@st.cache_data(ttl=300)
def get_searchable_kbs() -> dict:
    return _get("/api/v1/search/hub/kbs")


@st.cache_data(ttl=60, show_spinner=False)
def hub_search_answer(query: str, kb_ids: list[str] | None = None, mode: str | None = None,
                      group_name: str | None = None) -> dict:
    query = sanitize_input(query, max_length=500)
    body: dict[str, Any] = {"query": query, "top_k": 5, "include_answer": True}
    if kb_ids is not None:
        body["kb_filter"] = {"kb_ids": kb_ids}
    if mode is not None:
        body["mode"] = mode
    if group_name:
        body["group_name"] = group_name
    return _post("/api/v1/search/hub", body, timeout=cfg.API_SEARCH_TIMEOUT, retries=1)


# ============================================================================
# Knowledge RAG
# ============================================================================

def rag_query(query: str, kb_ids: list[str] | None = None, mode: str = "classic") -> dict:
    query = sanitize_input(query, max_length=500)
    body: dict[str, Any] = {"query": query, "mode": mode}
    if kb_ids is not None:
        body["kb_ids"] = kb_ids
    return _post("/api/v1/knowledge/ask", body)


@st.cache_data(ttl=300)
def get_rag_config() -> dict:
    return _get("/api/v1/knowledge/rag/config")


@st.cache_data(ttl=60)
def get_rag_stats() -> dict:
    return _get("/api/v1/knowledge/rag/stats")


# ============================================================================
# Intelligent RAG
# ============================================================================

@st.cache_data(ttl=60)
def get_intelligent_rag_cache_stats() -> dict:
    return _get("/api/v1/intelligent-rag/cache/stats")


def invalidate_rag_cache(pattern: str | None = None) -> dict:
    body = {"pattern": pattern} if pattern is not None else {}
    return _post("/api/v1/intelligent-rag/cache/invalidate", body)


def clear_rag_cache() -> dict:
    return _post("/api/v1/intelligent-rag/cache/clear")


@st.cache_data(ttl=60)
def get_intelligent_rag_metrics() -> dict:
    return _get("/api/v1/intelligent-rag/metrics")


@st.cache_data(ttl=300)
def get_rag_domain_config() -> dict:
    return _get("/api/v1/intelligent-rag/config/domains")


def update_rag_domain_config(body: dict) -> dict:
    return _put("/api/v1/intelligent-rag/config/domains", body)


@st.cache_data(ttl=60)
def get_intelligent_rag_health() -> dict:
    return _get("/api/v1/intelligent-rag/health")


def intelligent_rag_query(query: str, domain: str | None = None) -> dict:
    query = sanitize_input(query, max_length=500)
    body: dict[str, Any] = {"query": query}
    if domain is not None:
        body["domain"] = domain
    return _post("/api/v1/intelligent-rag/query", body)


@st.cache_data(ttl=300)
def get_intelligent_rag_adapters() -> dict:
    return _get("/api/v1/intelligent-rag/adapters")


# ============================================================================
# Pipeline
# ============================================================================

@st.cache_data(ttl=60)
def get_pipeline_status() -> dict:
    return _get("/api/v1/admin/pipeline/status")


@st.cache_data(ttl=60)
def get_pipeline_run_detail(run_id: str) -> dict:
    return _get(f"/api/v1/admin/pipeline/runs/{run_id}")


@st.cache_data(ttl=60)
def get_latest_experiment_run(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/pipeline/experiments/{kb_id}/latest")


def trigger_kb_sync(kb_id: str, *, mode: str = "canonical", source_type: str | None = None,
                    sync_source_name: str | None = None) -> dict:
    body: dict[str, Any] = {"mode": mode}
    if source_type:
        body["source_type"] = source_type
    if sync_source_name:
        body["sync_source_name"] = sync_source_name
    return _post(f"/api/v1/admin/kb/{kb_id}/sync", body, timeout=60)


def validate_kb_sync(kb_id: str, *, mode: str = "canonical", source_type: str | None = None,
                     sync_source_name: str | None = None) -> dict:
    body: dict[str, Any] = {"mode": mode}
    if source_type:
        body["source_type"] = source_type
    if sync_source_name:
        body["sync_source_name"] = sync_source_name
    return _post(f"/api/v1/admin/kb/{kb_id}/sync/validate", body, timeout=30)


def publish_experiment_dry_run(kb_id: str, run_id: str | None = None) -> dict:
    body: dict[str, Any] = {"kb_id": kb_id}
    if run_id:
        body["run_id"] = run_id
    return _post("/api/v1/admin/pipeline/publish/dry-run", body)


def publish_experiment_execute(kb_id: str, run_id: str | None = None) -> dict:
    body: dict[str, Any] = {"kb_id": kb_id}
    if run_id:
        body["run_id"] = run_id
    return _post("/api/v1/admin/pipeline/publish/execute", body, timeout=PUBLISH_EXECUTE_TIMEOUT_SECONDS)


@st.cache_data(ttl=60)
def get_pipeline_metrics() -> dict:
    return _get("/api/v1/admin/pipeline/metrics")


@st.cache_data(ttl=60)
def get_pipeline_gates_stats() -> dict:
    return _get("/api/v1/admin/pipeline/gates/stats")


@st.cache_data(ttl=60)
def get_pipeline_gate_blocked(gate_id: str) -> dict:
    return _get(f"/api/v1/admin/pipeline/gates/{gate_id}/blocked")


# ============================================================================
# Impact & Trust
# ============================================================================

@st.cache_data(ttl=300)
def get_kb_impact(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/kb/{kb_id}/impact")


@st.cache_data(ttl=300)
def get_kb_impact_rankings(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/kb/{kb_id}/impact/rankings")


@st.cache_data(ttl=300)
def get_kb_trust_scores(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/kb/{kb_id}/trust-scores")


@st.cache_data(ttl=300)
def get_kb_trust_score_distribution(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/kb/{kb_id}/trust-scores/distribution")


@st.cache_data(ttl=300)
def get_kb_freshness(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/kb/{kb_id}/freshness")


@st.cache_data(ttl=300)
def get_kb_value_tiers(kb_id: str) -> dict:
    return _get(f"/api/v1/admin/kb/{kb_id}/value-tiers")


# ============================================================================
# Verification
# ============================================================================

@st.cache_data(ttl=60)
def get_verification_pending(page: int = 1, page_size: int = 20) -> dict:
    page, page_size = validate_page_params(page, page_size)
    return _get("/api/v1/admin/verification/pending", page=page, page_size=page_size)


def submit_verification_vote(doc_id: str, body: dict) -> dict:
    return _post(f"/api/v1/admin/verification/{doc_id}/vote", body)


# ============================================================================
# Contributors
# ============================================================================

@st.cache_data(ttl=300)
def list_contributors(page: int = 1, page_size: int = 20) -> dict:
    page, page_size = validate_page_params(page, page_size)
    return _get("/api/v1/admin/contributors", page=page, page_size=page_size)


@st.cache_data(ttl=300)
def get_transparency_stats() -> dict:
    return _get("/api/v1/admin/transparency/stats")


# ============================================================================
# Search
# ============================================================================

@st.cache_data(ttl=60)
def get_search_history(page: int = 1, page_size: int = 50) -> dict:
    page, page_size = validate_page_params(page, page_size)
    return _get("/api/v1/admin/search/history", page=page, page_size=page_size)


@st.cache_data(ttl=60)
def get_search_analytics() -> dict:
    return _get("/api/v1/admin/search/analytics")


@st.cache_data(ttl=60)
def get_search_injection_stats() -> dict:
    return _get("/api/v1/admin/search/injection-stats")


@st.cache_data(ttl=60)
def get_agentic_rag_stats() -> dict:
    return _get("/api/v1/admin/search/agentic-rag-stats")


@st.cache_data(ttl=60)
def get_crag_stats() -> dict:
    return _get("/api/v1/admin/search/crag-stats")


@st.cache_data(ttl=60)
def get_search_adapter_stats() -> dict:
    return _get("/api/v1/admin/search/adapter-stats")


# ============================================================================
# Graph
# ============================================================================

@st.cache_data(ttl=300)
def get_graph_integrity() -> dict:
    return _get("/api/v1/admin/graph/integrity")


def run_graph_integrity_check() -> dict:
    return _post("/api/v1/admin/graph/integrity/run")


@st.cache_data(ttl=300)
def get_graph_stats() -> dict:
    return _get("/api/v1/admin/graph/stats")


@st.cache_data(ttl=300)
def get_graph_communities() -> dict:
    return _get("/api/v1/admin/graph/communities")


def graph_search(query: str, max_nodes: int = 50, max_hops: int = 2,
                 node_types: list[str] | None = None) -> dict:
    query = sanitize_input(query, max_length=500)
    max_nodes = max(1, min(max_nodes, 200))
    max_hops = max(1, min(max_hops, 5))
    body: dict[str, Any] = {"query": query, "max_nodes": max_nodes, "max_hops": max_hops}
    if node_types is not None:
        body["node_types"] = node_types
    return _post("/api/v1/admin/graph/search", body)


def graph_expand(node_id: str, max_neighbors: int = 30,
                 node_types: list[str] | None = None) -> dict:
    body: dict[str, Any] = {"node_id": node_id, "max_neighbors": max_neighbors}
    if node_types is not None:
        body["node_types"] = node_types
    return _post("/api/v1/admin/graph/expand", body)


def graph_experts(topic: str, limit: int = 10) -> dict:
    return _post("/api/v1/admin/graph/experts", {"topic": topic, "limit": limit})


def graph_path(from_node_id: str, to_node_id: str) -> dict:
    return _post("/api/v1/admin/graph/path", {"from_node_id": from_node_id, "to_node_id": to_node_id})


def graph_impact(node_id: str, max_hops: int = 2) -> dict:
    return _post("/api/v1/admin/graph/impact", {"node_id": node_id, "max_hops": max_hops})


@st.cache_data(ttl=120)
def graph_health() -> dict:
    return _get("/api/v1/admin/graph/health")


def graph_timeline(node_id: str) -> dict:
    return _post("/api/v1/admin/graph/timeline", {"node_id": node_id})


# ============================================================================
# Embedding & Cache
# ============================================================================

@st.cache_data(ttl=300)
def get_embedding_stats() -> dict:
    return _get("/api/v1/admin/embedding/stats")


@st.cache_data(ttl=60)
def get_cache_stats() -> dict:
    return _get("/api/v1/admin/cache/stats")


# ============================================================================
# Dedup
# ============================================================================

@st.cache_data(ttl=300)
def get_dedup_stats() -> dict:
    return _get("/api/v1/admin/dedup/stats")


@st.cache_data(ttl=300)
def get_dedup_conflicts(page: int = 1, page_size: int = 20) -> dict:
    page, page_size = validate_page_params(page, page_size)
    return _get("/api/v1/admin/dedup/conflicts", page=page, page_size=page_size)


def resolve_dedup_conflict(body: dict) -> dict:
    return _post("/api/v1/admin/dedup/resolve", body)


@st.cache_data(ttl=300)
def get_vectorstore_stats() -> dict:
    return _get("/api/v1/admin/vectorstore/stats")


# ============================================================================
# Version Management
# ============================================================================

@st.cache_data(ttl=300)
def get_document_version_list(doc_id: str) -> dict:
    return _get(f"/api/v1/admin/knowledge/{doc_id}/versions")


def rollback_document_version(doc_id: str, body: dict) -> dict:
    return _post(f"/api/v1/admin/documents/{doc_id}/rollback", body)


def approve_document_version(doc_id: str, body: dict) -> dict:
    return _post(f"/api/v1/admin/documents/{doc_id}/approve", body)


# ============================================================================
# Server Cache Management
# ============================================================================

def clear_search_cache() -> dict:
    return _post("/api/v1/admin/kb/search-cache/clear")


# ============================================================================
# Knowledge Dashboard Access Whitelist
# ============================================================================

def list_whitelist(page: int = 1, page_size: int = 20, active_only: bool = True) -> dict:
    page, page_size = validate_page_params(page, page_size, max_page_size=100)
    return _get("/api/v1/admin/knowledge/whitelist", page=page, page_size=page_size, active_only=active_only)


def add_whitelist_entry(body: dict) -> dict:
    return _post("/api/v1/admin/knowledge/whitelist", body)


def remove_whitelist_entry(entry_id: str) -> dict:
    return _delete(f"/api/v1/admin/knowledge/whitelist/{entry_id}")


def extend_whitelist_ttl(entry_id: str, body: dict) -> dict:
    return _patch(f"/api/v1/admin/knowledge/whitelist/{entry_id}/extend", body)


def sync_whitelist_to_configmap() -> dict:
    return _post("/api/v1/admin/knowledge/whitelist/sync")
