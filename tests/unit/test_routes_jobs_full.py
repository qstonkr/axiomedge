"""Unit tests for src/api/routes/jobs.py — Redis-backed job tracking."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import jobs as jobs_mod


def _run(coro):
    return asyncio.run(coro)


def _make_app():
    app = FastAPI()
    app.include_router(jobs_mod.router)
    return app


def _mock_redis():
    """Create a mock Redis client."""
    r = AsyncMock()
    r.hset = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.hget = AsyncMock(return_value=None)
    r.exists = AsyncMock(return_value=True)
    r.expire = AsyncMock()
    r.rpush = AsyncMock()
    r.llen = AsyncMock(return_value=0)
    r.lrange = AsyncMock(return_value=[])
    r.lpop = AsyncMock()
    r.delete = AsyncMock()

    pipe = AsyncMock()
    pipe.hset = MagicMock()
    pipe.expire = MagicMock()
    pipe.rpush = MagicMock()
    pipe.execute = AsyncMock()
    r.pipeline = MagicMock(return_value=pipe)

    return r


# ===========================================================================
# create_job
# ===========================================================================
class TestCreateJob:
    def test_create_job(self):
        r = _mock_redis()
        r.llen = AsyncMock(return_value=5)

        async def _go():
            with patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                job_id = await jobs_mod.create_job("kb1", 3)
                assert isinstance(job_id, str)
                assert len(job_id) == 8

        _run(_go())

    def test_create_job_evicts_old(self):
        r = _mock_redis()
        r.llen = AsyncMock(return_value=1002)  # > _MAX_JOBS
        r.lpop = AsyncMock(return_value="old-job-id")

        async def _go():
            with patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                job_id = await jobs_mod.create_job("kb1", 1)
                assert isinstance(job_id, str)
                r.lpop.assert_awaited()

        _run(_go())


# ===========================================================================
# update_job
# ===========================================================================
class TestUpdateJob:
    def test_update_job(self):
        r = _mock_redis()

        async def _go():
            with patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                await jobs_mod.update_job("job-1", processed=5, chunks=20)
                r.hset.assert_awaited()

        _run(_go())

    def test_update_job_with_status_completed(self):
        r = _mock_redis()

        async def _go():
            with patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                await jobs_mod.update_job("job-1", status="completed")
                call_kwargs = r.hset.call_args
                mapping = call_kwargs.kwargs.get("mapping", {})
                assert "completed_at" in mapping

        _run(_go())

    def test_update_job_with_list_errors(self):
        r = _mock_redis()

        async def _go():
            with patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                await jobs_mod.update_job("job-1", errors=["err1", "err2"])
                call_kwargs = r.hset.call_args
                mapping = call_kwargs.kwargs.get("mapping", {})
                assert json.loads(mapping["errors"]) == ["err1", "err2"]

        _run(_go())

    def test_update_nonexistent_job(self):
        r = _mock_redis()
        r.exists = AsyncMock(return_value=False)

        async def _go():
            with patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                await jobs_mod.update_job("missing-job", processed=1)
                r.hset.assert_not_awaited()

        _run(_go())


# ===========================================================================
# get_job
# ===========================================================================
class TestGetJob:
    def test_get_existing_job(self):
        r = _mock_redis()
        r.hgetall = AsyncMock(return_value={
            "id": "job-1", "kb_id": "kb1", "status": "processing",
            "total_files": "3", "processed": "1", "chunks": "10",
            "errors": '["err1"]',
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "",
        })

        async def _go():
            with patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                job = await jobs_mod.get_job("job-1")
                assert job is not None
                assert job["id"] == "job-1"
                assert job["total_files"] == 3
                assert job["processed"] == 1
                assert job["errors"] == ["err1"]

        _run(_go())

    def test_get_nonexistent_job(self):
        r = _mock_redis()
        r.hgetall = AsyncMock(return_value={})

        async def _go():
            with patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                job = await jobs_mod.get_job("missing")
                assert job is None

        _run(_go())


# ===========================================================================
# is_cancelled
# ===========================================================================
class TestIsCancelled:
    def test_is_cancelled_true(self):
        r = _mock_redis()
        r.hget = AsyncMock(return_value="cancelled")

        async def _go():
            with patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                assert await jobs_mod.is_cancelled("job-1") is True

        _run(_go())

    def test_is_cancelled_false(self):
        r = _mock_redis()
        r.hget = AsyncMock(return_value="processing")

        async def _go():
            with patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                assert await jobs_mod.is_cancelled("job-1") is False

        _run(_go())


# ===========================================================================
# _deserialize
# ===========================================================================
class TestDeserialize:
    def test_int_fields(self):
        result = jobs_mod._deserialize({
            "total_files": "10",
            "processed": "5",
            "chunks": "100",
        })
        assert result["total_files"] == 10
        assert result["processed"] == 5
        assert result["chunks"] == 100

    def test_invalid_int(self):
        result = jobs_mod._deserialize({"total_files": "abc"})
        assert result["total_files"] == "abc"  # stays string

    def test_errors_json(self):
        result = jobs_mod._deserialize({"errors": '["a", "b"]'})
        assert result["errors"] == ["a", "b"]

    def test_errors_invalid_json(self):
        result = jobs_mod._deserialize({"errors": "not json"})
        assert result["errors"] == []


# ===========================================================================
# HTTP endpoints
# ===========================================================================
class TestJobEndpoints:
    def test_get_job_status_found(self):
        async def _go():
            with patch.object(jobs_mod, "get_job", new_callable=AsyncMock, return_value={
                "id": "job-1", "status": "completed", "total_files": 2,
            }):
                app = _make_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/jobs/job-1")
                    assert resp.status_code == 200
                    assert resp.json()["id"] == "job-1"

        _run(_go())

    def test_get_job_status_not_found(self):
        async def _go():
            with patch.object(jobs_mod, "get_job", new_callable=AsyncMock, return_value=None):
                app = _make_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/jobs/missing")
                    assert resp.status_code == 404

        _run(_go())

    def test_cancel_job_success(self):
        r = _mock_redis()

        async def _go():
            with patch.object(jobs_mod, "get_job", new_callable=AsyncMock, return_value={
                "id": "job-1", "status": "processing",
            }), patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                app = _make_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/jobs/job-1/cancel")
                    assert resp.status_code == 200
                    assert resp.json()["status"] == "cancelled"

        _run(_go())

    def test_cancel_job_not_found(self):
        async def _go():
            with patch.object(jobs_mod, "get_job", new_callable=AsyncMock, return_value=None):
                app = _make_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/jobs/missing/cancel")
                    assert resp.status_code == 404

        _run(_go())

    def test_cancel_job_already_completed(self):
        async def _go():
            with patch.object(jobs_mod, "get_job", new_callable=AsyncMock, return_value={
                "id": "job-1", "status": "completed",
            }):
                app = _make_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/jobs/job-1/cancel")
                    assert resp.status_code == 400

        _run(_go())

    def test_list_jobs(self):
        r = _mock_redis()
        r.lrange = AsyncMock(return_value=["job-1", "job-2"])
        r.hgetall = AsyncMock(side_effect=[
            {"id": "job-1", "status": "completed", "total_files": "1", "processed": "1", "chunks": "5", "errors": "[]"},
            {"id": "job-2", "status": "processing", "total_files": "2", "processed": "0", "chunks": "0", "errors": "[]"},
        ])

        async def _go():
            with patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                app = _make_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/jobs")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert len(data["jobs"]) == 2

        _run(_go())

    def test_list_jobs_empty(self):
        r = _mock_redis()
        r.lrange = AsyncMock(return_value=[])

        async def _go():
            with patch.object(jobs_mod, "_get_redis", new_callable=AsyncMock, return_value=r):
                app = _make_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/jobs")
                    assert resp.status_code == 200
                    assert resp.json()["jobs"] == []

        _run(_go())
