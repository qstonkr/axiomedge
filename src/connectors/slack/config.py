"""Slack connector configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_DEFAULT_DAYS_BACK = 30
_DEFAULT_PAGE_SIZE = 200  # Slack max per call


@dataclass
class SlackConnectorConfig:
    """Resolved configuration for a Slack-backed data source.

    Attributes:
        auth_token: Bot OAuth Token (``xoxb-...``). SecretBox 우선 — connector
            launcher 가 inject.
        channel_ids: 동기화할 channel ID list (e.g. ``("C0123ABC", "C0456DEF")``).
            Channel 이름 (``#general``) 이 아니라 immutable ID 사용 (이름 변경
            대응).
        days_back: 며칠 전까지 메시지 가져올지 (default 30). 무한 = 0.
        include_threads: thread reply 포함 여부 (default True).
        include_bot_messages: bot 메시지 포함 여부 (default False — RAG 가치 낮음).
        page_size: API page size (max 200).
        name: Human readable source name.
    """

    auth_token: str
    channel_ids: tuple[str, ...]
    days_back: int = _DEFAULT_DAYS_BACK
    include_threads: bool = True
    include_bot_messages: bool = False
    page_size: int = _DEFAULT_PAGE_SIZE
    name: str = ""
    metadata_extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> SlackConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}

        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "slack connector requires auth_token (SecretBox 에 등록 후 자동 inject)",
            )

        raw_channels = crawl_cfg.get("channel_ids") or crawl_cfg.get("channels") or []
        if isinstance(raw_channels, str):
            raw_channels = [s.strip() for s in raw_channels.split(",") if s.strip()]
        channels = tuple(str(c).strip() for c in raw_channels if str(c).strip())
        if not channels:
            raise ValueError(
                "slack connector requires crawl_config.channel_ids (e.g. ['C0123ABC'])",
            )

        # ``days_back=0`` 은 "무한" — ``or`` 로 채우면 0 이 30 으로 변경됨.
        days_back_raw = crawl_cfg.get("days_back")
        days_back = (
            int(days_back_raw) if days_back_raw is not None else _DEFAULT_DAYS_BACK
        )
        return cls(
            auth_token=token,
            channel_ids=channels,
            days_back=days_back,
            include_threads=bool(crawl_cfg.get("include_threads", True)),
            include_bot_messages=bool(crawl_cfg.get("include_bot_messages", False)),
            page_size=min(
                int(crawl_cfg.get("page_size") or _DEFAULT_PAGE_SIZE), _DEFAULT_PAGE_SIZE,
            ),
            name=str(source.get("name") or ""),
            metadata_extra=dict(source.get("metadata") or {}),
        )
