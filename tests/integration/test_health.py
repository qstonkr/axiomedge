"""Integration tests for /health endpoint."""

import pytest


@pytest.mark.integration
def test_health_endpoint_returns_ok(api):
    resp = api.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["status"] in ("healthy", "degraded")


@pytest.mark.integration
def test_health_checks_services(api):
    resp = api.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    checks = data["checks"]
    # All expected service keys must be present
    expected_keys = {"qdrant", "neo4j", "embedding", "llm", "redis", "database", "paddleocr"}
    assert expected_keys.issubset(checks.keys()), (
        f"Missing health check keys: {expected_keys - set(checks.keys())}"
    )
    # Each value must be a boolean
    for key, value in checks.items():
        assert isinstance(value, bool), f"checks[{key!r}] should be bool, got {type(value)}"
