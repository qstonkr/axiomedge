"""Microsoft Teams connector — Microsoft Graph teams/channels/messages.

토큰 모드: shared (admin app-only token, ``ChannelMessage.Read.All``).
사용자가 team_id + channel_ids 만 입력. Slack 과 비슷 패턴 — 1 thread =
1 RawDocument.
"""

from .config import TeamsConnectorConfig
from .connector import TeamsConnector

__all__ = ["TeamsConnector", "TeamsConnectorConfig"]
