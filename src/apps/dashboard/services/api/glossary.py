"""Glossary API functions."""

from __future__ import annotations

import time
import urllib.parse

import httpx
import streamlit as st

from services.api._core import (
    _delete,
    _get,
    _post,
    _patch,
    _request,
    cfg,
    logger,
    validate_page_params,
)


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
    except Exception as exc:  # noqa: BLE001
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
    return _request("POST", f"/api/v1/admin/glossary/similarity-check?threshold={threshold}&page={page}&page_size={page_size}",  # noqa: E501
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
# Glossary Stats
# ============================================================================

@st.cache_data(ttl=300)
def get_glossary_domain_stats() -> dict:
    return _get("/api/v1/admin/glossary/domain-stats")


@st.cache_data(ttl=300)
def get_glossary_source_stats() -> dict:
    return _get("/api/v1/admin/glossary/source-stats")
