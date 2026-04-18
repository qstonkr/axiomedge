"""Cross-tenant access blocking — B-0 Day 3 SSOT.

Smoke-tests that an authenticated user from organization A cannot see, fetch,
or operate on a KB owned by organization B. Skipped unless the API is running
with AUTH_ENABLED=true and two test users (a@test/b@test, password "test")
have been provisioned in distinct orgs.

Tests are written against ``httpx`` so they exercise the real middleware,
dependency chain, and DB filters — no mocks.
"""

from __future__ import annotations

import os

import httpx
import pytest

API_URL = os.getenv("TEST_API_URL", "http://localhost:8000")


def _login(api: httpx.Client, email: str, password: str) -> str | None:
    """Try to obtain an access token; return None if creds are unknown."""
    try:
        r = api.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            timeout=10,
        )
    except httpx.RequestError:
        return None
    if r.status_code != 200:
        return None
    return r.json().get("access_token")


@pytest.fixture
def org_a_token(api: httpx.Client) -> str:
    token = _login(api, "a@test.local", "test1234!")
    if not token:
        pytest.skip(
            "Cross-tenant tests require two seeded users — see "
            "docs/RBAC.md for the seed script. AUTH_ENABLED also has to be true."
        )
    return token


@pytest.fixture
def org_b_token(api: httpx.Client) -> str:
    token = _login(api, "b@test.local", "test1234!")
    if not token:
        pytest.skip("Second test user (b@test.local) not provisioned — see docs/RBAC.md.")
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_kb_list_only_returns_callers_org(
    api: httpx.Client, org_a_token: str, org_b_token: str,
) -> None:
    """Each user should see only KBs that belong to their own org."""
    a = api.get("/api/v1/admin/kb", headers=_bearer(org_a_token))
    b = api.get("/api/v1/admin/kb", headers=_bearer(org_b_token))
    assert a.status_code == 200
    assert b.status_code == 200

    ids_a = {kb.get("kb_id") for kb in a.json().get("kbs", [])}
    ids_b = {kb.get("kb_id") for kb in b.json().get("kbs", [])}

    # The two orgs must not see each other's KBs.
    assert ids_a.isdisjoint(ids_b), (
        f"Cross-tenant leak: org A and org B both saw {ids_a & ids_b}"
    )


def test_get_foreign_kb_returns_404(
    api: httpx.Client, org_a_token: str, org_b_token: str,
) -> None:
    """Picking any KB visible to B and requesting it as A must 404 (no leak)."""
    b_kbs = api.get("/api/v1/admin/kb", headers=_bearer(org_b_token)).json().get("kbs", [])
    if not b_kbs:
        pytest.skip("Org B has no KBs to attempt cross-tenant access against.")

    foreign_kb = b_kbs[0]["kb_id"]
    r = api.get(
        f"/api/v1/admin/kb/{foreign_kb}",
        headers=_bearer(org_a_token),
    )
    assert r.status_code == 404, (
        f"Cross-tenant fetch should 404 (existence not leaked), got {r.status_code}"
    )


def test_search_does_not_return_foreign_kb_chunks(
    api: httpx.Client, org_a_token: str, org_b_token: str,
) -> None:
    """A broad query as A must not include any chunk whose kb_id belongs to B."""
    b_kbs = api.get("/api/v1/admin/kb", headers=_bearer(org_b_token)).json().get("kbs", [])
    if not b_kbs:
        pytest.skip("Org B has no KBs to attempt cross-tenant search leak against.")
    b_kb_ids = {kb["kb_id"] for kb in b_kbs}

    r = api.post(
        "/api/v1/search/hub",
        json={"query": "공지", "top_k": 20, "include_answer": False},
        headers=_bearer(org_a_token),
    )
    if r.status_code == 503:
        pytest.skip("Search engine not initialized on test server.")
    assert r.status_code == 200

    chunk_kb_ids = {c.get("kb_id") for c in r.json().get("chunks", [])}
    leaked = chunk_kb_ids & b_kb_ids
    assert not leaked, f"Search returned chunks from foreign KBs: {leaked}"


def test_agentic_ask_scoped_to_callers_org(
    api: httpx.Client, org_a_token: str,
) -> None:
    """Agentic /ask must succeed (or fail gracefully) — never 500 from
    missing org context now that the route requires get_current_org."""
    r = api.post(
        "/api/v1/agentic/ask",
        json={"query": "안녕"},
        headers=_bearer(org_a_token),
        timeout=120,
    )
    # 200 = succeeded; 503 = LLM not configured on test box; either is acceptable.
    # 401/403/409 would indicate the dependency chain rejected a valid token,
    # which would be a regression.
    assert r.status_code in (200, 503), (
        f"Unexpected status {r.status_code}: {r.text[:200]}"
    )
