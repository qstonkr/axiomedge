"""Integration tests for Dedup API endpoints."""

import pytest


@pytest.mark.integration
def test_get_dedup_stats(api):
    """GET /api/v1/admin/dedup/stats should return dedup statistics."""
    resp = api.get("/api/v1/admin/dedup/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_duplicates_found" in data
    assert "stages" in data
    assert "bloom" in data["stages"]
    assert "lsh" in data["stages"]
    assert "semhash" in data["stages"]
    assert "document_count" in data


@pytest.mark.integration
def test_get_dedup_conflicts(api):
    """GET /api/v1/admin/dedup/conflicts should return conflict list."""
    resp = api.get("/api/v1/admin/dedup/conflicts")
    assert resp.status_code == 200
    data = resp.json()
    assert "conflicts" in data
    assert isinstance(data["conflicts"], list)
    assert "total" in data


@pytest.mark.integration
def test_resolve_dedup_conflict(api):
    """POST /api/v1/admin/dedup/resolve should handle resolution."""
    resp = api.post("/api/v1/admin/dedup/resolve", json={
        "conflict_id": "nonexistent-conflict-id",
        "resolution": "keep_newest",
        "resolved_by": "test-user",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "success" in data
    # Should gracefully handle nonexistent conflict
    assert isinstance(data["success"], bool)
