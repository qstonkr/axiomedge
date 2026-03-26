"""Integration tests for /api/v1/admin/config/weights endpoints."""

import pytest


@pytest.mark.integration
def test_get_config_weights(api):
    """POST /api/v1/admin/config/weights should return current weights."""
    resp = api.post("/api/v1/admin/config/weights")
    assert resp.status_code == 200
    data = resp.json()
    # Should be a dict with config sections
    assert isinstance(data, dict)
    assert len(data) > 0


@pytest.mark.integration
def test_update_config_weights(api):
    """PUT /api/v1/admin/config/weights should update and return applied changes."""
    # First get the current weights to confirm the endpoint works
    current = api.post("/api/v1/admin/config/weights")
    assert current.status_code == 200

    # Try updating search.rerank_pool_multiplier which is a known field
    resp = api.put("/api/v1/admin/config/weights", json={
        "search.rerank_pool_multiplier": 3,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "applied" in data
    assert "current" in data
    assert isinstance(data["applied"], dict)
    assert len(data["applied"]) > 0


@pytest.mark.integration
def test_update_config_weights_empty_body_returns_400(api):
    """PUT /api/v1/admin/config/weights with empty body should return 400."""
    resp = api.put("/api/v1/admin/config/weights", json={})
    assert resp.status_code == 400


@pytest.mark.integration
def test_reset_config_weights(api):
    """POST /api/v1/admin/config/weights/reset should reset to defaults."""
    resp = api.post("/api/v1/admin/config/weights/reset")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "reset"
    assert "current" in data
    assert isinstance(data["current"], dict)
