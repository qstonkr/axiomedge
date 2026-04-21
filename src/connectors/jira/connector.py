"""JiraConnector — JQL 검색 → issues + comments → RawDocument.

ADF (Atlassian Document Format) 재귀 text 추출 — v3 API 의 description/
comment body 가 ADF JSON. v2 는 wiki markup string (그대로 사용).

Version fingerprint: ``jira:{base_url}:{jql_hash}:{updated_max}``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from src.core.models import ConnectorResult, RawDocument

from .client import JiraAPIError, JiraClient
from .config import JiraConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "jira:"


class JiraConnector:
    """Jira issue crawler — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "jira"

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
            cfg = JiraConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        if not cfg.jql:
            logger.warning(
                "jira: empty jql — fetching all accessible issues "
                "(권장: 'project = X AND updated >= -30d' 같은 필터)",
            )

        documents: list[RawDocument] = []
        latest_dt: datetime | None = None
        count = 0

        async with JiraClient(
            base_url=cfg.base_url,
            auth_token=cfg.auth_token,
            email=cfg.email,
            api_version=cfg.api_version,
        ) as client:
            try:
                async for issue in client.search_issues(
                    cfg.jql, max_results=min(cfg.max_issues, 100),
                ):
                    if count >= cfg.max_issues:
                        break
                    doc = _build_document(issue, cfg)
                    if doc is None:
                        continue
                    documents.append(doc)
                    count += 1
                    if doc.updated_at and (
                        latest_dt is None or doc.updated_at > latest_dt
                    ):
                        latest_dt = doc.updated_at
            except JiraAPIError as exc:
                return ConnectorResult(
                    success=False, source_type=self.source_type,
                    error=str(exc), documents=documents,
                )

        jql_hash = hashlib.sha256(cfg.jql.encode("utf-8")).hexdigest()[:8]
        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{cfg.base_url}:{jql_hash}:"
            f"{latest_dt.isoformat() if latest_dt else 'empty'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "base_url": cfg.base_url,
                "jql": cfg.jql,
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


# ---------------------------------------------------------------------------
# Helpers — issue → RawDocument + ADF text 추출
# ---------------------------------------------------------------------------


def _build_document(
    issue: dict[str, Any], cfg: JiraConnectorConfig,
) -> RawDocument | None:
    key = str(issue.get("key") or "")
    issue_id = str(issue.get("id") or "")
    fields = issue.get("fields") or {}
    summary = str(fields.get("summary") or "(no summary)")
    description = _body_to_text(fields.get("description"), cfg.api_version)
    status_name = ((fields.get("status") or {}).get("name") or "")
    reporter = ""
    rep = fields.get("reporter") or {}
    if isinstance(rep, dict):
        reporter = str(
            rep.get("displayName") or rep.get("emailAddress") or rep.get("name") or "",
        )
    updated = _parse_iso_date(fields.get("updated"))

    pieces: list[str] = [f"# [{key}] {summary}"]
    if status_name:
        pieces.append(f"Status: {status_name}")
    if description:
        pieces.append("")
        pieces.append(description)

    if cfg.include_comments:
        comments_field = fields.get("comment") or {}
        comments = comments_field.get("comments") if isinstance(comments_field, dict) else []
        if comments:
            pieces.append("")
            pieces.append("## Comments")
            for c in comments:
                if not isinstance(c, dict):
                    continue
                author = ""
                a = c.get("author") or {}
                if isinstance(a, dict):
                    author = str(a.get("displayName") or a.get("name") or "")
                created = str(c.get("created") or "")
                body = _body_to_text(c.get("body"), cfg.api_version)
                if not body:
                    continue
                stamp = created[:16].replace("T", " ") if created else ""
                pieces.append(f"**{author}** [{stamp}]")
                pieces.append(body)
                pieces.append("")

    full = "\n".join(p for p in pieces if p is not None).strip()
    if not full:
        return None

    web_url = f"{cfg.base_url}/browse/{key}"
    return RawDocument(
        doc_id=f"jira:{key}",
        title=f"[{key}] {summary}",
        content=full,
        source_uri=web_url,
        author=reporter,
        updated_at=updated,
        content_hash=RawDocument.sha256(full),
        metadata={
            "source_type": "jira",
            "issue_key": key,
            "issue_id": issue_id,
            "status": status_name,
            "knowledge_type": cfg.name or "jira",
        },
    )


def _body_to_text(body: Any, api_version: str) -> str:
    """Description/comment body → text.

    - v3: ADF JSON ({type, content: [...]}) → 재귀 text 수집
    - v2: wiki markup string → 그대로 사용
    - None: 빈 string
    """
    if body is None:
        return ""
    if api_version == "2" or isinstance(body, str):
        return str(body).strip()
    if not isinstance(body, dict):
        return ""
    return _adf_to_text(body).strip()


def _adf_to_text(node: Any) -> str:
    """ADF (Atlassian Document Format) 재귀 → plain text.

    paragraph/heading/bullet_list 사이에 ``\\n\\n`` 추가. text node 의 ``text``
    field 만 수집. 미지원 타입은 자식 재귀.
    """
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""

    t = node.get("type")
    if t == "text":
        return str(node.get("text") or "")
    if t == "hardBreak":
        return "\n"

    children: list[str] = []
    for child in node.get("content") or []:
        children.append(_adf_to_text(child))

    text = "".join(children)
    # block-level types 사이에 빈 줄
    if t in ("paragraph", "heading", "blockquote", "codeBlock",
             "bulletList", "orderedList", "listItem"):
        return text + "\n\n"
    return text


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
