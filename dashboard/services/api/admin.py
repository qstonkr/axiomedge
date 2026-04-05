"""Admin operations: graph, embedding, cache, pipeline API functions."""

from __future__ import annotations

from typing import Any

import streamlit as st

from services.api._core import (
    PUBLISH_EXECUTE_TIMEOUT_SECONDS,
    _get,
    _post,
    sanitize_input,
)


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


def graph_integrity_check() -> dict:
    return _post("/api/v1/admin/graph/integrity/check")


def graph_experts_search(topic: str) -> dict:
    return _get("/api/v1/admin/graph/experts", topic=topic)


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


@st.cache_data(ttl=60)
def get_pipeline_gates_blocked() -> dict:
    return _get("/api/v1/admin/pipeline/gates/blocked")
