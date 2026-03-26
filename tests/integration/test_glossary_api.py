"""Integration tests for /api/v1/admin/glossary/* endpoints."""

import pytest


@pytest.mark.integration
def test_list_glossary_terms(api):
    """GET /api/v1/admin/glossary should return terms list."""
    resp = api.get("/api/v1/admin/glossary")
    assert resp.status_code == 200
    data = resp.json()
    assert "terms" in data
    assert isinstance(data["terms"], list)
    assert "total" in data
    assert isinstance(data["total"], int)
    assert "page" in data
    assert "page_size" in data


@pytest.mark.integration
def test_get_glossary_domain_stats(api):
    """GET /api/v1/admin/glossary/domain-stats should return domain statistics."""
    resp = api.get("/api/v1/admin/glossary/domain-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "domains" in data
    assert isinstance(data["domains"], dict)


@pytest.mark.integration
def test_get_glossary_source_stats(api):
    """GET /api/v1/admin/glossary/source-stats should return source statistics."""
    resp = api.get("/api/v1/admin/glossary/source-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "sources" in data
    assert isinstance(data["sources"], dict)
