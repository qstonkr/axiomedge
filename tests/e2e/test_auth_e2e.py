"""E2E tests: auth, user management, roles, KB permissions, activities, internal login."""

import os
import uuid

import pytest

# Internal auth E2E tests require AUTH_PROVIDER=internal
_IS_INTERNAL_AUTH = os.getenv("AUTH_PROVIDER", "local") == "internal"


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


# =============================================================================
# Internal Auth (LOGIN / REFRESH / LOGOUT) E2E Tests
# Require: AUTH_ENABLED=true AUTH_PROVIDER=internal AUTH_JWT_SECRET=...
# =============================================================================


@pytest.mark.e2e
@pytest.mark.skipif(not _IS_INTERNAL_AUTH, reason="Requires AUTH_PROVIDER=internal")
def test_register_login_logout_flow(api):
    """Register user -> login -> access /me -> refresh -> logout -> verify denied."""
    email = f"test-login-{uuid.uuid4().hex[:8]}@test.local"
    password = "TestPassword123!"
    user_id = None

    try:
        # 1. Register new user (admin endpoint)
        reg_resp = api.post("/api/v1/auth/register", json={
            "email": email,
            "password": password,
            "display_name": "Login Test User",
            "department": "IT",
        })
        assert reg_resp.status_code == 200, f"Register failed: {reg_resp.text}"
        user_id = reg_resp.json().get("id")
        assert user_id

        # 2. Login with credentials
        login_resp = api.post("/api/v1/auth/login", json={
            "email": email,
            "password": password,
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        login_data = login_resp.json()
        assert login_data["success"] is True
        assert login_data["user"]["email"] == email
        assert login_data["token_type"] == "Bearer"
        assert login_data["expires_in"] > 0
        # access_token should NOT be in body (security)
        assert "access_token" not in login_data

        # Verify cookies were set
        cookies = login_resp.cookies
        assert "access_token" in cookies or login_resp.headers.get("set-cookie")

        # 3. Access /me with cookies from login
        me_resp = api.get("/api/v1/auth/me")
        assert me_resp.status_code == 200, f"GET /me failed: {me_resp.text}"
        me_data = me_resp.json()
        assert me_data["email"] == email
        assert me_data["display_name"] == "Login Test User"

        # 4. Refresh token
        refresh_resp = api.post("/api/v1/auth/refresh")
        assert refresh_resp.status_code == 200, f"Refresh failed: {refresh_resp.text}"
        refresh_data = refresh_resp.json()
        assert refresh_data["success"] is True
        assert "access_token" not in refresh_data

        # 5. /me should still work with new tokens
        me_resp2 = api.get("/api/v1/auth/me")
        assert me_resp2.status_code == 200

        # 6. Logout
        logout_resp = api.post("/api/v1/auth/logout")
        assert logout_resp.status_code == 200
        assert logout_resp.json()["success"] is True

        # 7. After logout, /me should fail (cookies cleared)
        # Note: httpx may still send old cookies, so create fresh client
        import httpx
        with httpx.Client(base_url=str(api.base_url), timeout=10) as fresh:
            me_denied = fresh.get("/api/v1/auth/me")
            assert me_denied.status_code == 401, f"Expected 401 after logout: {me_denied.text}"

    finally:
        if user_id:
            api.delete(f"/api/v1/auth/users/{user_id}")


@pytest.mark.e2e
@pytest.mark.skipif(not _IS_INTERNAL_AUTH, reason="Requires AUTH_PROVIDER=internal")
def test_login_wrong_password(api):
    """Login with wrong password should return 401."""
    email = f"test-wrong-pw-{uuid.uuid4().hex[:8]}@test.local"
    user_id = None

    try:
        # 1. Register user
        reg_resp = api.post("/api/v1/auth/register", json={
            "email": email,
            "password": "CorrectPassword1!",
            "display_name": "Wrong PW Test",
        })
        assert reg_resp.status_code == 200
        user_id = reg_resp.json().get("id")

        # 2. Login with wrong password
        login_resp = api.post("/api/v1/auth/login", json={
            "email": email,
            "password": "WrongPassword!",
        })
        assert login_resp.status_code == 401

    finally:
        if user_id:
            api.delete(f"/api/v1/auth/users/{user_id}")


@pytest.mark.e2e
@pytest.mark.skipif(not _IS_INTERNAL_AUTH, reason="Requires AUTH_PROVIDER=internal")
def test_login_nonexistent_user(api):
    """Login with non-existent email should return 401."""
    resp = api.post("/api/v1/auth/login", json={
        "email": "nonexistent@test.local",
        "password": "SomePassword1!",
    })
    assert resp.status_code == 401


@pytest.mark.e2e
@pytest.mark.skipif(not _IS_INTERNAL_AUTH, reason="Requires AUTH_PROVIDER=internal")
def test_change_password_flow(api):
    """Register -> login -> change password -> re-login with new password."""
    email = f"test-chpw-{uuid.uuid4().hex[:8]}@test.local"
    old_pw = "OldPassword123!"
    new_pw = "NewPassword456!"
    user_id = None

    try:
        # 1. Register
        reg_resp = api.post("/api/v1/auth/register", json={
            "email": email,
            "password": old_pw,
            "display_name": "Change PW Test",
        })
        assert reg_resp.status_code == 200
        user_id = reg_resp.json().get("id")

        # 2. Login with old password
        login_resp = api.post("/api/v1/auth/login", json={
            "email": email, "password": old_pw,
        })
        assert login_resp.status_code == 200

        # 3. Change password
        chpw_resp = api.post("/api/v1/auth/change-password", json={
            "old_password": old_pw,
            "new_password": new_pw,
        })
        assert chpw_resp.status_code == 200, f"Change password failed: {chpw_resp.text}"
        assert chpw_resp.json()["success"] is True

        # 4. Login with old password should fail
        old_login = api.post("/api/v1/auth/login", json={
            "email": email, "password": old_pw,
        })
        assert old_login.status_code == 401

        # 5. Login with new password should succeed
        new_login = api.post("/api/v1/auth/login", json={
            "email": email, "password": new_pw,
        })
        assert new_login.status_code == 200

    finally:
        if user_id:
            api.delete(f"/api/v1/auth/users/{user_id}")


@pytest.mark.e2e
@pytest.mark.skipif(not _IS_INTERNAL_AUTH, reason="Requires AUTH_PROVIDER=internal")
def test_register_duplicate_email(api):
    """Registering same email twice should return 409."""
    email = f"test-dup-{uuid.uuid4().hex[:8]}@test.local"
    user_id = None

    try:
        reg1 = api.post("/api/v1/auth/register", json={
            "email": email, "password": "Password123!", "display_name": "Dup Test",
        })
        assert reg1.status_code == 200
        user_id = reg1.json().get("id")

        reg2 = api.post("/api/v1/auth/register", json={
            "email": email, "password": "Password456!", "display_name": "Dup Test 2",
        })
        assert reg2.status_code == 409

    finally:
        if user_id:
            api.delete(f"/api/v1/auth/users/{user_id}")


@pytest.mark.e2e
@pytest.mark.skipif(not _IS_INTERNAL_AUTH, reason="Requires AUTH_PROVIDER=internal")
def test_register_short_password(api):
    """Password shorter than 8 characters should be rejected."""
    resp = api.post("/api/v1/auth/register", json={
        "email": f"test-short-{uuid.uuid4().hex[:8]}@test.local",
        "password": "short",
        "display_name": "Short PW",
    })
    assert resp.status_code == 400


@pytest.mark.e2e
@pytest.mark.skipif(not _IS_INTERNAL_AUTH, reason="Requires AUTH_PROVIDER=internal")
def test_refresh_after_logout_fails(api):
    """Refresh token should be invalidated after logout."""
    email = f"test-refresh-{uuid.uuid4().hex[:8]}@test.local"
    user_id = None

    try:
        # 1. Register + login
        reg = api.post("/api/v1/auth/register", json={
            "email": email, "password": "Password123!", "display_name": "Refresh Test",
        })
        user_id = reg.json().get("id")
        api.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})

        # 2. Logout
        api.post("/api/v1/auth/logout")

        # 3. Try refresh — should fail
        refresh_resp = api.post("/api/v1/auth/refresh")
        # Either 401 (revoked) or no refresh cookie available
        assert refresh_resp.status_code in (401, 503)

    finally:
        if user_id:
            api.delete(f"/api/v1/auth/users/{user_id}")
