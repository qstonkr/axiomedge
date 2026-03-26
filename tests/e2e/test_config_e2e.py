"""E2E tests: config weights and job lifecycle."""

import time
import uuid

import pytest


@pytest.mark.e2e
def test_update_and_reset_weights(api):
    """Get weights -> update -> verify changed -> reset -> verify restored."""
    # 1. Get current weights
    get_resp = api.post("/api/v1/admin/config/weights")
    assert get_resp.status_code == 200
    original_weights = get_resp.json()

    # Remember original dense_weight for comparison
    hybrid = original_weights.get("hybrid_search", {})
    original_dense = hybrid.get("dense_weight")

    # 2. Update a weight value
    new_dense = 0.42
    update_resp = api.put("/api/v1/admin/config/weights", json={
        "hybrid_search.dense_weight": new_dense,
    })
    assert update_resp.status_code == 200
    update_result = update_resp.json()
    assert len(update_result.get("applied", [])) > 0, "Expected at least one applied change"

    # Verify the change is reflected
    current = update_result.get("current", {})
    assert current.get("hybrid_search", {}).get("dense_weight") == new_dense

    # 3. Reset to defaults
    reset_resp = api.post("/api/v1/admin/config/weights/reset")
    assert reset_resp.status_code == 200
    reset_result = reset_resp.json()
    assert reset_result.get("status") == "reset"

    # Verify restored to original
    restored = reset_result.get("current", {})
    restored_dense = restored.get("hybrid_search", {}).get("dense_weight")
    if original_dense is not None:
        assert restored_dense == original_dense, (
            f"Expected dense_weight restored to {original_dense}, got {restored_dense}"
        )


@pytest.mark.e2e
def test_job_lifecycle(api):
    """Upload file via file-upload-ingest -> get job_id -> poll status -> verify completed."""
    kb_id = f"test-e2e-job-{uuid.uuid4().hex[:8]}"

    try:
        # 1. Upload via file-upload-ingest endpoint (returns job_id)
        run_id = uuid.uuid4().hex[:8]
        content = f"# Job Lifecycle Test (run {run_id})\n\n" + f"Testing background job tracking and status polling. Run {run_id}. " * 20
        files = {"file": (f"job-{kb_id}.txt", content.encode(), "text/plain")}
        data = {"kb_id": kb_id}
        upload_resp = api.post("/api/v1/knowledge/file-upload-ingest", files=files, data=data)
        assert upload_resp.status_code == 200, f"Upload failed: {upload_resp.text}"
        result = upload_resp.json()
        assert result["success"] is True
        job_id = result.get("job_id")
        assert job_id, f"No job_id in response: {result}"

        # 2. Poll job status until completed or timeout
        max_wait = 30  # seconds
        poll_interval = 1
        elapsed = 0
        final_status = None

        while elapsed < max_wait:
            job_resp = api.get(f"/api/v1/jobs/{job_id}")
            assert job_resp.status_code == 200, f"Job status failed: {job_resp.text}"
            job_data = job_resp.json()
            final_status = job_data.get("status")

            if final_status in ("completed", "failed"):
                break

            time.sleep(poll_interval)
            elapsed += poll_interval

        assert final_status == "completed", (
            f"Expected job to complete, got status={final_status} after {elapsed}s"
        )

        # 3. Verify job details
        job_resp = api.get(f"/api/v1/jobs/{job_id}")
        job_data = job_resp.json()
        assert job_data["chunks"] > 0, "Expected chunks > 0 for completed job"
        assert job_data["processed"] > 0, "Expected processed > 0 for completed job"
        assert len(job_data.get("errors", [])) == 0, f"Unexpected errors: {job_data['errors']}"

        # 4. Verify jobs list includes this job
        list_resp = api.get("/api/v1/jobs")
        assert list_resp.status_code == 200
        jobs = list_resp.json().get("jobs", [])
        job_ids = [j.get("id") for j in jobs]
        assert job_id in job_ids, f"Job {job_id} not found in jobs list"

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")
