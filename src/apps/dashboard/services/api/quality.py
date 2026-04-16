"""Quality, evaluation, golden set, trust scores, dedup API functions."""

from __future__ import annotations

import streamlit as st

from services.api._core import (
    _delete,
    _get,
    _patch,
    _post,
    validate_page_params,
)


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
