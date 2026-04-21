"""GitHubIssuesConnector — repos 의 issues + comments → RawDocument.

GitHub API 의 ``/issues`` endpoint 가 PR 도 같이 반환 — ``pull_request`` field
로 구분. ``include_prs=False`` 면 PR 은 skip.

Version fingerprint: ``github_issues:{repos_hash}:{updated_max}``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.models import ConnectorResult, RawDocument

from .client import GitHubAPIError, GitHubClient
from .config import GitHubIssuesConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "github_issues:"


class GitHubIssuesConnector:
    """GitHub Issues / PR crawler — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "github_issues"

    async def health_check(self) -> bool:
        return True

    async def fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,  # noqa: ARG002
        last_fingerprint: str | None = None,  # noqa: ARG002
    ) -> ConnectorResult:
        try:
            cfg = GitHubIssuesConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        since: str | None = None
        if cfg.days_back > 0:
            since_dt = datetime.now(UTC) - timedelta(days=cfg.days_back)
            since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        documents: list[RawDocument] = []
        latest_dt: datetime | None = None
        skipped_repos: list[str] = []

        async with GitHubClient(
            cfg.auth_token, api_base_url=cfg.api_base_url,
        ) as client:
            for repo_full in cfg.repos:
                owner, repo = repo_full.split("/", 1)
                try:
                    repo_docs, repo_latest = await self._fetch_repo(
                        client, cfg, owner, repo, since,
                    )
                except GitHubAPIError as e:
                    if e.status in (403, 404):
                        logger.warning(
                            "github_issues: skip %s (%d)", repo_full, e.status,
                        )
                        skipped_repos.append(repo_full)
                        continue
                    return ConnectorResult(
                        success=False, source_type=self.source_type,
                        error=f"{repo_full}: {e}", documents=documents,
                    )
                documents.extend(repo_docs)
                if repo_latest and (latest_dt is None or repo_latest > latest_dt):
                    latest_dt = repo_latest

        repos_hash = hashlib.sha256(
            ",".join(sorted(cfg.repos)).encode("utf-8"),
        ).hexdigest()[:8]
        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{repos_hash}:"
            f"{latest_dt.isoformat() if latest_dt else 'empty'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "repos_total": len(cfg.repos),
                "repos_skipped": skipped_repos,
                "issues_emitted": len(documents),
            },
        )

    async def lazy_fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,
        last_fingerprint: str | None = None,
    ) -> AsyncIterator[RawDocument]:
        result = await self.fetch(
            config, force=force, last_fingerprint=last_fingerprint,
        )
        if not result.success or result.skipped:
            return
        for doc in result.documents:
            yield doc

    async def _fetch_repo(
        self,
        client: GitHubClient,
        cfg: GitHubIssuesConnectorConfig,
        owner: str,
        repo: str,
        since: str | None,
    ) -> tuple[list[RawDocument], datetime | None]:
        documents: list[RawDocument] = []
        latest: datetime | None = None
        count = 0

        async for issue in client.list_issues(
            owner, repo, state=cfg.state, since=since,
        ):
            if count >= cfg.max_issues_per_repo:
                break
            is_pr = bool(issue.get("pull_request"))
            if is_pr and not cfg.include_prs:
                continue

            number = int(issue.get("number") or 0)
            comments: list[dict[str, Any]] = []
            if cfg.include_comments and (issue.get("comments") or 0) > 0:
                try:
                    comments = await client.list_issue_comments(owner, repo, number)
                except GitHubAPIError as e:
                    logger.warning(
                        "github_issues: comments fetch failed for %s/%s#%d: %s",
                        owner, repo, number, e,
                    )

            doc = _build_document(owner, repo, issue, comments, cfg, is_pr=is_pr)
            if doc is None:
                continue
            documents.append(doc)
            count += 1
            updated = _parse_iso_date(issue.get("updated_at"))
            if updated and (latest is None or updated > latest):
                latest = updated

        return documents, latest


def _build_document(
    owner: str,
    repo: str,
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    cfg: GitHubIssuesConnectorConfig,
    *,
    is_pr: bool,
) -> RawDocument | None:
    number = int(issue.get("number") or 0)
    title = str(issue.get("title") or f"#{number}")
    body = str(issue.get("body") or "")
    state = str(issue.get("state") or "")
    user = ((issue.get("user") or {}).get("login") or "")
    web_url = str(issue.get("html_url") or "")
    labels = [
        str(lbl.get("name") or "")
        for lbl in (issue.get("labels") or [])
        if isinstance(lbl, dict)
    ]
    label_str = ", ".join(label for label in labels if label)

    kind = "PR" if is_pr else "Issue"
    pieces: list[str] = [f"# {kind} #{number}: {title}"]
    pieces.append(f"State: {state} · Author: {user}")
    if label_str:
        pieces.append(f"Labels: {label_str}")
    if body.strip():
        pieces.append("")
        pieces.append(body.strip())

    if comments:
        pieces.append("")
        pieces.append("## Comments")
        for c in comments:
            if not isinstance(c, dict):
                continue
            cu = ((c.get("user") or {}).get("login") or "")
            ct = str(c.get("created_at") or "")
            cb = str(c.get("body") or "").strip()
            if not cb:
                continue
            stamp = ct[:16].replace("T", " ") if ct else ""
            pieces.append(f"**{cu}** [{stamp}]")
            pieces.append(cb)
            pieces.append("")

    full = "\n".join(p for p in pieces if p is not None).strip()
    if not full:
        return None

    updated = _parse_iso_date(issue.get("updated_at"))
    return RawDocument(
        doc_id=f"github_issues:{owner}/{repo}#{number}",
        title=f"[{owner}/{repo}#{number}] {title}",
        content=full,
        source_uri=web_url,
        author=user,
        updated_at=updated,
        content_hash=RawDocument.sha256(full),
        metadata={
            "source_type": "github_issues",
            "owner": owner,
            "repo": repo,
            "number": number,
            "state": state,
            "is_pr": is_pr,
            "labels": labels,
            "knowledge_type": cfg.name or "github_issues",
        },
    )


def _parse_iso_date(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
