"""Extended unit tests for src/api/routes/glossary.py — import-csv, similarity-distribution, batch approve/reject."""

from __future__ import annotations

import asyncio
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import src.api.app  # noqa: F401
from src.api.routes import glossary


def _make_app():
    app = FastAPI()
    app.include_router(glossary.router)
    return app


def _mock_state(repo=None, embedder=None, search_cache=None):
    from src.api.state import AppState
    state = AppState()
    state["glossary_repo"] = repo
    state["embedder"] = embedder
    state["search_cache"] = search_cache
    return state


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# POST /import-csv
# ---------------------------------------------------------------------------
class TestImportCsv:
    def test_no_repo(self):
        state = _mock_state()
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary/import-csv",
                        files={"file": ("test.csv", b"term\nhello", "text/csv")},
                    )
                    assert resp.status_code == 503

            _run(_go())

    def test_no_files(self):
        repo = AsyncMock()
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/glossary/import-csv")
                    assert resp.status_code == 400

            _run(_go())

    def test_import_success_with_cache_clear(self):
        repo = AsyncMock()
        search_cache = AsyncMock()
        search_cache.clear = AsyncMock(return_value=5)
        state = _mock_state(repo=repo, search_cache=search_cache)

        fake_result = {"success": True, "imported": 10, "skipped": 0, "errors": []}
        with patch.object(glossary, "_get_state", return_value=state), \
             patch("src.api.services.glossary_import_service.import_csv", new_callable=AsyncMock, return_value=fake_result):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary/import-csv",
                        files={"file": ("test.csv", b"term\nhello", "text/csv")},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["success"] is True
                    assert data["imported"] == 10
                    search_cache.clear.assert_awaited_once()

            _run(_go())


# ---------------------------------------------------------------------------
# GET /similarity-distribution
# ---------------------------------------------------------------------------
class TestSimilarityDistribution:
    def test_no_repo(self):
        state = _mock_state()
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/similarity-distribution")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["total_pairs"] == 0
                    assert data["sample_size"] == 0

            _run(_go())

    def test_empty_terms(self):
        repo = AsyncMock()
        repo.count_by_kb = AsyncMock(return_value=0)
        repo.list_by_kb = AsyncMock(return_value=[])
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/similarity-distribution")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["total_pairs"] == 0

            _run(_go())

    def test_with_pending_terms_and_standards(self):
        repo = AsyncMock()
        # First two calls: count approved/pending for distribution
        # Next two: count pending_total/approved_total
        # Then list calls
        pending_terms = [
            {"term": "서버", "definition": "컴퓨터", "term_ko": "서버", "id": "1"},
            {"term": "네트워크", "definition": "통신", "term_ko": "네트워크", "id": "2"},
        ]
        approved_terms = [
            {"term": "시스템", "definition": "체계", "term_ko": "시스템", "id": "3"},
            {"term": "데이터베이스", "definition": "DB", "term_ko": "DB", "id": "4"},
        ]
        repo.count_by_kb = AsyncMock(side_effect=[2, 2, 2, 2])
        repo.list_by_kb = AsyncMock(side_effect=[pending_terms, approved_terms])
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/similarity-distribution")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert "distribution" in data

            _run(_go())

    def test_exception_returns_error(self):
        repo = AsyncMock()
        repo.count_by_kb = AsyncMock(side_effect=RuntimeError("db error"))
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/similarity-distribution")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert "error" in data

            _run(_go())


