"""Integration tests for Graph API endpoints."""

import pytest


@pytest.mark.integration
def test_graph_search(api):
    """POST /api/v1/admin/graph/search should return graph search results."""
    resp = api.post("/api/v1/admin/graph/search", json={
        "query": "kubernetes deployment",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "query" in data or "results" in data or "message" in data


@pytest.mark.integration
def test_graph_integrity_check(api):
    """GET /api/v1/admin/graph/integrity should return integrity report."""
    resp = api.get("/api/v1/admin/graph/integrity")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["status"] in ("ok", "warning", "error")
    assert "orphan_nodes" in data
    assert "dangling_edges" in data
    assert "missing_relationships" in data
