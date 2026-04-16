"""KB Management API functions."""

from __future__ import annotations

import time

import streamlit as st

from services.api._core import (
    _delete,
    _get,
    _post,
    _put,
    api_failed,
    validate_page_params,
)


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
def get_kb_lifecycle(kb_id: str, *, filter: str | None = None) -> dict:  # noqa: A002
    return _get(f"/api/v1/admin/kb/{kb_id}/lifecycle", filter=filter)


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
