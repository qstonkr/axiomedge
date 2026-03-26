"""Integration tests for KB Lifecycle API endpoints."""

import pytest


@pytest.mark.integration
def test_get_kb_lifecycle(api):
    """GET /api/v1/admin/kb/{kb_id}/lifecycle should return lifecycle info."""
    resp = api.get("/api/v1/admin/kb/knowledge/lifecycle")
    assert resp.status_code == 200
    data = resp.json()
    assert "kb_id" in data
    assert data["kb_id"] == "knowledge"
    assert "stage" in data
