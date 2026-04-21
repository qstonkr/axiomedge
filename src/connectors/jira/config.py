"""Jira connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class JiraConnectorConfig:
    """Resolved configuration for a Jira-backed data source.

    Attributes:
        auth_token: API token (Cloud) 또는 PAT (Server/DC). per-user — 사용자
            본인이 발급.
        base_url: Jira base URL — ``https://{domain}.atlassian.net`` (Cloud) 또는
            ``https://jira.example.com`` (self-hosted).
        email: Cloud 의 경우 Atlassian 계정 이메일. 비어있으면 Server/DC PAT
            모드 (Bearer).
        jql: JQL 검색 query (default 빈 string = 전체 — 권장 안 함).
            예: ``project = ENG AND updated >= -30d``.
        api_version: ``3`` (Cloud, default — ADF body) 또는 ``2`` (Server/DC,
            wiki markup body).
        max_issues: 최대 issue 수 (default 200).
        include_comments: comments 포함 여부 (default True).
        name: human readable.
    """

    auth_token: str
    base_url: str
    email: str = ""
    jql: str = ""
    api_version: str = "3"
    max_issues: int = 200
    include_comments: bool = True
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> JiraConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}

        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "jira connector requires auth_token (본인 PAT, SecretBox 자동 inject)",
            )

        base_url = str(crawl_cfg.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            raise ValueError(
                "jira connector requires crawl_config.base_url "
                "(예: https://your-domain.atlassian.net)",
            )

        api_version = str(crawl_cfg.get("api_version") or "3").strip()
        if api_version not in ("2", "3"):
            raise ValueError(f"jira api_version must be '2' or '3', got {api_version!r}")

        return cls(
            auth_token=token,
            base_url=base_url,
            email=str(crawl_cfg.get("email") or "").strip(),
            jql=str(crawl_cfg.get("jql") or "").strip(),
            api_version=api_version,
            max_issues=int(crawl_cfg.get("max_issues") or 200),
            include_comments=bool(crawl_cfg.get("include_comments", True)),
            name=str(source.get("name") or ""),
        )
