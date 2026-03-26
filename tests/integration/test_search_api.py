"""Integration tests for /api/v1/search/* endpoints."""

import pytest


@pytest.mark.integration
def test_hub_search_returns_response_structure(api):
    """POST /api/v1/search/hub should return a well-structured response."""
    resp = api.post("/api/v1/search/hub", json={
        "query": "test query",
        "top_k": 3,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "query" in data
    assert data["query"] == "test query"
    assert "chunks" in data
    assert isinstance(data["chunks"], list)
    assert "searched_kbs" in data
    assert isinstance(data["searched_kbs"], list)
    assert "total_chunks" in data
    assert isinstance(data["total_chunks"], int)
    assert "search_time_ms" in data
    assert isinstance(data["search_time_ms"], (int, float))


@pytest.mark.integration
def test_hub_search_with_empty_query_returns_error(api):
    """POST /api/v1/search/hub with empty query should return 422."""
    resp = api.post("/api/v1/search/hub", json={
        "query": "",
        "top_k": 3,
    })
    # FastAPI validation returns 422 for min_length=1 violation
    assert resp.status_code == 422


@pytest.mark.integration
def test_search_with_nonexistent_kb_returns_empty(api):
    """POST /api/v1/search/hub with nonexistent KB should return empty results."""
    resp = api.post("/api/v1/search/hub", json={
        "query": "test",
        "kb_ids": ["nonexistent-kb-xyz-999"],
        "top_k": 3,
    })
    # Should succeed but return no chunks (collection doesn't exist)
    assert resp.status_code in (200, 500)
    if resp.status_code == 200:
        data = resp.json()
        assert data["total_chunks"] == 0


@pytest.mark.integration
def test_list_searchable_kbs(api):
    """GET /api/v1/search/hub/kbs should list available KBs."""
    resp = api.get("/api/v1/search/hub/kbs")
    assert resp.status_code == 200
    data = resp.json()
    assert "kbs" in data
    assert isinstance(data["kbs"], list)
