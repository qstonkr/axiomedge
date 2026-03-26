"""Integration tests for KB management API endpoints."""

import pytest


@pytest.mark.integration
def test_list_kbs(api):
    """GET /api/v1/kb/list should return a list of knowledge bases."""
    resp = api.get("/api/v1/kb/list")
    assert resp.status_code == 200
    data = resp.json()
    assert "kbs" in data
    assert isinstance(data["kbs"], list)


@pytest.mark.integration
def test_admin_list_kbs(api):
    """GET /api/v1/admin/kb should return a list of knowledge bases."""
    resp = api.get("/api/v1/admin/kb")
    assert resp.status_code == 200
    data = resp.json()
    assert "kbs" in data
    assert isinstance(data["kbs"], list)


@pytest.mark.integration
def test_get_kb_stats(api):
    """GET /api/v1/admin/kb/stats should return aggregation stats."""
    resp = api.get("/api/v1/admin/kb/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_kbs" in data
    assert "total_documents" in data
    assert "total_chunks" in data
    assert isinstance(data["total_kbs"], int)
    assert isinstance(data["total_chunks"], int)


@pytest.mark.integration
def test_get_single_kb_stats(api):
    """GET /api/v1/admin/kb/{kb_id}/stats should return per-KB stats."""
    resp = api.get("/api/v1/admin/kb/knowledge/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["kb_id"] == "knowledge"
    assert "total_chunks" in data
    assert "freshness" in data
