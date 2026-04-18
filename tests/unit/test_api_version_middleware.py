"""Tests for API deprecation header middleware."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.middleware.api_version import (
    add_deprecation_headers,
    clear_deprecations,
    deprecate,
)


@pytest.fixture
def app() -> FastAPI:
    clear_deprecations()
    application = FastAPI()
    application.add_middleware(BaseHTTPMiddleware, dispatch=add_deprecation_headers)

    @application.get("/api/v1/legacy/search")
    async def legacy() -> dict:
        return {"ok": True}

    @application.get("/api/v1/active/foo")
    async def active() -> dict:
        return {"ok": True}

    @application.get("/api/v2/search")
    async def v2_search() -> dict:
        return {"ok": True}

    return application


def test_deprecated_route_gets_headers(app: FastAPI) -> None:
    deprecate("/api/v1/legacy", sunset="2026-12-31", successor="/api/v2/search")
    client = TestClient(app)
    resp = client.get("/api/v1/legacy/search")
    assert resp.status_code == 200
    assert resp.headers["deprecation"] == "true"
    assert "31 Dec 2026" in resp.headers["sunset"]
    assert resp.headers["link"] == '</api/v2/search>; rel="successor-version"'


def test_undeprecated_route_unchanged(app: FastAPI) -> None:
    deprecate("/api/v1/legacy", sunset="2026-12-31")
    client = TestClient(app)
    resp = client.get("/api/v1/active/foo")
    assert resp.status_code == 200
    assert "deprecation" not in resp.headers
    assert "sunset" not in resp.headers


def test_v2_route_unaffected(app: FastAPI) -> None:
    deprecate("/api/v1", sunset="2026-12-31")
    client = TestClient(app)
    resp = client.get("/api/v2/search")
    assert "deprecation" not in resp.headers


def test_longest_prefix_wins(app: FastAPI) -> None:
    deprecate("/api/v1", sunset="2026-06-30", successor="/api/v2")
    deprecate("/api/v1/legacy", sunset="2026-12-31", successor="/api/v2/search")
    client = TestClient(app)
    resp = client.get("/api/v1/legacy/search")
    assert "31 Dec 2026" in resp.headers["sunset"]  # specific match wins


def test_note_header_optional(app: FastAPI) -> None:
    deprecate("/api/v1/legacy", sunset="2026-12-31", note="confidence type changed")
    client = TestClient(app)
    resp = client.get("/api/v1/legacy/search")
    assert resp.headers["x-api-deprecation-note"] == "confidence type changed"


def test_sunset_passthrough_for_imf_fixdate(app: FastAPI) -> None:
    deprecate("/api/v1/legacy", sunset="Wed, 31 Dec 2026 00:00:00 GMT")
    client = TestClient(app)
    resp = client.get("/api/v1/legacy/search")
    assert resp.headers["sunset"] == "Wed, 31 Dec 2026 00:00:00 GMT"
