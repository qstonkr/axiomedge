"""audit_log archive cron — P1-2."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.jobs.audit_log_archive import audit_log_archive_sweep, run_audit_archive


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
        assert result["skipped"] == 1
        assert result["status"] == "noop"
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
    async def test_bucket_set_but_dump_unimplemented_returns_error(
        self, monkeypatch, repo,
    ):
        # P0-W3: silent skip 이 아니라 status=error 보고 + logger.error.
        monkeypatch.setenv("AUDIT_LOG_ARCHIVE_BUCKET", "s3://x")
        monkeypatch.delenv("AUDIT_LOG_DELETE_ONLY", raising=False)
        result = await run_audit_archive(repo=repo)
        assert result["status"] == "error"
        assert result["reason"] == "archive_bucket_set_but_dump_unimplemented"
        repo.archive_older_than.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_retention_180_days(self, monkeypatch, repo):
        monkeypatch.delenv("AUDIT_LOG_ARCHIVE_BUCKET", raising=False)
        monkeypatch.setenv("AUDIT_LOG_DELETE_ONLY", "true")
        monkeypatch.delenv("AUDIT_LOG_RETENTION_DAYS", raising=False)
        await run_audit_archive(repo=repo)
        repo.archive_older_than.assert_awaited_once_with(days=180)

    @pytest.mark.asyncio
    async def test_bucket_set_runs_metrics_inc(self, monkeypatch, repo):
        """bucket-only error path 가 metrics_inc 를 호출하는지 (line 56-57 cover)."""
        monkeypatch.setenv("AUDIT_LOG_ARCHIVE_BUCKET", "s3://x")
        monkeypatch.delenv("AUDIT_LOG_DELETE_ONLY", raising=False)
        with patch("src.api.routes.metrics.inc") as mock_inc:
            result = await run_audit_archive(repo=repo)
        assert result["status"] == "error"
        mock_inc.assert_called_once_with("errors", 1)


class TestSweepEntrypoint:
    """audit_log_archive_sweep — arq entrypoint."""

    @pytest.mark.asyncio
    async def test_skipped_when_session_maker_missing(self, monkeypatch):
        """get_knowledge_session_maker → None 이면 status=skipped."""
        monkeypatch.delenv("AUDIT_LOG_ARCHIVE_BUCKET", raising=False)
        monkeypatch.delenv("AUDIT_LOG_DELETE_ONLY", raising=False)
        with patch(
            "src.stores.postgres.session.get_knowledge_session_maker",
            return_value=None,
        ):
            result = await audit_log_archive_sweep({})
        assert result["status"] == "skipped"
        assert result["reason"] == "no_database_url"

    @pytest.mark.asyncio
    async def test_skipped_on_import_error(self, monkeypatch):
        """import 실패 시 graceful skip."""
        # Force ImportError by removing the module from sys.modules + patching.
        with patch.dict(
            sys.modules, {"src.stores.postgres.session": None},
        ):
            result = await audit_log_archive_sweep({})
        assert result["status"] == "skipped"
        assert "reason" in result

    @pytest.mark.asyncio
    async def test_invokes_run_audit_archive(self, monkeypatch):
        """session_maker 정상 + repo 생성 → run_audit_archive 호출."""
        monkeypatch.delenv("AUDIT_LOG_ARCHIVE_BUCKET", raising=False)
        monkeypatch.delenv("AUDIT_LOG_DELETE_ONLY", raising=False)
        fake_maker = MagicMock()
        with patch(
            "src.stores.postgres.session.get_knowledge_session_maker",
            return_value=fake_maker,
        ):
            with patch(
                "src.stores.postgres.repositories.audit_log.AuditLogRepository",
            ) as mock_repo_cls:
                mock_repo_cls.return_value = MagicMock()
                result = await audit_log_archive_sweep({})
        # NOOP 모드 (env 없음) → status="noop"
        assert result["status"] == "noop"
