"""Unit tests for the jobs module (Redis-backed ingestion job tracking).

Mocks the Redis client to test job CRUD logic without a running Redis.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeRedis:
    """In-memory fake Redis for unit testing."""

    def __init__(self):
        self._store: dict[str, dict[str, str]] = {}
        self._lists: dict[str, list[str]] = {}

    async def hset(self, key: str, mapping: dict) -> None:
        if key not in self._store:
            self._store[key] = {}
        self._store[key].update({k: str(v) for k, v in mapping.items()})

    async def hgetall(self, key: str) -> dict:
        return dict(self._store.get(key, {}))

    async def hget(self, key: str, field: str) -> str | None:
        return self._store.get(key, {}).get(field)

    async def exists(self, key: str) -> bool:
        return key in self._store

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def expire(self, key: str, ttl: int) -> None:
        pass  # No-op for tests

    async def rpush(self, key: str, value: str) -> None:
        self._lists.setdefault(key, []).append(value)

    async def llen(self, key: str) -> int:
        return len(self._lists.get(key, []))

    async def lpop(self, key: str) -> str | None:
        lst = self._lists.get(key, [])
        return lst.pop(0) if lst else None

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis: _FakeRedis):
        self._redis = redis
        self._ops: list = []

    def hset(self, key, mapping):
        self._ops.append(("hset", key, mapping))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def rpush(self, key, value):
        self._ops.append(("rpush", key, value))
        return self

    async def execute(self):
        for op in self._ops:
            if op[0] == "hset":
                await self._redis.hset(op[1], op[2])
            elif op[0] == "rpush":
                await self._redis.rpush(op[1], op[2])


class TestJobs:
    """Test Redis-backed job tracking."""

    def setup_method(self) -> None:
        self._fake_redis = _FakeRedis()

    def _patch_redis(self):
        return patch(
            "src.api.routes.jobs._get_redis",
            new=AsyncMock(return_value=self._fake_redis),
        )

    def test_create_job(self) -> None:
        from src.api.routes.jobs import create_job, get_job

        async def _test():
            with self._patch_redis():
                job_id = await create_job("kb-1", file_count=5)
                assert isinstance(job_id, str)
                assert len(job_id) == 8

                job = await get_job(job_id)
                assert job is not None
                assert job["kb_id"] == "kb-1"
                assert job["status"] == "processing"
                assert job["total_files"] == 5

        _run(_test())

    def test_update_job(self) -> None:
        from src.api.routes.jobs import create_job, update_job, get_job

        async def _test():
            with self._patch_redis():
                job_id = await create_job("kb-1", file_count=3)
                await update_job(job_id, processed=2, chunks=50)

                job = await get_job(job_id)
                assert job["processed"] == 2
                assert job["chunks"] == 50

        _run(_test())

    def test_update_job_status_completed(self) -> None:
        from src.api.routes.jobs import create_job, update_job, get_job

        async def _test():
            with self._patch_redis():
                job_id = await create_job("kb-1", file_count=1)
                await update_job(job_id, status="completed", processed=1, chunks=10)

                job = await get_job(job_id)
                assert job["status"] == "completed"
                assert job["completed_at"]  # Should be set

        _run(_test())

    def test_update_nonexistent_job(self) -> None:
        from src.api.routes.jobs import update_job, get_job

        async def _test():
            with self._patch_redis():
                await update_job("nonexistent", status="completed")
                assert await get_job("nonexistent") is None

        _run(_test())

    def test_get_nonexistent_job(self) -> None:
        from src.api.routes.jobs import get_job

        async def _test():
            with self._patch_redis():
                assert await get_job("does-not-exist") is None

        _run(_test())

    def test_multiple_creates_unique_ids(self) -> None:
        from src.api.routes.jobs import create_job

        async def _test():
            with self._patch_redis():
                ids = set()
                for i in range(10):
                    jid = await create_job(f"kb-{i}", file_count=i)
                    ids.add(jid)
                assert len(ids) == 10

        _run(_test())
