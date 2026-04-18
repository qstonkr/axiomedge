"""Tests for Arq job queue helpers (URL parsing + task registry)."""

from __future__ import annotations

from unittest.mock import patch

from src.jobs.queue import redis_settings_from_env
from src.jobs.tasks import REGISTERED_TASKS, example_task


def test_redis_settings_default() -> None:
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("ARQ_REDIS_URL", None)
        os.environ.pop("REDIS_URL", None)
        s = redis_settings_from_env()
        assert s.host == "localhost"
        assert s.port == 6379
        assert s.database == 0


def test_redis_settings_from_url_with_db() -> None:
    with patch.dict("os.environ", {"ARQ_REDIS_URL": "redis://my-host:6380/3"}):
        s = redis_settings_from_env()
        assert s.host == "my-host"
        assert s.port == 6380
        assert s.database == 3


def test_redis_settings_with_password() -> None:
    with patch.dict("os.environ", {"ARQ_REDIS_URL": "redis://:secret@host:6379/0"}):
        s = redis_settings_from_env()
        assert s.password == "secret"


def test_arq_redis_url_takes_precedence_over_redis_url() -> None:
    with patch.dict("os.environ", {
        "ARQ_REDIS_URL": "redis://arq-host:6380",
        "REDIS_URL": "redis://main-host:6379",
    }):
        s = redis_settings_from_env()
        assert s.host == "arq-host"


def test_example_task_registered() -> None:
    assert example_task in REGISTERED_TASKS


def test_registered_tasks_have_unique_names() -> None:
    names = [t.__name__ for t in REGISTERED_TASKS]
    assert len(names) == len(set(names)), "duplicate task name in REGISTERED_TASKS"


import pytest


@pytest.mark.asyncio
async def test_example_task_returns_processed_message() -> None:
    ctx = {"job_id": "test-123"}
    result = await example_task(ctx, "hello")
    assert result == "processed: hello"
