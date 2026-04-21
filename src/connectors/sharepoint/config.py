"""SharePoint connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SharePointConnectorConfig:
    """Resolved configuration for a SharePoint-backed data source.

    Attributes:
        auth_token: MSGraph app-only access token (admin shared SecretBox 에서
            launcher 가 inject).
        site_id: SharePoint site ID — ``{hostname},{site-collection-id},{site-id}``
            형식 (Graph API 표준). 또는 sites/root, sites/{hostname}:/sites/{name}.
        list_ids: 동기화할 list ID list. 비어있으면 전체 list.
        max_items: 한 list 당 최대 item 수 (default 1000). API throttle 보호.
        name: human readable source name.
    """

    auth_token: str
    site_id: str
    list_ids: tuple[str, ...]
    max_items: int = 1000
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> SharePointConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}

        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "sharepoint connector requires auth_token (shared SecretBox 에 admin 등록 필요)",
            )

        site_id = str(crawl_cfg.get("site_id") or "").strip()
        if not site_id:
            raise ValueError("sharepoint connector requires crawl_config.site_id")

        raw_lists = crawl_cfg.get("list_ids") or []
        if isinstance(raw_lists, str):
            raw_lists = [s.strip() for s in raw_lists.split(",") if s.strip()]
        list_ids = tuple(str(lid).strip() for lid in raw_lists if str(lid).strip())

        return cls(
            auth_token=token,
            site_id=site_id,
            list_ids=list_ids,
            max_items=int(crawl_cfg.get("max_items") or 1000),
            name=str(source.get("name") or ""),
        )
