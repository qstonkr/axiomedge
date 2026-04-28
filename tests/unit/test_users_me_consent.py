"""POST/GET /api/v1/users/me/consent — server-side PIPA legal trail."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import app as fastapi_app
from src.auth.dependencies import OrgContext, get_current_org, get_current_user
from src.auth.providers import AuthUser


USER_ID = "11111111-1111-1111-1111-111111111111"
ORG_ID = "default-org"


@pytest.fixture
def fake_user():
    return AuthUser(
        sub=USER_ID, email="x@y.com", display_name="X",
        provider="internal", roles=["admin"], active_org_id=ORG_ID,
    )


@pytest.fixture
def fake_org():
    return OrgContext(id=ORG_ID, user_role_in_org="OWNER")


@pytest.fixture
def consent_repo():
    repo = AsyncMock()
    repo.accept.return_value = MagicMock(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        user_id=uuid.UUID(USER_ID),
        org_id=ORG_ID,
        policy_version="v1",
        accepted_at=datetime.now(UTC),
        ip_address="127.0.0.1",
        user_agent="curl/8",
    )
    repo.get_for_user.return_value = None
    return repo


@pytest.fixture
def client(fake_user, fake_org, consent_repo, monkeypatch):
    monkeypatch.setattr("src.auth.middleware.AUTH_ENABLED", False)
    monkeypatch.setattr("src.auth.middleware._ANONYMOUS_USER", fake_user)
    fastapi_app.dependency_overrides[get_current_user] = lambda: fake_user
    fastapi_app.dependency_overrides[get_current_org] = lambda: fake_org

    from src.api.app import _state as real_state
    monkeypatch.setattr(real_state, "consent_repo", consent_repo)
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def test_post_consent_records_acceptance(client, consent_repo):
    res = client.post("/api/v1/users/me/consent", json={"policy_version": "v1"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["policy_version"] == "v1"
    assert body["accepted_at"]
    consent_repo.accept.assert_awaited_once()
    kwargs = consent_repo.accept.await_args.kwargs
    assert kwargs["user_id"] == uuid.UUID(USER_ID)
    assert kwargs["policy_version"] == "v1"


def test_post_consent_idempotent_repeat(client, consent_repo):
    """Server-side accept is idempotent — repo.accept handles upsert."""
    client.post("/api/v1/users/me/consent", json={"policy_version": "v1"})
    res2 = client.post("/api/v1/users/me/consent", json={"policy_version": "v1"})
    assert res2.status_code == 201
    assert consent_repo.accept.await_count == 2  # endpoint always calls repo


def test_get_consent_returns_null_when_missing(client, consent_repo):
    consent_repo.get_for_user.return_value = None
    res = client.get("/api/v1/users/me/consent")
    assert res.status_code == 200
    assert res.json() is None


def test_get_consent_returns_record_when_present(client, consent_repo):
    consent_repo.get_for_user.return_value = MagicMock(
        policy_version="v1",
        accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    res = client.get("/api/v1/users/me/consent")
    assert res.status_code == 200
    body = res.json()
    assert body["policy_version"] == "v1"
    assert body["accepted_at"].startswith("2026-01-01")
