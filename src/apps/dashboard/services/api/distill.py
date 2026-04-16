"""Distill Plugin API — 엣지 모델 관리."""

from __future__ import annotations

from urllib.parse import quote

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


@st.cache_data(ttl=300)
def list_distill_base_models(enabled_only: bool = True) -> dict:
    """베이스 모델 레지스트리 조회.

    Args:
        enabled_only: True 면 드롭다운 노출 대상 (기본). Admin UI 는 False.
    """
    return _get("/api/v1/distill/base-models", enabled_only=enabled_only)


def upsert_distill_base_model(body: dict) -> dict:
    """베이스 모델 레지스트리 추가/갱신. Admin UI 전용."""
    return _post("/api/v1/distill/base-models", body)


def delete_distill_base_model(hf_id: str) -> dict:
    """베이스 모델 레지스트리 삭제. Admin UI 전용.

    ``hf_id`` 는 ``org/repo`` 형식이므로 슬래시는 보존하고 나머지 특수문자
    (공백, ``%``, ``#`` 등) 는 percent-encode 해서 URL 깨짐을 방지.
    """
    encoded = quote(hf_id, safe="/")
    return _delete(f"/api/v1/distill/base-models/{encoded}")


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
    source_type: str | None = None, batch_id: str | None = None,
    limit: int = 50, offset: int = 0,
) -> dict:
    return _get(
        "/api/v1/distill/training-data",
        profile_name=profile_name, status=status,
        source_type=source_type, batch_id=batch_id,
        limit=limit, offset=offset,
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
    return _post(
        "/api/v1/distill/edge-logs/collect"
        + (f"?profile_name={profile_name}" if profile_name else ""),
        {}, timeout=60,
    )


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


# ── 데이터 큐레이션 ──

def generate_training_data(body: dict) -> dict:
    return _post("/api/v1/distill/training-data/generate", body, timeout=10)


def generate_test_data(body: dict) -> dict:
    return _post("/api/v1/distill/training-data/generate-test", body, timeout=120)


@st.cache_data(ttl=10)
def get_generation_batch(batch_id: str) -> dict:
    return _get(f"/api/v1/distill/training-data/batches/{batch_id}")


def review_edit_training_data(body: dict) -> dict:
    return _put("/api/v1/distill/training-data/review-edit", body)


# ── 스마트 승인 ──

def smart_approve(profile_name: str, source_type: str | None = None) -> dict:
    path = f"/api/v1/distill/training-data/smart-approve?profile_name={profile_name}"
    if source_type:
        path += f"&source_type={source_type}"
    return _post(path, {})


# ── 증강 + 용어 ──

def augment_training_data(body: dict) -> dict:
    return _post("/api/v1/distill/training-data/augment", body, timeout=10)


def generate_term_qa(body: dict) -> dict:
    return _post("/api/v1/distill/training-data/generate-term-qa", body, timeout=10)


# ── 모델 리셋 ──

def reset_to_base_model(profile_name: str) -> dict:
    return _post(
        f"/api/v1/distill/builds/reset-to-base?profile_name={profile_name}", {},
    )


# ── 초기화 ──

def delete_by_source_type(profile_name: str, source_type: str) -> dict:
    return _delete(
        f"/api/v1/distill/training-data/by-source"
        f"?profile_name={profile_name}&source_type={source_type}"
    )


def delete_batch_data(batch_id: str) -> dict:
    return _delete(f"/api/v1/distill/training-data/batch/{batch_id}")


def delete_build(build_id: str) -> dict:
    return _delete(f"/api/v1/distill/builds/{build_id}")


# ── 모델 버전 ──

@st.cache_data(ttl=15)
def list_model_versions(profile_name: str) -> dict:
    return _get("/api/v1/distill/builds/versions", profile_name=profile_name)


# ── 앱 빌드 ──

@st.cache_data(ttl=30)
def get_app_info(profile_name: str) -> dict:
    return _get("/api/v1/distill/app/info", profile_name=profile_name)


# ── 엣지 서버 관리 ──

@st.cache_data(ttl=15)
def list_edge_servers(profile_name: str | None = None, status: str | None = None) -> dict:
    return _get(
        "/api/v1/distill/edge-servers",
        profile_name=profile_name, status=status,
    )


def get_edge_server(store_id: str) -> dict:
    return _get(f"/api/v1/distill/edge-servers/{store_id}")


@st.cache_data(ttl=15)
def get_fleet_stats(profile_name: str) -> dict:
    return _get("/api/v1/distill/edge-servers/fleet-stats", profile_name=profile_name)


def request_server_update(store_id: str, update_type: str) -> dict:
    return _post(
        f"/api/v1/distill/edge-servers/{store_id}/request-update",
        {"update_type": update_type},
    )


def bulk_request_update(profile_name: str, update_type: str) -> dict:
    return _post(
        "/api/v1/distill/edge-servers/bulk-request-update",
        {"profile_name": profile_name, "update_type": update_type},
    )


def set_app_version(profile_name: str, version: str) -> dict:
    """엣지 앱 소스 버전 태그만 갱신 (S3 manifest 업데이트).

    실제 소스 파일 재다운로드는 엣지가 heartbeat 시 자동으로 수행.
    """
    return _post(
        f"/api/v1/distill/profiles/{profile_name}/app-version",
        {"version": version},
    )


def delete_edge_server(store_id: str) -> dict:
    return _delete(f"/api/v1/distill/edge-servers/{store_id}")


def register_edge_server(store_id: str, profile_name: str, display_name: str = "") -> dict:
    return _post("/api/v1/distill/edge-servers/register", {
        "store_id": store_id, "profile_name": profile_name, "display_name": display_name,
    })


def get_provision_config(store_id: str) -> dict:
    return _get(f"/api/v1/distill/edge-servers/{store_id}/provision")
