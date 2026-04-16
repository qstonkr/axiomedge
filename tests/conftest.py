"""Shared test fixtures for knowledge-local.

pytest-asyncio auto mode is configured in pyproject.toml.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

# Dashboard tests need dashboard dir on sys.path for `from services.xxx` imports
_dashboard_dir = str(Path(__file__).resolve().parents[1] / "src" / "apps" / "dashboard")
if _dashboard_dir not in sys.path:
    sys.path.insert(0, _dashboard_dir)
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Settings cache reset — ensures monkeypatched env vars take effect
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Clear get_settings() LRU cache so env var changes take effect per test."""
    from src.config import reset_settings
    reset_settings()
    yield
    reset_settings()


# ---------------------------------------------------------------------------
# Mock embedder
# ---------------------------------------------------------------------------

class MockEmbedder:
    """Fake embedding provider returning fixed-dimension zero vectors."""

    def __init__(self, dimension: int = 1024) -> None:
        self.dimension = dimension

    def is_ready(self) -> bool:
        return True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dimension for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * self.dimension


@pytest.fixture
def mock_embedder() -> MockEmbedder:
    return MockEmbedder()


# ---------------------------------------------------------------------------
# Mock vector store
# ---------------------------------------------------------------------------

class MockVectorStore:
    """In-memory vector store stub for unit tests."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    async def upsert(self, collection: str, points: list[dict]) -> int:
        for p in points:
            self._data[p["id"]] = p
        return len(points)

    async def search(
        self, collection: str, vector: list[float], top_k: int = 5
    ) -> list[dict]:
        # Return first top_k items (no actual similarity computation)
        items = list(self._data.values())[:top_k]
        return items

    async def delete(self, collection: str, ids: list[str]) -> int:
        removed = 0
        for _id in ids:
            if _id in self._data:
                del self._data[_id]
                removed += 1
        return removed


@pytest.fixture
def mock_vector_store() -> MockVectorStore:
    return MockVectorStore()


# ---------------------------------------------------------------------------
# Mock graph store
# ---------------------------------------------------------------------------

class MockGraphStore:
    """In-memory graph store stub."""

    def __init__(self) -> None:
        self._nodes: dict[str, dict] = {}
        self._edges: list[tuple[str, str, str]] = []

    async def add_node(self, node_id: str, labels: list[str], properties: dict) -> None:
        self._nodes[node_id] = {"labels": labels, "properties": properties}

    async def add_edge(self, from_id: str, to_id: str, rel_type: str) -> None:
        self._edges.append((from_id, to_id, rel_type))

    async def query(self, cypher: str, **params: Any) -> list[dict]:
        return []


@pytest.fixture
def mock_graph_store() -> MockGraphStore:
    return MockGraphStore()


# ---------------------------------------------------------------------------
# FastAPI TestClient (async)
# ---------------------------------------------------------------------------

@pytest.fixture
async def client():
    """Async test client that skips full lifespan initialization.

    Creates the FastAPI app with lifespan disabled to avoid
    requiring real Qdrant/Neo4j/Postgres connections.
    """
    from fastapi import FastAPI

    # Import routes individually to avoid full _init_services
    from src.api.routes import health, jobs as jobs_route

    test_app = FastAPI()
    test_app.include_router(health.router)
    test_app.include_router(jobs_route.router)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
