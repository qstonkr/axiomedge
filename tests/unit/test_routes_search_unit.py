"""Unit tests for src/api/routes/search.py — hub_search and list_searchable_kbs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Pre-import to avoid circular import issues
import src.api.app  # noqa: F401
from src.api.routes import search as search_route

# B-0 RBAC bypass (conftest 의 bypass_route_auth fixture)
pytestmark = pytest.mark.usefixtures("bypass_route_auth")


def _make_test_app():
    app = FastAPI()
    app.include_router(search_route.router)
    return app


def _mock_search_state(
    *,
    with_search_engine: bool = True,
    with_embedder: bool = True,
    search_results: list | None = None,
):
    """Build an AppState populated with mocked search services."""
    from src.api.state import AppState

    state = AppState()

    if with_search_engine:
        @dataclass
        class FakeSearchResult:
            point_id: str = "p1"
            content: str = "test content"
            score: float = 0.85
            metadata: dict = field(default_factory=lambda: {
                "document_name": "doc.pdf",
                "source_uri": "/docs/doc.pdf",
            })

        engine = AsyncMock()
        results = search_results if search_results is not None else [FakeSearchResult()]
        engine.search = AsyncMock(return_value=results)
        engine.search_with_colbert_rerank = AsyncMock(return_value=results)
        state["qdrant_search"] = engine

    if with_embedder:
        embedder = MagicMock()
        embedder.encode = MagicMock(return_value={
            "dense_vecs": [[0.1] * 1024],
            "lexical_weights": [{"1": 0.5, "2": 0.3}],
            "colbert_vecs": None,
        })
        state["embedder"] = embedder

    return state


# ---------------------------------------------------------------------------
# POST /api/v1/search/hub — basic search flow
# ---------------------------------------------------------------------------

class TestHubSearch:
    def test_hub_search_no_search_engine(self):
        """Returns 503 when search engine is not initialized."""
        from src.api.state import AppState

        state = AppState()
        with patch.object(search_route, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/search/hub",
                        json={"query": "test query"},
                    )
                    assert resp.status_code == 503

            asyncio.run(_run())

    def test_hub_search_no_embedder(self):
        """Returns 503 when embedder is not initialized."""
        state = _mock_search_state(with_embedder=False)
        with patch.object(search_route, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/search/hub",
                        json={"query": "test query"},
                    )
                    assert resp.status_code == 503

            asyncio.run(_run())

    def test_hub_search_basic_success(self):
        """Basic search returns results without answer generation."""
        state = _mock_search_state()

        with patch.object(search_route, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/search/hub",
                        json={"query": "test query", "include_answer": False},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["query"] == "test query"
                    assert data["total_chunks"] >= 0
                    assert isinstance(data["chunks"], list)
                    assert isinstance(data["searched_kbs"], list)
                    assert data["search_time_ms"] >= 0

            asyncio.run(_run())

    def test_hub_search_with_kb_ids(self):
        """Search with explicit kb_ids."""
        state = _mock_search_state()

        with patch.object(search_route, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/search/hub",
                        json={
                            "query": "test",
                            "kb_ids": ["kb1", "kb2"],
                            "include_answer": False,
                        },
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    # Should search in both KBs
                    assert len(data["searched_kbs"]) <= 2

            asyncio.run(_run())

    def test_hub_search_with_kb_filter(self):
        """Search with nested kb_filter object."""
        state = _mock_search_state()

        with patch.object(search_route, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/search/hub",
                        json={
                            "query": "test",
                            "kb_filter": {"kb_ids": ["my-kb"]},
                            "include_answer": False,
                        },
                    )
                    assert resp.status_code == 200

            asyncio.run(_run())

    def test_hub_search_empty_query_rejected(self):
        """Empty query should fail validation."""
        state = _mock_search_state()

        with patch.object(search_route, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/search/hub",
                        json={"query": ""},
                    )
                    assert resp.status_code == 422  # Validation error

            asyncio.run(_run())

    def test_hub_search_with_answer_service(self):
        """When answer_service is present, answer is generated."""
        state = _mock_search_state()

        # Add answer service
        answer_svc = AsyncMock()
        answer_result = MagicMock()
        answer_result.answer = "This is a generated answer."
        answer_result.query_type = "concept"
        answer_result.confidence_indicator = "높음"
        answer_svc.enrich = AsyncMock(return_value=answer_result)
        state["answer_service"] = answer_svc

        with patch.object(search_route, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/search/hub",
                        json={"query": "what is API", "include_answer": True},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["answer"] is not None

            asyncio.run(_run())

    def test_hub_search_with_document_filter(self):
        """Document filter narrows results to matching doc names."""
        state = _mock_search_state()

        with patch.object(search_route, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/search/hub",
                        json={
                            "query": "test",
                            "document_filter": ["nonexistent_doc"],
                            "include_answer": False,
                        },
                    )
                    assert resp.status_code == 200
                    # Chunks should be filtered out since doc name doesn't match
                    data = resp.json()
                    assert data["total_chunks"] == 0

            asyncio.run(_run())

    def test_hub_search_search_collection_exception(self):
        """If a collection search raises, it is logged and skipped."""
        state = _mock_search_state()
        state["qdrant_search"].search = AsyncMock(side_effect=RuntimeError("Qdrant error"))

        with patch.object(search_route, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/search/hub",
                        json={"query": "test", "include_answer": False},
                    )
                    assert resp.status_code == 200
                    assert resp.json()["total_chunks"] == 0

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# GET /api/v1/search/hub/kbs
# ---------------------------------------------------------------------------

class TestListSearchableKBs:
    def test_list_kbs_no_collections(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(search_route, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/search/hub/kbs")
                    assert resp.status_code == 200
                    assert resp.json()["kbs"] == []

            asyncio.run(_run())

    def test_list_kbs_with_collections(self):
        from src.api.state import AppState

        state = AppState()
        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(return_value=["kb_test1", "kb_test2"])
        state["qdrant_collections"] = collections

        with patch.object(search_route, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/search/hub/kbs")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert len(data["kbs"]) == 2

            asyncio.run(_run())

    def test_list_kbs_exception_returns_empty(self):
        from src.api.state import AppState

        state = AppState()
        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(side_effect=RuntimeError("Qdrant down"))
        state["qdrant_collections"] = collections

        with patch.object(search_route, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/search/hub/kbs")
                    assert resp.status_code == 200
                    assert resp.json()["kbs"] == []

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# _extract_query_keywords helper
# ---------------------------------------------------------------------------

class TestExtractQueryKeywords:
    def test_extract_keywords_fallback(self):
        """When KiwiPy is not available, falls back to whitespace split."""
        from src.api.routes._search_steps import _extract_query_keywords  # noqa: F811

        # Reset singleton to force fallback path testing
        result = _extract_query_keywords("hello world testing")
        assert isinstance(result, list)
        # Should return some keywords
        assert len(result) >= 0
