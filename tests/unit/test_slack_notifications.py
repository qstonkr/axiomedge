"""Slack notification module — send + event helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.notifications.slack import (
    notify_bootstrap_failure_streak,
    notify_pending_threshold,
    notify_yaml_pr_stale,
    send,
)


class TestSend:
    @pytest.mark.asyncio
    async def test_noop_when_webhook_unset(self):
        with patch(
            "src.notifications.slack._get_webhook_url", return_value=None,
        ):
            result = await send("hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_posts_when_webhook_set(self):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch(
            "src.notifications.slack._get_webhook_url",
            return_value="https://hooks.slack.com/services/X",
        ), patch(
            "src.notifications.slack.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await send("hello")
        assert result is True
        mock_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_swallows_failures(self):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=RuntimeError("network down"))

        with patch(
            "src.notifications.slack._get_webhook_url",
            return_value="https://hooks.slack.com/services/X",
        ), patch(
            "src.notifications.slack.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await send("hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_swallows_non_2xx(self):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "internal"
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch(
            "src.notifications.slack._get_webhook_url",
            return_value="https://hooks.slack.com/services/X",
        ), patch(
            "src.notifications.slack.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await send("hello")
        assert result is False


class TestEventHelpers:
    @pytest.mark.asyncio
    async def test_bootstrap_failure_formats_message(self):
        with patch(
            "src.notifications.slack.send", new=AsyncMock(),
        ) as mock_send:
            await notify_bootstrap_failure_streak(kb_id="g-espa", count=3)
        mock_send.assert_awaited_once()
        msg = mock_send.await_args.args[0]
        assert "g-espa" in msg
        assert "3" in msg

    @pytest.mark.asyncio
    async def test_pending_threshold_formats(self):
        with patch(
            "src.notifications.slack.send", new=AsyncMock(),
        ) as mock_send:
            await notify_pending_threshold(kb_id="g-espa", pending=67)
        msg = mock_send.await_args.args[0]
        assert "g-espa" in msg
        assert "67" in msg

    @pytest.mark.asyncio
    async def test_yaml_pr_stale_formats(self):
        with patch(
            "src.notifications.slack.send", new=AsyncMock(),
        ) as mock_send:
            await notify_yaml_pr_stale(
                branch="schema/g-espa-20260424", hours=49,
            )
        msg = mock_send.await_args.args[0]
        assert "schema/g-espa-20260424" in msg
