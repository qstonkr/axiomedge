"""Search & RAG API functions."""

from __future__ import annotations

from typing import Any

import streamlit as st

from services.api._core import (
    _delete,
    _get,
    _post,
    _put,
    cfg,
    sanitize_input,
    validate_page_params,
)


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
    return _post("/api/v1/search/hub", body, timeout=cfg.API_SEARCH_TIMEOUT, _retries=1)


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
# Search History & Analytics
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
# Search Groups
# ============================================================================

@st.cache_data(ttl=300)
def list_search_groups() -> dict:
    return _get("/api/v1/search-groups")


def create_search_group(body: dict) -> dict:
    result = _post("/api/v1/search-groups", body)
    list_search_groups.clear()
    return result


def update_search_group(group_id: str, body: dict) -> dict:
    result = _put(f"/api/v1/search-groups/{group_id}", body)
    list_search_groups.clear()
    return result


def delete_search_group(group_id: str) -> dict:
    result = _delete(f"/api/v1/search-groups/{group_id}")
    list_search_groups.clear()
    return result


# ============================================================================
# Server Cache Management
# ============================================================================

def clear_search_cache() -> dict:
    return _post("/api/v1/admin/kb/search-cache/clear")
