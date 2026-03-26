"""E2E tests: ingestion gate validation via upload API."""

import uuid

import pytest


@pytest.mark.e2e
def test_valid_file_passes_gates(api):
    """Upload valid .txt file -> success."""
    run_id = uuid.uuid4().hex[:8]
    kb_id = f"test-gate-{run_id}"
    content = (
        f"# Valid Ingestion Gate Test ({run_id})\n\n"
        + f"This document has enough content to pass all ingestion gates. Run {run_id}. " * 30
    )
    files = {"file": (f"gate-{run_id}.txt", content.encode(), "text/plain")}
    data = {"kb_id": kb_id}

    resp = api.post("/api/v1/knowledge/upload", files=files, data=data)
    assert resp.status_code == 200, f"Upload failed: {resp.text}"
    result = resp.json()
    assert result["success"] is True
    assert result["chunks_created"] > 0

    # Cleanup
    api.delete(f"/api/v1/admin/kb/{kb_id}")


@pytest.mark.e2e
def test_empty_file_rejected_by_gate(api):
    """Upload empty file -> rejected by ingestion gate."""
    run_id = uuid.uuid4().hex[:8]
    files = {"file": (f"gate-empty-{run_id}.txt", b"", "text/plain")}
    data = {"kb_id": f"test-gate-empty-{run_id}"}

    resp = api.post("/api/v1/knowledge/upload", files=files, data=data)
    # Empty file should fail at the gate or parser
    assert resp.status_code in (400, 500)
