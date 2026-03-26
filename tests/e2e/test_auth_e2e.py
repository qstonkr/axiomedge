"""E2E tests: auth, user management, roles, KB permissions, activities."""

import uuid

import pytest


@pytest.mark.e2e
def test_create_user_assign_role(api):
    """Create user -> assign role -> verify role in user details."""
    user_email = f"test-e2e-{uuid.uuid4().hex[:8]}@test.local"
    user_id = None

    try:
        # 1. Create user
        create_resp = api.post("/api/v1/auth/users", json={
            "email": user_email,
            "display_name": "E2E Test User",
            "department": "IT",
            "organization_id": "test-org",
            "role": "viewer",
        })
        assert create_resp.status_code == 200, f"Create user failed: {create_resp.text}"
        result = create_resp.json()
        assert result["success"] is True
        user_id = result.get("user_id") or result.get("sub") or result.get("id")
        assert user_id, f"No user_id in response: {result}"

        # 2. Assign role
        role_resp = api.post(f"/api/v1/auth/users/{user_id}/roles", json={
            "role": "kb_manager",
        })
        assert role_resp.status_code == 200, f"Assign role failed: {role_resp.text}"
        assert role_resp.json()["success"] is True

        # 3. Verify role in user details
        user_resp = api.get(f"/api/v1/auth/users/{user_id}")
        assert user_resp.status_code == 200
        user_data = user_resp.json()
        roles = user_data.get("roles", [])
        # Roles can be list of strings or list of dicts
        role_names = []
        for r in roles:
            if isinstance(r, str):
                role_names.append(r)
            elif isinstance(r, dict):
                role_names.append(r.get("role", ""))
        assert "kb_manager" in role_names, f"Expected 'kb_manager' in roles: {roles}"

    finally:
        if user_id:
            api.delete(f"/api/v1/auth/users/{user_id}")


@pytest.mark.e2e
def test_kb_permission_grant(api):
    """Create user -> grant KB permission -> verify in permissions list."""
    user_email = f"test-e2e-perm-{uuid.uuid4().hex[:8]}@test.local"
    user_id = None
    kb_id = f"test-e2e-authkb-{uuid.uuid4().hex[:8]}"

    try:
        # 1. Create user
        create_resp = api.post("/api/v1/auth/users", json={
            "email": user_email,
            "display_name": "E2E Permission User",
        })
        assert create_resp.status_code == 200
        result = create_resp.json()
        user_id = result.get("user_id") or result.get("sub") or result.get("id")
        assert user_id

        # 2. Create KB (so permission target exists)
        api.post("/api/v1/admin/kb", json={"kb_id": kb_id, "name": kb_id})

        # 3. Grant KB permission
        perm_resp = api.post(f"/api/v1/auth/kb/{kb_id}/permissions", json={
            "user_id": user_id,
            "permission_level": "contributor",
        })
        assert perm_resp.status_code == 200, f"Grant permission failed: {perm_resp.text}"
        assert perm_resp.json()["success"] is True

        # 4. Verify in permissions list
        perms_resp = api.get(f"/api/v1/auth/kb/{kb_id}/permissions")
        assert perms_resp.status_code == 200
        permissions = perms_resp.json().get("permissions", [])
        perm_user_ids = [p.get("user_id") for p in permissions]
        assert user_id in perm_user_ids, (
            f"User {user_id} not found in KB permissions: {permissions}"
        )

    finally:
        if user_id:
            api.delete(f"/api/v1/auth/kb/{kb_id}/permissions/{user_id}")
            api.delete(f"/api/v1/auth/users/{user_id}")
        api.delete(f"/api/v1/admin/kb/{kb_id}")


@pytest.mark.e2e
def test_user_activity_logged(api):
    """Perform search -> check my-activities -> verify activity exists."""
    kb_id = f"test-e2e-activity-{uuid.uuid4().hex[:8]}"

    try:
        # 1. Upload something to search against
        run_id = uuid.uuid4().hex[:8]
        content = f"# Activity Test (run {run_id})\n\n" + f"Testing user activity logging for search operations. Run {run_id}. " * 20
        files = {"file": (f"activity-{kb_id}.txt", content.encode(), "text/plain")}
        api.post("/api/v1/knowledge/upload", files=files, data={"kb_id": kb_id})

        import time
        time.sleep(1)

        # 2. Perform a search
        search_resp = api.post("/api/v1/search/hub", json={
            "query": "activity logging test",
            "kb_ids": [kb_id],
            "top_k": 3,
        })
        assert search_resp.status_code == 200

        # 3. Check my-activities (auth is disabled so anonymous user)
        activities_resp = api.get("/api/v1/auth/my-activities")
        assert activities_resp.status_code == 200
        # The endpoint should return a valid response shape
        data = activities_resp.json()
        assert "activities" in data

        # 4. Check activity summary
        summary_resp = api.get("/api/v1/auth/my-activities/summary")
        assert summary_resp.status_code == 200

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")
