"""audit_log archive cron — P1-2."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jobs.audit_log_archive import run_audit_archive


@pytest.fixture
def repo():
    r = MagicMock()
    r.archive_older_than = AsyncMock(return_value=42)
    return r


class TestArchive:
    @pytest.mark.asyncio
    async def test_skips_when_no_bucket_no_delete_only(
        self, monkeypatch, repo,
    ):
        monkeypatch.delenv("AUDIT_LOG_ARCHIVE_BUCKET", raising=False)
        monkeypatch.delenv("AUDIT_LOG_DELETE_ONLY", raising=False)
        result = await run_audit_archive(repo=repo)
        assert result == {"archived": 0, "deleted": 0, "skipped": 1}
        repo.archive_older_than.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_only_mode_invokes_archive(
        self, monkeypatch, repo,
    ):
        monkeypatch.delenv("AUDIT_LOG_ARCHIVE_BUCKET", raising=False)
        monkeypatch.setenv("AUDIT_LOG_DELETE_ONLY", "1")
        monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "90")
        result = await run_audit_archive(repo=repo)
        assert result["deleted"] == 42
        repo.archive_older_than.assert_awaited_once_with(days=90)

    @pytest.mark.asyncio
    async def test_bucket_set_but_dump_unimplemented_skips(
        self, monkeypatch, repo,
    ):
        monkeypatch.setenv("AUDIT_LOG_ARCHIVE_BUCKET", "s3://x")
        monkeypatch.delenv("AUDIT_LOG_DELETE_ONLY", raising=False)
        result = await run_audit_archive(repo=repo)
        # 안전 default: bucket 만 있고 delete-only 없으면 보존
        assert result["skipped"] == 1
        repo.archive_older_than.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_retention_180_days(self, monkeypatch, repo):
        monkeypatch.delenv("AUDIT_LOG_ARCHIVE_BUCKET", raising=False)
        monkeypatch.setenv("AUDIT_LOG_DELETE_ONLY", "true")
        monkeypatch.delenv("AUDIT_LOG_RETENTION_DAYS", raising=False)
        await run_audit_archive(repo=repo)
        repo.archive_older_than.assert_awaited_once_with(days=180)
