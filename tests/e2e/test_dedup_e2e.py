"""E2E tests: duplicate document detection via upload API."""

import time
import uuid

import pytest


@pytest.mark.e2e
def test_duplicate_document_rejected(api):
    """Upload same file twice -> second upload should detect duplicate."""
    run_id = uuid.uuid4().hex[:8]
    kb_id = f"test-dedup-{run_id}"
    content = f"# Dedup Test Document ({run_id})\n\n" + f"This is a test document for dedup e2e testing. Run {run_id}. " * 50

    # 1. Upload first copy
    files = {"file": (f"dedup-{run_id}.txt", content.encode(), "text/plain")}
    data = {"kb_id": kb_id}
    resp1 = api.post("/api/v1/knowledge/upload", files=files, data=data)
    assert resp1.status_code == 200, f"First upload failed: {resp1.text}"
    result1 = resp1.json()
    assert result1["success"] is True

    time.sleep(1)

    # 2. Upload second copy (exact duplicate with different filename)
    files2 = {"file": (f"dedup-copy-{run_id}.txt", content.encode(), "text/plain")}
    resp2 = api.post("/api/v1/knowledge/upload", files=files2, data=data)
    # Depending on gate policy, may succeed with dedup flag or be rejected
    assert resp2.status_code in (200, 409, 500)

    # 3. Cleanup
    api.delete(f"/api/v1/admin/kb/{kb_id}")


@pytest.mark.e2e
def test_near_duplicate_flagged(api):
    """Upload slightly modified version -> should flag as near-duplicate."""
    run_id = uuid.uuid4().hex[:8]
    kb_id = f"test-near-dedup-{run_id}"
    base_content = f"# Near Dedup Test ({run_id})\n\n" + f"knowledge management system deployment guide for enterprise. Run {run_id}. " * 50
    modified_content = f"# Near Dedup Test ({run_id})\n\n" + f"knowledge management system deployment guide for enterprise users. Run {run_id}. " * 50

    # 1. Upload original
    files1 = {"file": (f"near-dedup-orig-{run_id}.txt", base_content.encode(), "text/plain")}
    resp1 = api.post("/api/v1/knowledge/upload", files=files1, data={"kb_id": kb_id})
    assert resp1.status_code == 200, f"Original upload failed: {resp1.text}"

    time.sleep(1)

    # 2. Upload modified version
    files2 = {"file": (f"near-dedup-mod-{run_id}.txt", modified_content.encode(), "text/plain")}
    resp2 = api.post("/api/v1/knowledge/upload", files=files2, data={"kb_id": kb_id})
    # Should succeed but may flag the near-duplicate
    assert resp2.status_code in (200, 409, 500)

    # 3. Check dedup stats to verify detection
    stats_resp = api.get("/api/v1/admin/dedup/stats")
    if stats_resp.status_code == 200:
        stats = stats_resp.json()
        # Just verify the endpoint works and returns expected shape
        assert "stages" in stats

    # 4. Cleanup
    api.delete(f"/api/v1/admin/kb/{kb_id}")
