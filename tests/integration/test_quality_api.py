"""Integration tests for Quality, Transparency, Verification, Cache API endpoints."""

import pytest


@pytest.mark.integration
def test_get_transparency_stats(api):
    """GET /api/v1/admin/transparency/stats should return transparency metrics."""
    resp = api.get("/api/v1/admin/transparency/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_documents" in data
    assert "with_provenance" in data
    assert "transparency_score" in data
    assert isinstance(data["transparency_score"], (int, float))


@pytest.mark.integration
def test_get_verification_pending(api):
    """GET /api/v1/admin/verification/pending should return pending documents."""
    resp = api.get("/api/v1/admin/verification/pending")
    assert resp.status_code == 200
    data = resp.json()
    assert "documents" in data
    assert isinstance(data["documents"], list)
    assert "total" in data
    assert "page" in data


@pytest.mark.integration
def test_get_cache_stats(api):
    """GET /api/v1/admin/cache/stats should return cache statistics."""
    resp = api.get("/api/v1/admin/cache/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "hits" in data
    assert "misses" in data
    assert "hit_rate" in data
    assert isinstance(data["hit_rate"], (int, float))


@pytest.mark.integration
def test_get_contributors(api):
    """GET /api/v1/admin/contributors should return contributor list."""
    resp = api.get("/api/v1/admin/contributors")
    assert resp.status_code == 200
    data = resp.json()
    assert "contributors" in data
    assert isinstance(data["contributors"], list)
    assert "total" in data
