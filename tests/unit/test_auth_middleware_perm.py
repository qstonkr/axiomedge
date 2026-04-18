"""Middleware-level permission enforcement tests (B-0 Day 4).

Mounts AuthMiddleware on a tiny FastAPI app, fakes an auth_provider that
returns users with the requested role, and verifies that the matrix
returns 200 for permitted role/endpoint pairs and 403 for the rest.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth import dependencies as deps
from src.auth import middleware as mw
from src.auth.middleware import AuthMiddleware
from src.auth.providers import AuthUser
from src.auth.rbac import RBACEngine


def _user(role: str) -> AuthUser:
    return AuthUser(
        sub=f"u-{role}",
        email=f"{role}@test",
        display_name=role,
        provider="internal",
        roles=[role],
        active_org_id="org-1",
    )


def _app_with_role(role: str) -> tuple[FastAPI, dict[str, Any]]:
    """Build a minimal app where every request is authenticated as ``role``."""
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    user = _user(role)
    auth_provider = MagicMock()
    auth_provider.verify_token = AsyncMock(return_value=user)

    auth_service = MagicMock()
    auth_service.get_user_roles = AsyncMock(return_value=[{"role": role}])

    state = {
        "auth_provider": auth_provider,
        "auth_service": auth_service,
        "rbac_engine": RBACEngine(),
    }
    app.state._app_state = state

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def echo(full_path: str) -> dict:
        return {"path": full_path}

    return app, state


# Representative endpoints the matrix covers, expressed as (method, path).
# Each row specifies the expected outcome per role.
# Format: (method, path, {role: expected_status})
MATRIX_CASES: list[tuple[str, str, dict[str, int]]] = [
    # KB lifecycle
    (
        "POST", "/api/v1/admin/kb",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 403, "VIEWER": 403},
    ),
    (
        "DELETE", "/api/v1/admin/kb/abc",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 403, "VIEWER": 403},
    ),
    (
        "GET", "/api/v1/admin/kb",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 200, "VIEWER": 200},
    ),
    # Document — MEMBER can ingest (per matrix), VIEWER cannot.
    (
        "POST", "/api/v1/knowledge/ingest",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 200, "VIEWER": 403},
    ),
    # Glossary
    (
        "POST", "/api/v1/admin/glossary",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 200, "VIEWER": 403},
    ),
    (
        "GET", "/api/v1/admin/glossary",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 200, "VIEWER": 200},
    ),
    # Search
    (
        "POST", "/api/v1/search/hub",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 200, "VIEWER": 200},
    ),
    # Agentic
    (
        "POST", "/api/v1/agentic/ask",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 200, "VIEWER": 200},
    ),
    # Quality
    (
        "POST", "/api/v1/admin/golden-set",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 403, "VIEWER": 403},
    ),
    (
        "GET", "/api/v1/admin/golden-set",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 200, "VIEWER": 403},
    ),
    # Data sources
    (
        "GET", "/api/v1/admin/data-sources",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 403, "VIEWER": 403},
    ),
    # Distill
    (
        "POST", "/api/v1/distill/builds",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 403, "VIEWER": 403},
    ),
    # Org-only (billing/destroy)
    (
        "PUT", "/api/v1/admin/config/weights",
        {"OWNER": 200, "ADMIN": 403, "MEMBER": 403, "VIEWER": 403},
    ),
    # Search analytics
    (
        "GET", "/api/v1/admin/search/history",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 403, "VIEWER": 403},
    ),
    # Auth introspection — auth-only sentinel; every role gets through.
    (
        "GET", "/api/v1/auth/me",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 200, "VIEWER": 200},
    ),
    # Auth admin
    (
        "POST", "/api/v1/auth/users",
        {"OWNER": 200, "ADMIN": 200, "MEMBER": 403, "VIEWER": 403},
    ),
]


@pytest.mark.parametrize("method,path,expected_by_role", MATRIX_CASES)
def test_role_matrix(
    monkeypatch: pytest.MonkeyPatch,
    method: str, path: str, expected_by_role: dict[str, int],
) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)
    monkeypatch.setattr(mw, "AUTH_ENABLED", True)

    for role, expected_status in expected_by_role.items():
        app, _state = _app_with_role(role)
        client = TestClient(app)

        resp = client.request(
            method, path,
            headers={"Authorization": "Bearer good"},
            json={} if method in {"POST", "PUT", "PATCH"} else None,
        )

        assert resp.status_code == expected_status, (
            f"role={role} {method} {path}: expected {expected_status}, "
            f"got {resp.status_code} ({resp.text[:200]})"
        )
