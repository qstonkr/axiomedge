"""Ingestion failure Slack alert sweep — PR-6 (E).

- threshold 미만 streak: 알림 X
- threshold 이상: 알림 1회
- redis NX dedup 시 중복 알림 X
- 실패 sample 가 알림 텍스트에 포함
- recent_failure_streak 시맨틱 (success 만나면 streak 마감)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.jobs.ingestion_alerts import run_ingestion_alerts


@pytest.fixture
def fake_settings(monkeypatch):
    s = type("S", (), {})()
    s.notifications = type("N", (), {
        "slack_webhook_url": "https://example.com/hook",
        "ingestion_failure_streak": 3,
        "ingestion_failure_window_hours": 24,
        "ingestion_alert_dedup_minutes": 120,
        "candidate_pending_threshold": 50,
        "yaml_pr_stale_hours": 48,
        "bootstrap_failure_streak": 3,
    })()
    monkeypatch.setattr("src.config.get_settings", lambda: s)
    return s


class TestRunIngestionAlerts:
    @pytest.mark.asyncio
    async def test_no_alert_below_threshold(self, fake_settings):
        run_repo = MagicMock()
        run_repo.recent_failure_streak = AsyncMock(
            return_value={"kb-a": 1, "kb-b": 2},
        )
        failure_repo = MagicMock()
        with patch(
            "src.notifications.slack.notify_ingestion_failure_streak",
            new=AsyncMock(return_value=True),
        ) as notify:
            result = await run_ingestion_alerts(
                run_repo=run_repo, failure_repo=failure_repo, redis=None,
            )
        assert result == {"fired": 0, "checked_kbs": 2}
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_fires_alert_at_threshold(self, fake_settings):
        run_repo = MagicMock()
        run_repo.recent_failure_streak = AsyncMock(
            return_value={"kb-a": 4, "kb-b": 1},
        )
        failure_repo = MagicMock()
        failure_repo.list_by_kb = AsyncMock(return_value=[
            {"doc_id": "doc-x", "stage": "embed", "reason": "boom"},
        ])
        with patch(
            "src.notifications.slack.notify_ingestion_failure_streak",
            new=AsyncMock(return_value=True),
        ) as notify:
            result = await run_ingestion_alerts(
                run_repo=run_repo, failure_repo=failure_repo, redis=None,
            )
        assert result == {"fired": 1, "checked_kbs": 2}
        notify.assert_awaited_once()
        call = notify.await_args
        assert call.kwargs["kb_id"] == "kb-a"
        assert call.kwargs["count"] == 4
        assert len(call.kwargs["sample_failures"]) == 1

    @pytest.mark.asyncio
    async def test_redis_dedup_blocks_duplicate(self, fake_settings):
        run_repo = MagicMock()
        run_repo.recent_failure_streak = AsyncMock(return_value={"kb-a": 5})
        failure_repo = MagicMock()
        failure_repo.list_by_kb = AsyncMock(return_value=[])
        # redis.set NX returns False → 이미 알림이 보내진 상태
        redis = MagicMock()
        redis.set = AsyncMock(return_value=False)

        with patch(
            "src.notifications.slack.notify_ingestion_failure_streak",
            new=AsyncMock(return_value=True),
        ) as notify:
            result = await run_ingestion_alerts(
                run_repo=run_repo, failure_repo=failure_repo, redis=redis,
            )
        assert result == {"fired": 0, "checked_kbs": 1}
        notify.assert_not_called()
        # redis.set 은 SET NX EX 형태로 호출됐는지
        redis.set.assert_awaited_once()
        kwargs = redis.set.await_args.kwargs
        assert kwargs.get("nx") is True
        assert kwargs.get("ex") == 120 * 60

    @pytest.mark.asyncio
    async def test_redis_failure_does_not_block_alert(self, fake_settings):
        run_repo = MagicMock()
        run_repo.recent_failure_streak = AsyncMock(return_value={"kb-a": 5})
        failure_repo = MagicMock()
        failure_repo.list_by_kb = AsyncMock(return_value=[])
        redis = MagicMock()
        redis.set = AsyncMock(side_effect=RuntimeError("redis down"))

        with patch(
            "src.notifications.slack.notify_ingestion_failure_streak",
            new=AsyncMock(return_value=True),
        ) as notify:
            result = await run_ingestion_alerts(
                run_repo=run_repo, failure_repo=failure_repo, redis=redis,
            )
        # Redis 장애 시 알림은 그래도 발사 (cron 30분 자체 dedup)
        assert result == {"fired": 1, "checked_kbs": 1}
        notify.assert_awaited_once()


class TestRecentFailureStreakSemantics:
    @pytest.mark.asyncio
    async def test_streak_marks_kb_with_consecutive_failures(self):
        from src.stores.postgres.repositories.ingestion_run import (
            IngestionRunRepository,
        )

        # session.execute mock 으로 raw 결과 시뮬레이션:
        # kb-a: failed, failed, failed → streak=3
        # kb-b: failed, success, failed → streak=1 (success 가 마감)
        rows = [
            ("kb-a", "failed", None),
            ("kb-a", "failed", None),
            ("kb-a", "failed", None),
            ("kb-b", "failed", None),
            ("kb-b", "completed", None),
            ("kb-b", "failed", None),
        ]
        result_obj = MagicMock()
        result_obj.all = MagicMock(return_value=rows)

        session = MagicMock()
        session.execute = AsyncMock(return_value=result_obj)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        maker = MagicMock(return_value=session)

        repo = IngestionRunRepository(maker)
        out = await repo.recent_failure_streak(window_hours=24)
        assert out == {"kb-a": 3, "kb-b": 1}
