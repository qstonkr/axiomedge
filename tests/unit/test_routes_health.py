"""Unit tests for src/api/routes/health.py."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Import app first to resolve circular imports, then routes
import src.api.app  # noqa: F401
from src.api.routes import health


def _make_test_app():
    app = FastAPI()
    app.include_router(health.router)
    return app


class TestHealthEndpoint:
    def test_health_all_services_down(self):
        """When no services are in state, health returns degraded."""
        from src.api.state import AppState

        mock_state = AppState()  # All None

        with patch.object(health, "_get_state", return_value=mock_state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/health")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["status"] == "degraded"
                    assert data["checks"]["qdrant"] is False
                    assert data["checks"]["embedding"] is False
                    assert data["checks"]["neo4j"] is False
                    assert data["checks"]["llm"] is False
                    assert data["checks"]["redis"] is False
                    assert data["checks"]["database"] is False

            asyncio.run(_run())

    def test_health_qdrant_and_embedding_up(self):
        """When qdrant and embedding are healthy, status is healthy."""
        from src.api.state import AppState

        mock_state = AppState()

        # Mock qdrant provider
        mock_client = AsyncMock()
        mock_client.get_collections = AsyncMock(return_value=[])
        mock_provider = AsyncMock()
        mock_provider.ensure_client = AsyncMock(return_value=mock_client)
        mock_state["qdrant_provider"] = mock_provider

        # Mock embedder
        mock_embedder = MagicMock()
        mock_embedder.is_ready.return_value = True
        mock_state["embedder"] = mock_embedder

        # Mock db session factory
        mock_state["db_session_factory"] = MagicMock()

        with patch.object(health, "_get_state", return_value=mock_state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/health")
                    data = resp.json()
                    assert data["status"] == "healthy"
                    assert data["checks"]["qdrant"] is True
                    assert data["checks"]["embedding"] is True
                    assert data["checks"]["database"] is True

            asyncio.run(_run())

    def test_health_qdrant_throws_exception(self):
        """When qdrant throws, check is False but endpoint succeeds."""
        from src.api.state import AppState

        mock_state = AppState()
        mock_provider = AsyncMock()
        mock_provider.ensure_client = AsyncMock(side_effect=Exception("connection refused"))
        mock_state["qdrant_provider"] = mock_provider

        with patch.object(health, "_get_state", return_value=mock_state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/health")
                    data = resp.json()
                    assert resp.status_code == 200
                    assert data["checks"]["qdrant"] is False

            asyncio.run(_run())
