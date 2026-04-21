"""LinearConnector — GraphQL issues + nested comments → RawDocument.

GraphQL 한 쿼리에 issue + assignee + state + labels + comments (nested
connection) 까지 가져와 REST 의 N+1 호출 회피.

Version fingerprint: ``linear:{teams_hash}:{updated_max}``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.models import ConnectorResult, RawDocument

from .client import LinearAPIError, LinearClient
from .config import LinearConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "linear:"

# GraphQL 쿼리 — issues + nested comments. team filter + updatedAt cutoff.
_ISSUES_QUERY = """
query Issues($filter: IssueFilter!, $first: Int!, $after: String) {
  issues(filter: $filter, first: $first, after: $after, orderBy: updatedAt) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id identifier title description
      url updatedAt createdAt
      state { name type }
      assignee { name email }
      creator { name email }
      labels(first: 20) { nodes { name } }
      team { key name }
      comments(first: 50) {
        nodes {
          body createdAt
          user { name }
        }
      }
    }
  }
}
"""


class LinearConnector:
    """Linear issue crawler — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "linear"

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
            cfg = LinearConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        # Linear IssueFilter 빌드 — team key + updatedAt
        issue_filter: dict[str, Any] = {}
        if cfg.team_keys:
            issue_filter["team"] = {"key": {"in": list(cfg.team_keys)}}
        if cfg.days_back > 0:
            since = datetime.now(UTC) - timedelta(days=cfg.days_back)
            issue_filter["updatedAt"] = {"gte": since.strftime("%Y-%m-%dT%H:%M:%S.000Z")}

        documents: list[RawDocument] = []
        latest_dt: datetime | None = None
        cursor: str | None = None
        count = 0

        async with LinearClient(cfg.auth_token) as client:
            try:
                while count < cfg.max_issues:
                    page_size = min(50, cfg.max_issues - count)
                    page = await client.query(_ISSUES_QUERY, {
                        "filter": issue_filter,
                        "first": page_size,
                        "after": cursor,
                    })
                    issues_block = page.get("issues") or {}
                    nodes = issues_block.get("nodes") or []
                    for node in nodes:
                        doc = _build_document(node, cfg)
                        if doc is None:
                            continue
                        documents.append(doc)
                        count += 1
                        if doc.updated_at and (
                            latest_dt is None or doc.updated_at > latest_dt
                        ):
                            latest_dt = doc.updated_at
                        if count >= cfg.max_issues:
                            break

                    info = issues_block.get("pageInfo") or {}
                    if not info.get("hasNextPage") or count >= cfg.max_issues:
                        break
                    cursor = info.get("endCursor")
                    if not cursor:
                        break
            except LinearAPIError as exc:
                return ConnectorResult(
                    success=False, source_type=self.source_type,
                    error=str(exc), documents=documents,
                )

        teams_hash = hashlib.sha256(
            ",".join(sorted(cfg.team_keys)).encode("utf-8"),
        ).hexdigest()[:8]
        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{teams_hash}:"
            f"{latest_dt.isoformat() if latest_dt else 'empty'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "team_keys": list(cfg.team_keys),
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


def _build_document(
    node: dict[str, Any], cfg: LinearConnectorConfig,
) -> RawDocument | None:
    issue_id = str(node.get("id") or "")
    identifier = str(node.get("identifier") or "")  # 예: ENG-123
    title = str(node.get("title") or "(no title)")
    description = str(node.get("description") or "").strip()
    url = str(node.get("url") or "")
    state = ((node.get("state") or {}).get("name") or "")
    assignee = ((node.get("assignee") or {}).get("name") or "")
    creator = ((node.get("creator") or {}).get("name") or "")
    team_key = ((node.get("team") or {}).get("key") or "")
    labels = [
        str(lbl.get("name") or "")
        for lbl in ((node.get("labels") or {}).get("nodes") or [])
        if isinstance(lbl, dict)
    ]
    label_str = ", ".join(label for label in labels if label)

    pieces: list[str] = [f"# {identifier}: {title}"]
    pieces.append(f"State: {state} · Team: {team_key} · Assignee: {assignee or '—'}")
    if label_str:
        pieces.append(f"Labels: {label_str}")
    if description:
        pieces.append("")
        pieces.append(description)

    if cfg.include_comments:
        comments = (node.get("comments") or {}).get("nodes") or []
        if comments:
            pieces.append("")
            pieces.append("## Comments")
            for c in comments:
                if not isinstance(c, dict):
                    continue
                cu = ((c.get("user") or {}).get("name") or "")
                ct = str(c.get("createdAt") or "")
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

    updated = _parse_iso_date(node.get("updatedAt"))
    return RawDocument(
        doc_id=f"linear:{issue_id}",
        title=f"{identifier}: {title}" if identifier else title,
        content=full,
        source_uri=url,
        author=creator or assignee,
        updated_at=updated,
        content_hash=RawDocument.sha256(full),
        metadata={
            "source_type": "linear",
            "issue_id": issue_id,
            "identifier": identifier,
            "team_key": team_key,
            "state": state,
            "labels": labels,
            "knowledge_type": cfg.name or "linear",
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
