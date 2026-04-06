"""Distill Plugin API — 엣지 모델 관리."""

from __future__ import annotations

import streamlit as st

from services.api._core import _delete, _get, _post, _put


# ── 프로필 (빌드 설정) ──

@st.cache_data(ttl=300)
def list_distill_profiles() -> dict:
    return _get("/api/v1/distill/profiles")


def get_distill_profile(name: str) -> dict:
    return _get(f"/api/v1/distill/profiles/{name}")


def create_distill_profile(body: dict) -> dict:
    return _post("/api/v1/distill/profiles", body)


def update_distill_profile(name: str, body: dict) -> dict:
    return _put(f"/api/v1/distill/profiles/{name}", body)


def delete_distill_profile(name: str) -> dict:
    return _delete(f"/api/v1/distill/profiles/{name}")


@st.cache_data(ttl=300)
def list_search_groups_for_distill() -> dict:
    return _get("/api/v1/distill/search-groups")


# ── 빌드 ──

def trigger_distill_build(body: dict) -> dict:
    return _post("/api/v1/distill/builds", body, timeout=10)


@st.cache_data(ttl=15)
def list_distill_builds(profile_name: str | None = None) -> dict:
    return _get("/api/v1/distill/builds", profile_name=profile_name)


@st.cache_data(ttl=5)
def get_distill_build(build_id: str) -> dict:
    return _get(f"/api/v1/distill/builds/{build_id}")


def deploy_build(build_id: str) -> dict:
    return _post(f"/api/v1/distill/builds/{build_id}/deploy")


def rollback_build(build_id: str) -> dict:
    return _post(f"/api/v1/distill/builds/{build_id}/rollback")


# ── 학습 데이터 ──

@st.cache_data(ttl=60)
def list_training_data(
    profile_name: str, status: str | None = None,
    limit: int = 50, offset: int = 0,
) -> dict:
    return _get(
        "/api/v1/distill/training-data",
        profile_name=profile_name, status=status, limit=limit, offset=offset,
    )


def add_training_data(body: dict) -> dict:
    return _post("/api/v1/distill/training-data", body)


def review_training_data(body: dict) -> dict:
    return _put("/api/v1/distill/training-data/review", body)


@st.cache_data(ttl=60)
def get_training_data_stats(profile_name: str) -> dict:
    return _get("/api/v1/distill/training-data/stats", profile_name=profile_name)


# ── 엣지 로그 ──

def collect_edge_logs(profile_name: str | None = None) -> dict:
    body = {"profile_name": profile_name} if profile_name else {}
    return _post("/api/v1/distill/edge-logs/collect", body, timeout=60)


@st.cache_data(ttl=30)
def list_edge_logs(
    profile_name: str, store_id: str | None = None,
    success: bool | None = None, limit: int = 50, offset: int = 0,
) -> dict:
    return _get(
        "/api/v1/distill/edge-logs",
        profile_name=profile_name, store_id=store_id,
        success=success, limit=limit, offset=offset,
    )


@st.cache_data(ttl=30)
def get_edge_analytics(profile_name: str, days: int = 7) -> dict:
    return _get(
        "/api/v1/distill/edge-logs/analytics",
        profile_name=profile_name, days=days,
    )


@st.cache_data(ttl=30)
def list_failed_edge_queries(profile_name: str, limit: int = 50) -> dict:
    return _get(
        "/api/v1/distill/edge-logs/failed",
        profile_name=profile_name, limit=limit,
    )


# ── 재학습 ──

def trigger_retrain(body: dict) -> dict:
    return _post("/api/v1/distill/retrain", body, timeout=10)
