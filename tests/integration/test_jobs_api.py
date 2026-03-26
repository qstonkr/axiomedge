"""Integration tests for /api/v1/jobs/* endpoints."""

import pytest


@pytest.mark.integration
def test_list_jobs(api):
    """GET /api/v1/jobs should return jobs list."""
    resp = api.get("/api/v1/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs" in data
    assert isinstance(data["jobs"], list)


@pytest.mark.integration
def test_get_nonexistent_job_returns_404(api):
    """GET /api/v1/jobs/{id} with nonexistent ID should return 404."""
    resp = api.get("/api/v1/jobs/nonexistent-job-id-xyz")
    assert resp.status_code == 404
    data = resp.json()
    assert "detail" in data
