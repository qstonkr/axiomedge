"""Slack knowledge connector.

Slack Web API (https://slack.com/api) — channel message + thread crawl. Auth
는 Bot OAuth Token (``xoxb-...``) — admin UI 에서 SecretBox 로 입력 후
connector launcher 가 inject.

권장 OAuth scopes:
- ``channels:history`` — public channel 메시지 read
- ``groups:history`` — private channel (bot 가 멤버일 때)
- ``users:read`` — username 조회 (mention/author 표시)

각 thread 또는 stand-alone message → 하나의 RawDocument. Bot ``not_in_channel``
는 그 channel 만 skip — 다른 channel 진행.
"""

from .client import SlackAPIError, SlackClient
from .config import SlackConnectorConfig
from .connector import SlackConnector

__all__ = [
    "SlackAPIError",
    "SlackClient",
    "SlackConnector",
    "SlackConnectorConfig",
]
