"""Integration tests for /api/v1/auth/* endpoints."""

import pytest


@pytest.mark.integration
def test_get_me_returns_user_structure(api):
    """GET /api/v1/auth/me should return user info (anonymous when auth disabled)."""
    resp = api.get("/api/v1/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert "sub" in data
    assert "email" in data
    assert "display_name" in data
    assert "provider" in data
    assert "roles" in data
    assert isinstance(data["roles"], list)
    assert "permissions" in data
    assert isinstance(data["permissions"], list)


@pytest.mark.integration
def test_list_roles_returns_default_roles(api):
    """GET /api/v1/auth/roles should return default role definitions."""
    resp = api.get("/api/v1/auth/roles")
    assert resp.status_code == 200
    data = resp.json()
    assert "roles" in data
    assert isinstance(data["roles"], list)
    assert len(data["roles"]) > 0
    # Each role should have required fields
    for role in data["roles"]:
        assert "name" in role
        assert "display_name" in role
        assert "weight" in role
        assert "permissions" in role


@pytest.mark.integration
def test_list_users_returns_list(api):
    """GET /api/v1/auth/users should return a list structure."""
    resp = api.get("/api/v1/auth/users")
    assert resp.status_code == 200
    data = resp.json()
    assert "users" in data
    assert isinstance(data["users"], list)
    assert "total" in data
    assert isinstance(data["total"], int)


@pytest.mark.integration
def test_system_stats_returns_structure(api):
    """GET /api/v1/auth/system/stats should return auth statistics."""
    resp = api.get("/api/v1/auth/system/stats")
    assert resp.status_code == 200
    data = resp.json()
    # Should have top-level stat categories
    assert "users" in data
    assert "roles" in data
    assert "kb_permissions" in data
    assert "abac_policies" in data
    assert "activities" in data
    # Users sub-keys
    assert "total" in data["users"]
    assert "active" in data["users"]
