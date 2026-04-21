"""Notion connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_DEFAULT_MAX_DEPTH = 5
_DEFAULT_PAGE_SIZE = 100
_MAX_PAGE_SIZE = 100  # Notion API 상한


@dataclass
class NotionConnectorConfig:
    """Resolved configuration for a Notion-backed data source.

    Attributes:
        auth_token: Notion Internal Integration token (``secret_xxx``).
            SecretBox 우선 — connector launcher 가 inject.
        root_page_id: BFS 시작점 page ID (UUID — hyphen 유무 무관).
        max_depth: 자식 페이지 재귀 깊이 (default 5). 무한 루프 방지.
        include_archived: archived 페이지/블록 포함 여부 (default False).
        page_size: API page size (max 100).
        name: Human readable source name.
    """

    auth_token: str
    root_page_id: str
    max_depth: int = _DEFAULT_MAX_DEPTH
    include_archived: bool = False
    page_size: int = _DEFAULT_PAGE_SIZE
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> NotionConnectorConfig:
        """Build a config from a data_source dict (crawl_config + metadata)."""
        crawl_cfg = source.get("crawl_config") or {}
        metadata = source.get("metadata") or {}

        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "notion connector requires auth_token (SecretBox 에 등록 후 자동 inject)",
            )

        root_page_id = (
            crawl_cfg.get("root_page_id")
            or metadata.get("root_page_id")
            or crawl_cfg.get("page_id")
            or ""
        )
        root_page_id = str(root_page_id).strip().replace("-", "")
        if not root_page_id:
            raise ValueError("notion connector requires crawl_config.root_page_id")

        max_depth = int(crawl_cfg.get("max_depth") or _DEFAULT_MAX_DEPTH)
        page_size = min(
            int(crawl_cfg.get("page_size") or _DEFAULT_PAGE_SIZE), _MAX_PAGE_SIZE,
        )

        return cls(
            auth_token=token,
            root_page_id=root_page_id,
            max_depth=max_depth,
            include_archived=bool(crawl_cfg.get("include_archived", False)),
            page_size=page_size,
            name=str(source.get("name") or ""),
        )
