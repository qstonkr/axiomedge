"""Unit tests for src/api/routes/glossary.py — key endpoints with mocked repo."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Pre-import to avoid circular import issues
import src.api.app  # noqa: F401
from src.api.routes import glossary


def _make_test_app():
    app = FastAPI()
    app.include_router(glossary.router)
    return app


def _mock_state_with_repo(repo=None):
    """Create an AppState with a mocked glossary_repo."""
    from src.api.state import AppState

    state = AppState()
    state["glossary_repo"] = repo
    return state


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary — list terms
# ---------------------------------------------------------------------------

class TestListGlossaryTerms:
    def test_list_terms_with_repo(self):
        repo = AsyncMock()
        repo.list_by_kb = AsyncMock(return_value=[
            {"id": "1", "term": "API", "kb_id": "all", "status": "approved"},
            {"id": "2", "term": "SDK", "kb_id": "all", "status": "pending"},
        ])
        repo.count_by_kb = AsyncMock(return_value=2)

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["total"] == 2
                    assert len(data["terms"]) == 2
                    assert data["terms"][0]["term"] == "API"

            asyncio.run(_run())

    def test_list_terms_no_repo(self):
        """Without glossary_repo, returns empty list."""
        from src.api.state import AppState

        state = AppState()
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["terms"] == []
                    assert data["total"] == 0

            asyncio.run(_run())

    def test_list_terms_with_filters(self):
        """Query params (status, scope, page) are forwarded to repo."""
        repo = AsyncMock()
        repo.list_by_kb = AsyncMock(return_value=[])
        repo.count_by_kb = AsyncMock(return_value=0)

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get(
                        "/api/v1/admin/glossary",
                        params={"status": "approved", "scope": "global", "page": 2, "page_size": 50},
                    )
                    assert resp.status_code == 200
                    # Verify repo was called with correct params
                    repo.list_by_kb.assert_called_once_with(
                        kb_id="all", status="approved", scope="global",
                        term_type=None, limit=50, offset=50,
                    )

            asyncio.run(_run())

    def test_list_terms_repo_error_returns_empty(self):
        """If repo raises, gracefully return empty."""
        repo = AsyncMock()
        repo.list_by_kb = AsyncMock(side_effect=Exception("DB error"))
        repo.count_by_kb = AsyncMock(return_value=0)

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary")
                    assert resp.status_code == 200
                    assert resp.json()["terms"] == []

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/{term_id}
# ---------------------------------------------------------------------------

class TestGetGlossaryTerm:
    def test_get_term_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "t1", "term": "API", "kb_id": "test"})

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/t1")
                    assert resp.status_code == 200
                    assert resp.json()["term"] == "API"

            asyncio.run(_run())

    def test_get_term_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/nonexistent")
                    assert resp.status_code == 404

            asyncio.run(_run())

    def test_get_term_no_repo(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/t1")
                    assert resp.status_code == 404

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary — create term
# ---------------------------------------------------------------------------

class TestCreateGlossaryTerm:
    def test_create_term_success(self):
        repo = AsyncMock()
        repo.save = AsyncMock()

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary",
                        json={"term": "API", "kb_id": "test", "definition": "Application Programming Interface"},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["success"] is True
                    assert "term_id" in data
                    repo.save.assert_called_once()

            asyncio.run(_run())

    def test_create_term_with_explicit_id(self):
        repo = AsyncMock()
        repo.save = AsyncMock()

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary",
                        json={"id": "custom-id", "term": "SDK", "kb_id": "test"},
                    )
                    data = resp.json()
                    assert data["term_id"] == "custom-id"

            asyncio.run(_run())

    def test_create_term_no_repo_returns_stub(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary",
                        json={"term": "test"},
                    )
                    assert resp.status_code == 200
                    assert "stub" in resp.json()["message"]

            asyncio.run(_run())

    def test_create_term_repo_error(self):
        repo = AsyncMock()
        repo.save = AsyncMock(side_effect=Exception("DB write error"))

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary",
                        json={"term": "fail"},
                    )
                    assert resp.status_code == 500

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/glossary/{term_id} — update term
# ---------------------------------------------------------------------------

class TestUpdateGlossaryTerm:
    def test_update_term_success(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={
            "id": "t1", "term": "API", "kb_id": "test", "scope": "local", "source": "manual",
        })
        repo.save = AsyncMock()

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.patch(
                        "/api/v1/admin/glossary/t1",
                        json={"definition": "Updated definition"},
                    )
                    assert resp.status_code == 200
                    assert resp.json()["success"] is True

            asyncio.run(_run())

    def test_update_global_standard_blocked(self):
        """Global standard terms (non-manual source) cannot be updated."""
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={
            "id": "t1", "term": "standard", "kb_id": "all",
            "scope": "global", "source": "csv_import",
        })

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.patch(
                        "/api/v1/admin/glossary/t1",
                        json={"definition": "try to change"},
                    )
                    assert resp.status_code == 403

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/glossary/{term_id}
# ---------------------------------------------------------------------------

class TestDeleteGlossaryTerm:
    def test_delete_success(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={
            "id": "t1", "term": "API", "kb_id": "test", "scope": "local", "source": "manual",
        })
        repo.delete = AsyncMock(return_value=True)

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/admin/glossary/t1")
                    assert resp.status_code == 200
                    assert resp.json()["success"] is True

            asyncio.run(_run())

    def test_delete_global_standard_blocked(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={
            "id": "t1", "term": "std", "kb_id": "all",
            "scope": "global", "source": "csv_import",
        })

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/admin/glossary/t1")
                    assert resp.status_code == 403

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/{term_id}/approve
# ---------------------------------------------------------------------------

class TestApproveGlossaryTerm:
    def test_approve_success(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={
            "id": "t1", "term": "API", "kb_id": "test",
        })
        repo.save = AsyncMock()

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary/t1/approve",
                        json={"approved_by": "admin"},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["status"] == "approved"

            asyncio.run(_run())

    def test_approve_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary/missing/approve",
                        json={},
                    )
                    assert resp.status_code == 404

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/{term_id}/reject
# ---------------------------------------------------------------------------

class TestRejectGlossaryTerm:
    def test_reject_success(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={
            "id": "t1", "term": "API", "kb_id": "test",
        })
        repo.save = AsyncMock()

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary/t1/reject",
                        json={},
                    )
                    assert resp.status_code == 200
                    assert resp.json()["status"] == "rejected"

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/{term_id}/promote-global
# ---------------------------------------------------------------------------

class TestPromoteGlossaryTerm:
    def test_promote_success(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={
            "id": "t1", "term": "API", "kb_id": "test",
        })
        repo.save = AsyncMock()

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/glossary/t1/promote-global")
                    assert resp.status_code == 200
                    assert resp.json()["scope"] == "global"

            asyncio.run(_run())

    def test_promote_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/glossary/missing/promote-global")
                    assert resp.status_code == 404

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/domain-stats
# ---------------------------------------------------------------------------

class TestDomainStats:
    def test_domain_stats_no_repo(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/domain-stats")
                    assert resp.status_code == 200
                    assert resp.json()["domains"] == {}

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/source-stats
# ---------------------------------------------------------------------------

class TestSourceStats:
    def test_source_stats_no_repo(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/source-stats")
                    assert resp.status_code == 200
                    assert resp.json()["sources"] == {}

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/discovered-synonyms
# ---------------------------------------------------------------------------

class TestDiscoveredSynonyms:
    def test_discovered_synonyms_no_repo(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/discovered-synonyms")
                    assert resp.status_code == 200
                    assert resp.json()["synonyms"] == []

            asyncio.run(_run())

    def test_discovered_synonyms_with_repo(self):
        repo = AsyncMock()
        repo.list_by_kb = AsyncMock(return_value=[
            {"id": "1", "term": "syn1", "source": "auto_discovered"},
            {"id": "2", "term": "syn2", "source": "manual"},
        ])

        state = _mock_state_with_repo(repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/discovered-synonyms")
                    assert resp.status_code == 200
                    data = resp.json()
                    # Only auto_discovered source terms returned
                    assert len(data["synonyms"]) == 1
                    assert data["synonyms"][0]["source"] == "auto_discovered"

            asyncio.run(_run())
