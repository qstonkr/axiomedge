"""Outlook connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OutlookConnectorConfig:
    """Resolved configuration for an Outlook-backed data source.

    Attributes:
        auth_token: MSGraph app-only access token (shared SecretBox).
        user_id: ``me`` (token owner) 또는 이메일/object ID (app-only 모드).
        folder: mail folder. ``inbox``, ``sentitems``, ``drafts``, ``deleteditems``,
            ``junkemail``, ``archive`` 또는 임의 folder ID.
        days_back: 며칠 전까지 (default 30). 0 = 무한.
        include_body: body 본문 포함 여부 (default True).
        max_messages: 최대 메시지 수 (default 200).
        name: human readable.
    """

    auth_token: str
    user_id: str = "me"
    folder: str = "inbox"
    days_back: int = 30
    include_body: bool = True
    max_messages: int = 200
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> OutlookConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}
        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "outlook connector requires auth_token (shared SecretBox 에 admin 등록 필요)",
            )

        # ``days_back=0`` 은 "무한" 의미라 ``or`` 로 default 채우면 안 됨 — 명시 None 체크.
        days_back_raw = crawl_cfg.get("days_back")
        days_back = int(days_back_raw) if days_back_raw is not None else 30
        return cls(
            auth_token=token,
            user_id=str(crawl_cfg.get("user_id") or "me").strip(),
            folder=str(crawl_cfg.get("folder") or "inbox").strip(),
            days_back=days_back,
            include_body=bool(crawl_cfg.get("include_body", True)),
            max_messages=int(crawl_cfg.get("max_messages") or 200),
            name=str(source.get("name") or ""),
        )
