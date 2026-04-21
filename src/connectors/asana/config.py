"""Asana connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AsanaConnectorConfig:
    """Resolved configuration for an Asana-backed data source.

    Attributes:
        auth_token: Asana PAT (per-user).
        workspace_gid: workspace gid (project_gids 가 비어있을 때 필수).
            workspace 안의 모든 task 가져옴 — 보통 너무 많으니 project_gids
            사용 권장.
        project_gids: 특정 project 들만. 비어있으면 workspace 전체.
        days_back: ``modified_since`` 적용 (default 30, 0 = 무한).
        include_comments: stories(comments+activity) 포함 (default True).
        include_completed: 완료된 task 포함 (default True).
        max_tasks: 최대 task 수 (default 500).
        name: human readable.
    """

    auth_token: str
    workspace_gid: str = ""
    project_gids: tuple[str, ...] = ()
    days_back: int = 30
    include_comments: bool = True
    include_completed: bool = True
    max_tasks: int = 500
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> AsanaConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}

        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "asana connector requires auth_token (본인 PAT, SecretBox)",
            )

        workspace_gid = str(crawl_cfg.get("workspace_gid") or "").strip()

        raw_projects = crawl_cfg.get("project_gids") or []
        if isinstance(raw_projects, str):
            raw_projects = [s.strip() for s in raw_projects.split(",") if s.strip()]
        project_gids = tuple(str(p).strip() for p in raw_projects if str(p).strip())

        if not workspace_gid and not project_gids:
            raise ValueError(
                "asana connector requires either workspace_gid or project_gids",
            )

        # ``days_back=0`` 은 "무한" — explicit None 체크.
        days_back_raw = crawl_cfg.get("days_back")
        days_back = int(days_back_raw) if days_back_raw is not None else 30

        return cls(
            auth_token=token,
            workspace_gid=workspace_gid,
            project_gids=project_gids,
            days_back=days_back,
            include_comments=bool(crawl_cfg.get("include_comments", True)),
            include_completed=bool(crawl_cfg.get("include_completed", True)),
            max_tasks=int(crawl_cfg.get("max_tasks") or 500),
            name=str(source.get("name") or ""),
        )
