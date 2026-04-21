"""AsanaConnector — tasks + stories → RawDocument.

각 task 의 notes (description) + stories (comments + activity) 결합. 1 task =
1 RawDocument. workspace 또는 project 별 task list 가져옴 (project_gids
지정 권장 — workspace 전체는 보통 너무 큼).

Version fingerprint: ``asana:{scope_hash}:{modified_max}``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.models import ConnectorResult, RawDocument

from .client import AsanaAPIError, AsanaClient
from .config import AsanaConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "asana:"

# Task 가져올 때 명시적 fields — 안 명시하면 gid + name 만 옴.
_TASK_FIELDS = (
    "name,notes,completed,modified_at,created_at,due_on,"
    "assignee.name,assignee.email,projects.name,tags.name,permalink_url"
)


class AsanaConnector:
    """Asana task crawler — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "asana"

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
            cfg = AsanaConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        modified_since: str | None = None
        if cfg.days_back > 0:
            since_dt = datetime.now(UTC) - timedelta(days=cfg.days_back)
            modified_since = since_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        documents: list[RawDocument] = []
        latest_dt: datetime | None = None

        async with AsanaClient(cfg.auth_token) as client:
            scopes = cfg.project_gids or (cfg.workspace_gid,)
            try:
                count = 0
                for scope in scopes:
                    if cfg.project_gids:
                        list_path = f"/projects/{scope}/tasks"
                        list_params: dict[str, Any] = {
                            "opt_fields": _TASK_FIELDS,
                        }
                    else:
                        list_path = "/tasks"
                        list_params = {
                            "workspace": scope,
                            "assignee": "me",  # workspace 전체는 너무 — 본인 task 만
                            "opt_fields": _TASK_FIELDS,
                        }
                    if modified_since:
                        list_params["modified_since"] = modified_since
                    if not cfg.include_completed:
                        list_params["completed_since"] = "now"

                    async for task in client.iterate_pages(
                        list_path, params=list_params,
                    ):
                        if count >= cfg.max_tasks:
                            break

                        comments: list[dict[str, Any]] = []
                        if cfg.include_comments:
                            try:
                                stories_path = f"/tasks/{task['gid']}/stories"
                                async for story in client.iterate_pages(
                                    stories_path,
                                    params={"opt_fields": "type,text,created_at,created_by.name"},
                                ):
                                    if str(story.get("type")) == "comment":
                                        comments.append(story)
                            except AsanaAPIError as e:
                                logger.warning(
                                    "asana stories fetch failed for %s: %s",
                                    task.get("gid"), e,
                                )

                        doc = _build_document(task, comments, cfg)
                        if doc is None:
                            continue
                        documents.append(doc)
                        count += 1
                        if doc.updated_at and (
                            latest_dt is None or doc.updated_at > latest_dt
                        ):
                            latest_dt = doc.updated_at

                    if count >= cfg.max_tasks:
                        break
            except AsanaAPIError as exc:
                return ConnectorResult(
                    success=False, source_type=self.source_type,
                    error=str(exc), documents=documents,
                )

        scope_str = ",".join(scopes)
        scope_hash = hashlib.sha256(scope_str.encode("utf-8")).hexdigest()[:8]
        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{scope_hash}:"
            f"{latest_dt.isoformat() if latest_dt else 'empty'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "scopes": list(scopes),
                "scope_kind": "project" if cfg.project_gids else "workspace",
                "tasks_emitted": len(documents),
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
    task: dict[str, Any],
    comments: list[dict[str, Any]],
    cfg: AsanaConnectorConfig,
) -> RawDocument | None:
    gid = str(task.get("gid") or "")
    name = str(task.get("name") or "(no name)")
    notes = str(task.get("notes") or "").strip()
    completed = bool(task.get("completed", False))
    assignee = ((task.get("assignee") or {}).get("name") or "")
    due = str(task.get("due_on") or "")
    permalink = str(task.get("permalink_url") or "")

    pieces: list[str] = [f"# Task: {name}"]
    pieces.append(f"Status: {'completed' if completed else 'in progress'}")
    if assignee:
        pieces.append(f"Assignee: {assignee}")
    if due:
        pieces.append(f"Due: {due}")
    if notes:
        pieces.append("")
        pieces.append(notes)

    if comments:
        pieces.append("")
        pieces.append("## Comments")
        for c in comments:
            author = ((c.get("created_by") or {}).get("name") or "")
            ts = str(c.get("created_at") or "")
            text = str(c.get("text") or "").strip()
            if not text:
                continue
            stamp = ts[:16].replace("T", " ") if ts else ""
            pieces.append(f"**{author}** [{stamp}]")
            pieces.append(text)
            pieces.append("")

    full = "\n".join(p for p in pieces if p is not None).strip()
    if not full:
        return None

    modified = _parse_iso_date(task.get("modified_at"))
    return RawDocument(
        doc_id=f"asana:{gid}",
        title=name,
        content=full,
        source_uri=permalink,
        author=assignee,
        updated_at=modified,
        content_hash=RawDocument.sha256(full),
        metadata={
            "source_type": "asana",
            "task_gid": gid,
            "completed": completed,
            "knowledge_type": cfg.name or "asana",
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
