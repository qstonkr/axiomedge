"""Microsoft Teams connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TeamsConnectorConfig:
    """Resolved configuration for a Teams-backed data source.

    Attributes:
        auth_token: MSGraph app-only access token (shared SecretBox).
        team_id: Microsoft Teams team ID (group ID).
        channel_ids: 동기화할 channel ID list. 비어있으면 모든 채널 (max 50).
        days_back: 며칠 전까지 (default 30). 0 = 무한.
        include_replies: thread reply 포함 여부 (default True).
        max_messages: 한 채널 당 최대 메시지 (default 500).
        name: human readable.
    """

    auth_token: str
    team_id: str
    channel_ids: tuple[str, ...]
    days_back: int = 30
    include_replies: bool = True
    max_messages: int = 500
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> TeamsConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}

        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "teams connector requires auth_token (shared SecretBox 에 admin 등록 필요)",
            )

        team_id = str(crawl_cfg.get("team_id") or "").strip()
        if not team_id:
            raise ValueError("teams connector requires crawl_config.team_id")

        raw_channels = crawl_cfg.get("channel_ids") or []
        if isinstance(raw_channels, str):
            raw_channels = [s.strip() for s in raw_channels.split(",") if s.strip()]
        channels = tuple(str(c).strip() for c in raw_channels if str(c).strip())

        # ``days_back=0`` 은 "무한" — ``or`` 로 채우면 0 이 30 으로 변경됨.
        days_back_raw = crawl_cfg.get("days_back")
        days_back = int(days_back_raw) if days_back_raw is not None else 30
        return cls(
            auth_token=token,
            team_id=team_id,
            channel_ids=channels,
            days_back=days_back,
            include_replies=bool(crawl_cfg.get("include_replies", True)),
            max_messages=int(crawl_cfg.get("max_messages") or 500),
            name=str(source.get("name") or ""),
        )
