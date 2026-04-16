"""Miscellaneous API functions: ownership, feedback, error reports, ingestion,
data sources, traceability, verification, contributors, whitelist, jobs,
config weights, version management.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import streamlit as st

from services.api._core import (
    _delete,
    _get,
    _patch,
    _post,
    _put,
    _request,
    cfg,
    logger,
    sanitize_input,
    validate_page_params,
)


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
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "_api_failed": True}


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


# ============================================================================
# Jobs
# ============================================================================

@st.cache_data(ttl=30)
def list_jobs() -> dict:
    return _get("/api/v1/jobs")


def get_job(job_id: str) -> dict:
    return _get(f"/api/v1/jobs/{job_id}")


def cancel_job(job_id: str) -> dict:
    return _post(f"/api/v1/jobs/{job_id}/cancel")


# ============================================================================
# Config Weights
# ============================================================================

def get_config_weights() -> dict:
    return _request("POST", "/api/v1/admin/config/weights", json_body={"action": "read"})


def update_config_weights(body: dict) -> dict:
    return _request("PUT", "/api/v1/admin/config/weights", json_body=body)


def reset_config_weights() -> dict:
    return _post("/api/v1/admin/config/weights/reset")


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
