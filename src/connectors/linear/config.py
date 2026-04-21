"""Linear connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class LinearConnectorConfig:
    """Resolved configuration for a Linear-backed data source.

    Attributes:
        auth_token: Linear API key (per-user, ``lin_api_...``).
        team_keys: 동기화할 team key list (예: ``("ENG", "DESIGN")``).
            비어있으면 모든 team. team key 는 issue identifier prefix
            (``ENG-123`` 의 ``ENG``).
        days_back: ``updatedAt`` filter (default 30, 0 = 무한).
        include_comments: comments 포함 (default True).
        max_issues: 최대 issue 수 (default 500).
        name: human readable.
    """

    auth_token: str
    team_keys: tuple[str, ...] = ()
    days_back: int = 30
    include_comments: bool = True
    max_issues: int = 500
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> LinearConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}

        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "linear connector requires auth_token (본인 API key, SecretBox)",
            )

        raw_keys = crawl_cfg.get("team_keys") or []
        if isinstance(raw_keys, str):
            raw_keys = [s.strip() for s in raw_keys.split(",") if s.strip()]
        team_keys = tuple(
            str(k).strip().upper() for k in raw_keys if str(k).strip()
        )

        # ``days_back=0`` 은 "무한" — explicit None 체크.
        days_back_raw = crawl_cfg.get("days_back")
        days_back = int(days_back_raw) if days_back_raw is not None else 30

        return cls(
            auth_token=token,
            team_keys=team_keys,
            days_back=days_back,
            include_comments=bool(crawl_cfg.get("include_comments", True)),
            max_issues=int(crawl_cfg.get("max_issues") or 500),
            name=str(source.get("name") or ""),
        )