# ---------------------------------------------------------------------------
# POST /discovered-synonyms/approve
# ---------------------------------------------------------------------------
class TestBatchApproveSynonyms:
    """Test approve_discovered_synonyms directly (route path conflicts with {term_id})."""

    def test_no_repo(self):
        state = _mock_state()
        with patch.object(glossary, "_get_state", return_value=state):
            async def _go():
                from fastapi import HTTPException
                with pytest.raises(HTTPException) as exc_info:
                    await glossary.approve_discovered_synonyms({"synonym_ids": ["id1"]})
                assert exc_info.value.status_code == 503

            _run(_go())

    def test_empty_ids(self):
        repo = AsyncMock()
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            async def _go():
                from fastapi import HTTPException
                with pytest.raises(HTTPException) as exc_info:
                    await glossary.approve_discovered_synonyms({"synonym_ids": []})
                assert exc_info.value.status_code == 400

            _run(_go())

    def test_approve_with_base_term(self):
        repo = AsyncMock()
        syn_record = {"id": "syn1", "term": "동의어", "kb_id": "kb1", "related_terms": ["base1"], "status": "pending"}
        base_record = {"id": "base1", "term": "원래용어", "kb_id": "kb1", "synonyms": []}
        repo.get_by_id = AsyncMock(side_effect=lambda tid: syn_record if tid == "syn1" else base_record)
        repo.save = AsyncMock()
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            async def _go():
                result = await glossary.approve_discovered_synonyms({"synonym_ids": ["syn1"]})
                assert result["approved"] == 1

            _run(_go())

    def test_approve_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            async def _go():
                result = await glossary.approve_discovered_synonyms({"synonym_ids": ["missing"]})
                assert result["approved"] == 0
                assert len(result["errors"]) > 0

            _run(_go())


# ---------------------------------------------------------------------------
# POST /discovered-synonyms/reject
# ---------------------------------------------------------------------------
class TestBatchRejectSynonyms:
    """Test reject_discovered_synonyms directly (route path conflicts with {term_id})."""

    def test_reject_success(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "syn1", "term": "용어", "kb_id": "kb1", "status": "pending"})
        repo.save = AsyncMock()
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            async def _go():
                result = await glossary.reject_discovered_synonyms({"synonym_ids": ["syn1"]})
                assert result["rejected"] == 1

            _run(_go())

    def test_reject_no_repo(self):
        state = _mock_state()
        with patch.object(glossary, "_get_state", return_value=state):
            async def _go():
                from fastapi import HTTPException
                with pytest.raises(HTTPException) as exc_info:
                    await glossary.reject_discovered_synonyms({"synonym_ids": ["syn1"]})
                assert exc_info.value.status_code == 503

            _run(_go())

    def test_reject_empty_ids(self):
        repo = AsyncMock()
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            async def _go():
                from fastapi import HTTPException
                with pytest.raises(HTTPException) as exc_info:
                    await glossary.reject_discovered_synonyms({"synonym_ids": []})
                assert exc_info.value.status_code == 400

            _run(_go())


# ---------------------------------------------------------------------------
# POST /similarity-check
# ---------------------------------------------------------------------------
class TestSimilarityCheck:
    def test_no_repo(self):
        state = _mock_state()
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/glossary/similarity-check")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["pairs"] == []

            _run(_go())

    def test_with_pending_terms(self):
        repo = AsyncMock()
        repo.list_by_kb = AsyncMock(return_value=[{"id": "t1", "term": "서버", "status": "pending"}])
        repo.count_by_kb = AsyncMock(return_value=1)
        repo.search = AsyncMock(return_value=[
            {"id": "t2", "term": "서버시스템", "status": "approved"},
        ])
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/glossary/similarity-check")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert len(data["pairs"]) == 1

            _run(_go())


# ---------------------------------------------------------------------------
# POST /similarity-cleanup
# ---------------------------------------------------------------------------
class TestSimilarityCleanup:
    def test_cleanup_with_term_ids(self):
        repo = AsyncMock()
        repo.bulk_delete = AsyncMock(return_value=3)
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary/similarity-cleanup",
                        json={"term_ids": ["a", "b", "c"]},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["removed"] == 3

            _run(_go())

    def test_cleanup_no_ids(self):
        repo = AsyncMock()
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary/similarity-cleanup",
                        json={},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["removed"] == 0

            _run(_go())


# ---------------------------------------------------------------------------
# DELETE /by-type/{term_type}
# ---------------------------------------------------------------------------
class TestDeleteByType:
    def test_delete_by_type(self):
        repo = AsyncMock()
        repo.list_by_kb = AsyncMock(return_value=[{"id": "1"}, {"id": "2"}])
        repo.bulk_delete = AsyncMock(return_value=2)
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/admin/glossary/by-type/word")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["deleted"] == 2

            _run(_go())

    def test_delete_by_type_no_terms(self):
        repo = AsyncMock()
        repo.list_by_kb = AsyncMock(return_value=[])
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/admin/glossary/by-type/word")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["deleted"] == 0

            _run(_go())


