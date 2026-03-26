"""E2E tests: upload -> ingest -> search full pipeline."""

import time
import uuid

import pytest


@pytest.mark.e2e
def test_upload_and_search(api):
    """Upload a text file, verify chunks created, search, verify results found."""
    run_id = uuid.uuid4().hex[:8]
    kb_id = f"test-e2e-auto-{run_id}"

    # 1. Upload a test document
    sections = []
    for i in range(10):
        sections.append(f"## Section {i + 1}: Knowledge Management\n\n")
        sections.append(
            f"Knowledge management system end-to-end test document run {run_id}. "
            f"Section {i + 1} content for testing. " * 5
        )
        sections.append("\n\n")
    content = f"# E2E Test Document ({run_id})\n\n" + "".join(sections)
    files = {"file": (f"test-e2e-{run_id}.txt", content.encode(), "text/plain")}
    data = {"kb_id": kb_id}

    resp = api.post("/api/v1/knowledge/upload", files=files, data=data)
    assert resp.status_code == 200, f"Upload failed: {resp.text}"
    result = resp.json()
    assert result["success"] is True
    assert result["chunks_created"] > 0

    # 2. Wait briefly for indexing to settle
    time.sleep(1)

    # 3. Search for the uploaded content
    search_resp = api.post("/api/v1/search/hub", json={
        "query": "knowledge management system",
        "kb_ids": [kb_id],
        "top_k": 3,
    })
    assert search_resp.status_code == 200
    search_result = search_resp.json()
    assert len(search_result.get("chunks", [])) > 0, (
        "Expected at least one chunk in search results after upload"
    )

    # 4. Cleanup: delete the test KB
    delete_resp = api.delete(f"/api/v1/admin/kb/{kb_id}")
    # Accept 200 (deleted) or 500 (collection not found is ok for cleanup)
    assert delete_resp.status_code in (200, 500)


@pytest.mark.e2e
def test_upload_empty_file(api):
    """Upload an empty file should return an error."""
    files = {"file": ("empty.txt", b"", "text/plain")}
    data = {"kb_id": "test-e2e-empty"}

    resp = api.post("/api/v1/knowledge/upload", files=files, data=data)
    # Empty file should fail parsing
    assert resp.status_code == 500
    result = resp.json()
    assert "detail" in result
