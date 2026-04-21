"""Gmail connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GmailConnectorConfig:
    """Resolved configuration for a Gmail-backed data source.

    Attributes:
        auth_token: service account JSON (domain-wide delegation, ``subject``
            field 에 impersonate 할 user 지정) 또는 raw access_token.
        user_id: ``me`` (token owner) 또는 이메일 (service account 만 가능).
        query: Gmail 검색 query (예: ``from:boss after:2026-01-01``).
        max_messages: 최대 메시지 수 (default 200).
        include_body: body 본문 포함 여부 (default True). False 면 subject + snippet 만.
        name: human readable.
    """

    auth_token: str
    user_id: str = "me"
    query: str = ""
    max_messages: int = 200
    include_body: bool = True
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> GmailConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}
        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "gmail connector requires auth_token (shared SecretBox 에 admin 등록 필요)",
            )

        return cls(
            auth_token=token,
            user_id=str(crawl_cfg.get("user_id") or "me").strip(),
            query=str(crawl_cfg.get("query") or "").strip(),
            max_messages=int(crawl_cfg.get("max_messages") or 200),
            include_body=bool(crawl_cfg.get("include_body", True)),
            name=str(source.get("name") or ""),
        )
