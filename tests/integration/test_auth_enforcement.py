"""Integration test — verify every protected route returns 401 without a token.

Run requirements:
  - FastAPI server running at TEST_API_URL (default http://localhost:8000)
  - AUTH_ENABLED=true on the server (dev default is false → tests are skipped)

This is the SSOT smoke test for B-0 Day 2: middleware-level enforcement.
We sample one endpoint per high-risk router rather than every route, so the
test stays fast and signals the refactor is alive end-to-end. Per-route
permission checks (Day 4) live in test_permission_matrix.py.
"""

from __future__ import annotations

import httpx
import pytest

# Sample of well-known protected routes — one per major domain.
# Format: (method, path, payload_or_None).
PROTECTED_ROUTES: list[tuple[str, str, dict | None]] = [
    ("GET", "/api/v1/auth/me", None),
    ("GET", "/api/v1/kb", None),
    ("POST", "/api/v1/search/hub", {"query": "x"}),
    ("POST", "/api/v1/agentic/ask", {"query": "x"}),
    ("GET", "/api/v1/admin/glossary", None),
    ("GET", "/api/v1/quality/runs", None),
    ("GET", "/api/v1/admin/data-sources", None),
]

# Public — must always be reachable without a token.
PUBLIC_ROUTES: list[tuple[str, str]] = [
    ("GET", "/health"),
    ("GET", "/openapi.json"),
]


def _auth_disabled_on_server(api: httpx.Client) -> bool:
    """Heuristic: if /api/v1/kb returns 200 without a token, AUTH_ENABLED is false."""
    try:
        r = api.get("/api/v1/kb", timeout=5)
        return r.status_code == 200
    except httpx.RequestError:
        return False


@pytest.fixture
def auth_required(api: httpx.Client) -> bool:
    """Skip enforcement tests when the server is in AUTH_ENABLED=false dev mode."""
    if _auth_disabled_on_server(api):
        pytest.skip(
            "Server is in AUTH_ENABLED=false dev mode — set AUTH_ENABLED=true "
            "to run enforcement integration tests."
        )
    return True


@pytest.mark.parametrize("method,path,payload", PROTECTED_ROUTES)
def test_protected_route_without_token_returns_401(
    api: httpx.Client, auth_required: bool,
    method: str, path: str, payload: dict | None,
) -> None:
    if method == "GET":
        r = api.get(path)
    else:
        r = api.post(path, json=payload or {})
    assert r.status_code == 401, (
        f"{method} {path} should be 401 without token, got {r.status_code}: {r.text[:200]}"
    )


@pytest.mark.parametrize("method,path,payload", PROTECTED_ROUTES)
def test_protected_route_with_invalid_token_returns_401(
    api: httpx.Client, auth_required: bool,
    method: str, path: str, payload: dict | None,
) -> None:
    headers = {"Authorization": "Bearer not-a-real-token"}
    if method == "GET":
        r = api.get(path, headers=headers)
    else:
        r = api.post(path, headers=headers, json=payload or {})
    assert r.status_code == 401


@pytest.mark.parametrize("method,path", PUBLIC_ROUTES)
def test_public_route_reachable_without_token(
    api: httpx.Client, method: str, path: str,
) -> None:
    if method == "GET":
        r = api.get(path)
    else:
        r = api.post(path, json={})
    # Health / docs / openapi must not 401 even when auth is on
    assert r.status_code != 401, (
        f"Public {method} {path} returned 401 — middleware whitelist may be wrong"
    )


def test_login_endpoint_reachable_without_token(api: httpx.Client) -> None:
    """Login is the entry point — must accept anonymous traffic to issue a token."""
    r = api.post("/api/v1/auth/login", json={"email": "x@x", "password": "wrong"})
    # 401 is fine (bad credentials) — what we want NOT to see is "Missing
    # authentication token", which would indicate the login endpoint itself
    # got auth-walled and nobody can ever sign in.
    assert r.status_code in (200, 400, 401, 422)
    if r.status_code == 401:
        assert "Missing authentication token" not in r.text
