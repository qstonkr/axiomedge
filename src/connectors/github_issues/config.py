"""GitHub Issues connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GitHubIssuesConnectorConfig:
    """Resolved configuration for a GitHub Issues-backed data source.

    Attributes:
        auth_token: GitHub PAT (classic 또는 fine-grained). per-user.
        repos: ``owner/repo`` list. 여러 repo 한 source 에 묶어도 OK.
        state: ``open``/``closed``/``all`` (default ``all``).
        days_back: 며칠 전까지 (default 90, 0 = 무한). ``since`` query 로 적용.
        include_prs: PR 본문 포함 여부 (default True). False 면 issue 만.
        include_comments: comment 포함 여부 (default True).
        max_issues_per_repo: repo 당 최대 issue 수 (default 500).
        api_base_url: ``https://api.github.com`` (Cloud, default) 또는
            GitHub Enterprise base URL.
        name: human readable.
    """

    auth_token: str
    repos: tuple[str, ...]
    state: str = "all"
    days_back: int = 90
    include_prs: bool = True
    include_comments: bool = True
    max_issues_per_repo: int = 500
    api_base_url: str = "https://api.github.com"
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> GitHubIssuesConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}

        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "github_issues connector requires auth_token (본인 PAT, SecretBox)",
            )

        raw_repos = crawl_cfg.get("repos") or []
        if isinstance(raw_repos, str):
            raw_repos = [s.strip() for s in raw_repos.split(",") if s.strip()]
        repos: list[str] = []
        for r in raw_repos:
            r = str(r).strip().strip("/")
            if not r:
                continue
            if "/" not in r or r.count("/") != 1:
                raise ValueError(
                    f"github_issues repo must be 'owner/repo' format, got {r!r}",
                )
            repos.append(r)
        if not repos:
            raise ValueError(
                "github_issues connector requires crawl_config.repos "
                "(예: ['owner/repo-1', 'owner/repo-2'])",
            )

        state = str(crawl_cfg.get("state") or "all").strip().lower()
        if state not in ("open", "closed", "all"):
            raise ValueError(
                f"github_issues state must be 'open'/'closed'/'all', got {state!r}",
            )

        # ``days_back=0`` 은 "무한" 의미 — ``or`` 로 default 채우면 안 됨.
        days_back_raw = crawl_cfg.get("days_back")
        days_back = int(days_back_raw) if days_back_raw is not None else 90

        return cls(
            auth_token=token,
            repos=tuple(repos),
            state=state,
            days_back=days_back,
            include_prs=bool(crawl_cfg.get("include_prs", True)),
            include_comments=bool(crawl_cfg.get("include_comments", True)),
            max_issues_per_repo=int(crawl_cfg.get("max_issues_per_repo") or 500),
            api_base_url=(
                str(crawl_cfg.get("api_base_url") or "https://api.github.com")
                .strip().rstrip("/")
            ),
            name=str(source.get("name") or ""),
        )
