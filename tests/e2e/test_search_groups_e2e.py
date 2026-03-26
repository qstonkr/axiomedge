"""E2E tests: search groups - create group, search via group, cleanup."""

import time
import uuid

import pytest


@pytest.mark.e2e
def test_create_group_and_search(api):
    """Create search group with KB IDs -> search via group -> verify scoped results."""
    grp_run = uuid.uuid4().hex[:8]
    kb_id_1 = f"test-e2e-grp1-{grp_run}"
    kb_id_2 = f"test-e2e-grp2-{grp_run}"
    group_id = None
    run_id = uuid.uuid4().hex[:8]

    try:
        # 1. Upload content to two KBs (unique per run to avoid dedup)
        content_1 = (
            f"# Network Infrastructure Guide (run {run_id})\n\n"
            + f"Network infrastructure includes routers, switches, and firewalls. Run {run_id}. " * 20
            + "\n\n"
        )
        files_1 = {"file": (f"network-{kb_id_1}.txt", content_1.encode(), "text/plain")}
        resp_1 = api.post("/api/v1/knowledge/upload", files=files_1, data={"kb_id": kb_id_1})
        assert resp_1.status_code == 200, f"Upload KB1 failed: {resp_1.text}"
        assert resp_1.json().get("chunks_created", 0) > 0, f"KB1 upload produced 0 chunks: {resp_1.json()}"

        content_2 = (
            f"# Application Deployment Guide (run {run_id})\n\n"
            + f"Application deployment follows CI/CD pipeline with automated testing. Run {run_id}. " * 20
            + "\n\n"
        )
        files_2 = {"file": (f"deploy-{kb_id_2}.txt", content_2.encode(), "text/plain")}
        resp_2 = api.post("/api/v1/knowledge/upload", files=files_2, data={"kb_id": kb_id_2})
        assert resp_2.status_code == 200, f"Upload KB2 failed: {resp_2.text}"
        assert resp_2.json().get("chunks_created", 0) > 0, f"KB2 upload produced 0 chunks: {resp_2.json()}"

        time.sleep(1)

        # 2. Create a search group
        group_name = f"test-e2e-group-{uuid.uuid4().hex[:8]}"
        create_resp = api.post("/api/v1/search-groups", json={
            "name": group_name,
            "description": "E2E test search group",
            "kb_ids": [kb_id_1, kb_id_2],
        })
        assert create_resp.status_code == 200, f"Create group failed: {create_resp.text}"
        group_data = create_resp.json()
        group_id = group_data.get("id") or group_data.get("group_id")
        assert group_id, f"No group_id in response: {group_data}"

        # 3. Verify group in listing
        list_resp = api.get("/api/v1/search-groups")
        assert list_resp.status_code == 200
        groups = list_resp.json().get("groups", [])
        group_ids = [g.get("id") or g.get("group_id") for g in groups]
        assert group_id in group_ids, f"Group {group_id} not in list: {group_ids}"

        # 4. Search via group (by group_name)
        search_resp = api.post("/api/v1/search/hub", json={
            "query": "network infrastructure deployment",
            "group_name": group_name,
            "top_k": 5,
        })
        assert search_resp.status_code == 200
        result = search_resp.json()
        # The group should scope the search to our 2 KBs
        chunks = result.get("chunks", [])
        assert len(chunks) > 0, "Expected results when searching via group"

        # 5. Get group details
        get_resp = api.get(f"/api/v1/search-groups/{group_id}")
        assert get_resp.status_code == 200
        detail = get_resp.json()
        detail_kb_ids = detail.get("kb_ids", [])
        assert kb_id_1 in detail_kb_ids
        assert kb_id_2 in detail_kb_ids

        # 6. Get group KBs
        kbs_resp = api.get(f"/api/v1/search-groups/{group_id}/kbs")
        assert kbs_resp.status_code == 200
        resolved_kbs = kbs_resp.json().get("kb_ids", [])
        assert kb_id_1 in resolved_kbs
        assert kb_id_2 in resolved_kbs

    finally:
        if group_id:
            api.delete(f"/api/v1/search-groups/{group_id}")
        api.delete(f"/api/v1/admin/kb/{kb_id_1}")
        api.delete(f"/api/v1/admin/kb/{kb_id_2}")
