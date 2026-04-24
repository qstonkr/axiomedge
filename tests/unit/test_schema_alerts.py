"""Cron: scan and emit Slack alerts for graph-schema ops events."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.jobs.schema_alerts import run_alerts_sweep


class _FakeNotif:
    candidate_pending_threshold = 5
    bootstrap_failure_streak = 3
    yaml_pr_stale_hours = 48


class _FakeSettings:
    notifications = _FakeNotif()


class TestRunAlertsSweep:
    @pytest.mark.asyncio
    async def test_pending_threshold_fires(self):
        candidate_repo = MagicMock()
        candidate_repo.count_pending_by_kb = AsyncMock(
            return_value=[("g-espa", 7), ("partner", 1)],
        )
        run_repo = MagicMock()
        run_repo.recent_failure_streak = AsyncMock(return_value={})

        with patch(
            "src.jobs.schema_alerts.get_settings",
            return_value=_FakeSettings(),
        ), patch(
            "src.jobs.schema_alerts.notify_pending_threshold",
            new=AsyncMock(),
        ) as pending, patch(
            "src.jobs.schema_alerts.notify_bootstrap_failure_streak",
            new=AsyncMock(),
        ) as streak:
            await run_alerts_sweep(
                candidate_repo=candidate_repo, run_repo=run_repo,
            )
        pending.assert_awaited_once()
        kwargs = pending.await_args.kwargs
        assert kwargs["kb_id"] == "g-espa"
        assert kwargs["pending"] == 7
        streak.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failure_streak_fires(self):
        candidate_repo = MagicMock()
        candidate_repo.count_pending_by_kb = AsyncMock(return_value=[])
        run_repo = MagicMock()
        run_repo.recent_failure_streak = AsyncMock(
            return_value={"partner": 3, "g-espa": 1},
        )

        with patch(
            "src.jobs.schema_alerts.get_settings",
            return_value=_FakeSettings(),
        ), patch(
            "src.jobs.schema_alerts.notify_bootstrap_failure_streak",
            new=AsyncMock(),
        ) as streak:
            await run_alerts_sweep(
                candidate_repo=candidate_repo, run_repo=run_repo,
            )
        streak.assert_awaited_once()
        kwargs = streak.await_args.kwargs
        assert kwargs["kb_id"] == "partner"
        assert kwargs["count"] == 3

    @pytest.mark.asyncio
    async def test_swallows_repo_errors(self):
        """Ops cron must never crash the worker loop."""
        candidate_repo = MagicMock()
        candidate_repo.count_pending_by_kb = AsyncMock(
            side_effect=RuntimeError("DB down"),
        )
        run_repo = MagicMock()
        run_repo.recent_failure_streak = AsyncMock(
            side_effect=RuntimeError("DB down"),
        )

        with patch(
            "src.jobs.schema_alerts.get_settings",
            return_value=_FakeSettings(),
        ):
            await run_alerts_sweep(
                candidate_repo=candidate_repo, run_repo=run_repo,
            )