# ---------------------------------------------------------------------------
# POST /add-synonym
# ---------------------------------------------------------------------------
class TestAddSynonym:
    def test_add_synonym_success(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "t1", "term": "서버", "kb_id": "kb1", "synonyms": ["server"]})
        repo.save = AsyncMock()
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary/add-synonym",
                        json={"term_id": "t1", "synonym": "srv"},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["success"] is True

            _run(_go())

    def test_add_synonym_missing_params(self):
        repo = AsyncMock()
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/glossary/add-synonym",
                        json={},
                    )
                    assert resp.status_code == 400

            _run(_go())


# ---------------------------------------------------------------------------
# GET /{term_id}/synonyms
# ---------------------------------------------------------------------------
class TestListSynonyms:
    def test_list_synonyms(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "t1", "term": "서버", "synonyms": ["server", "srv"]})
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/t1/synonyms")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert len(data["synonyms"]) == 2

            _run(_go())

    def test_list_synonyms_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/missing/synonyms")
                    assert resp.status_code == 404

            _run(_go())


# ---------------------------------------------------------------------------
# DELETE /{term_id}/synonyms/{synonym}
# ---------------------------------------------------------------------------
class TestRemoveSynonym:
    def test_remove_synonym_success(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "t1", "term": "서버", "kb_id": "kb1", "synonyms": ["server", "srv"]})
        repo.save = AsyncMock()
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/admin/glossary/t1/synonyms/server")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["success"] is True

            _run(_go())

    def test_remove_synonym_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "t1", "term": "서버", "kb_id": "kb1", "synonyms": ["server"]})
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/admin/glossary/t1/synonyms/missing")
                    assert resp.status_code == 404

            _run(_go())


# ---------------------------------------------------------------------------
# POST /{term_id}/promote-global
# ---------------------------------------------------------------------------
class TestPromoteGlobal:
    def test_promote_success(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "t1", "term": "서버", "kb_id": "kb1", "scope": "local"})
        repo.save = AsyncMock()
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/glossary/t1/promote-global")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["scope"] == "global"

            _run(_go())

    def test_promote_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/glossary/missing/promote-global")
                    assert resp.status_code == 404

            _run(_go())


# ---------------------------------------------------------------------------
# GET /discovered-synonyms
# ---------------------------------------------------------------------------
class TestDiscoveredSynonyms:
    def test_discovered_synonyms(self):
        repo = AsyncMock()
        repo.list_by_kb = AsyncMock(return_value=[
            {"id": "1", "term": "syn1", "source": "auto_discovered"},
            {"id": "2", "term": "normal", "source": "manual"},
        ])
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/discovered-synonyms")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert len(data["synonyms"]) == 1
                    assert data["synonyms"][0]["source"] == "auto_discovered"

            _run(_go())


# ---------------------------------------------------------------------------
# PATCH /{term_id}  (global standard read-only guard)
# ---------------------------------------------------------------------------
class TestUpdateGlobalGuard:
    def test_update_global_standard_forbidden(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={
            "id": "t1", "term": "서버", "kb_id": "global", "scope": "global", "source": "csv_import"
        })
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.patch(
                        "/api/v1/admin/glossary/t1",
                        json={"definition": "new def"},
                    )
                    assert resp.status_code == 403

            _run(_go())

    def test_update_manual_allowed(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={
            "id": "t1", "term": "서버", "kb_id": "kb1", "scope": "local", "source": "manual"
        })
        repo.save = AsyncMock()
        state = _mock_state(repo=repo)
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.patch(
                        "/api/v1/admin/glossary/t1",
                        json={"definition": "new def"},
                    )
                    assert resp.status_code == 200

            _run(_go())


# ---------------------------------------------------------------------------
# GET /source-stats
# ---------------------------------------------------------------------------
class TestSourceStats:
    def test_no_repo(self):
        state = _mock_state()
        with patch.object(glossary, "_get_state", return_value=state):
            app = _make_app()
            transport = ASGITransport(app=app)

            async def _go():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/glossary/source-stats")
                    assert resp.status_code == 200
                    assert resp.json()["sources"] == {}

            _run(_go())
