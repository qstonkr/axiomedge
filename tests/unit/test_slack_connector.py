"""SlackConnector — config / message format / channel iteration 검증.

실제 Slack API 호출 X — SlackClient 메서드 mock 으로 교체 후 BFS 회로 검증.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestSlackConfig:
    def test_missing_token_raises(self):
        from src.connectors.slack.config import SlackConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            SlackConnectorConfig.from_source({"crawl_config": {"channel_ids": ["C1"]}})

    def test_missing_channels_raises(self):
        from src.connectors.slack.config import SlackConnectorConfig
        with pytest.raises(ValueError, match="channel_ids"):
            SlackConnectorConfig.from_source({"crawl_config": {"auth_token": "xoxb"}})

    def test_string_channels_split_on_comma(self):
        from src.connectors.slack.config import SlackConnectorConfig
        cfg = SlackConnectorConfig.from_source({
            "crawl_config": {"auth_token": "xoxb", "channel_ids": "C1, C2 , C3"},
        })
        assert cfg.channel_ids == ("C1", "C2", "C3")

    def test_defaults(self):
        from src.connectors.slack.config import SlackConnectorConfig
        cfg = SlackConnectorConfig.from_source({
            "crawl_config": {"auth_token": "xoxb", "channel_ids": ["C1"]},
        })
        assert cfg.days_back == 30
        assert cfg.include_threads is True
        assert cfg.include_bot_messages is False


# ---------------------------------------------------------------------------
# Message formatting — mention / link 정규화
# ---------------------------------------------------------------------------


class TestMessageFormat:
    @pytest.mark.asyncio
    async def test_user_mention_resolved(self):
        from src.connectors.slack.connector import _format_message
        client = AsyncMock()
        client.users_info = AsyncMock(return_value="alice")
        msg = {"text": "hi <@U123>", "user": "U999", "ts": "1700000000.000100"}
        client.users_info.side_effect = lambda uid: {"U123": "alice", "U999": "bob"}[uid]
        out = await _format_message(client, msg)
        assert "@alice" in out
        assert "**bob**" in out  # author label

    @pytest.mark.asyncio
    async def test_channel_and_link_normalized(self):
        from src.connectors.slack.connector import _format_message
        client = AsyncMock()
        client.users_info = AsyncMock(return_value="user")
        msg = {
            "text": "see <#C99|general> and <https://x.com|X> and <https://y.com>",
            "ts": "1700000000.000100",
        }
        out = await _format_message(client, msg)
        assert "#general" in out
        assert "[X](https://x.com)" in out
        assert "https://y.com" in out


# ---------------------------------------------------------------------------
# 채널 fetch — bot 메시지 필터, thread 포함
# ---------------------------------------------------------------------------


class TestChannelFetch:
    @pytest.mark.asyncio
    async def test_skip_unauthorized_channel_continues_others(self):
        from src.connectors.slack import SlackConnector
        from src.connectors.slack.client import SlackAPIError, SlackClient

        async def fake_info(channel):
            if channel == "C_BAD":
                raise SlackAPIError("not in channel", code="not_in_channel")
            return {"channel": {"name": f"name-{channel}"}}

        async def fake_history(channel, **kwargs):
            return {
                "messages": [{
                    "type": "message", "user": "U1", "text": "hi",
                    "ts": "1700000001.000100",
                }],
                "has_more": False,
            }

        client_instance = SlackClient.__new__(SlackClient)
        client_instance.conversations_info = AsyncMock(side_effect=fake_info)
        client_instance.conversations_history = AsyncMock(side_effect=fake_history)
        client_instance.conversations_replies = AsyncMock()
        client_instance.users_info = AsyncMock(return_value="alice")
        client_instance.aclose = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=None)

        connector = SlackConnector()
        with patch(
            "src.connectors.slack.connector.SlackClient",
            return_value=client_instance,
        ):
            result = await connector.fetch({
                "auth_token": "xoxb", "channel_ids": ["C_BAD", "C_OK"],
                "days_back": 0, "include_threads": False,
            })

        assert result.success
        assert "C_BAD" in result.metadata["channels_skipped"]
        assert len(result.documents) == 1  # C_OK 의 메시지 하나만

    @pytest.mark.asyncio
    async def test_bot_messages_filtered(self):
        from src.connectors.slack import SlackConnector
        from src.connectors.slack.client import SlackClient

        async def fake_history(channel, **kwargs):
            return {
                "messages": [
                    {"type": "message", "user": "U1", "text": "real",
                     "ts": "1700000001.000100"},
                    {"type": "message", "subtype": "bot_message",
                     "bot_id": "B1", "text": "from bot",
                     "ts": "1700000002.000100"},
                ],
                "has_more": False,
            }

        client_instance = SlackClient.__new__(SlackClient)
        client_instance.conversations_info = AsyncMock(
            return_value={"channel": {"name": "general"}},
        )
        client_instance.conversations_history = AsyncMock(side_effect=fake_history)
        client_instance.conversations_replies = AsyncMock()
        client_instance.users_info = AsyncMock(return_value="alice")
        client_instance.aclose = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=None)

        connector = SlackConnector()
        with patch(
            "src.connectors.slack.connector.SlackClient",
            return_value=client_instance,
        ):
            result = await connector.fetch({
                "auth_token": "xoxb", "channel_ids": ["C_OK"],
                "days_back": 0, "include_threads": False,
                "include_bot_messages": False,
            })

        assert result.success
        assert len(result.documents) == 1
        assert "real" in result.documents[0].content
