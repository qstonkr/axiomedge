"""BoxConnector — folder BFS + file download → RawDocument."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.models import ConnectorResult, RawDocument
from src.pipelines.document_parser import parse_file

from .client import BoxAPIError, BoxClient
from .config import BoxConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "box:"


class BoxConnector:
    """Box file crawler — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "box"

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
            cfg = BoxConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        documents: list[RawDocument] = []
        last_modified_max: datetime | None = None
        files_visited = 0
        folders_visited: set[str] = set()

        async with BoxClient(cfg.auth_token) as client:
            queue: deque[str] = deque([cfg.folder_id])

            try:
                while queue and files_visited < cfg.max_files:
                    folder_id = queue.popleft()
                    if folder_id in folders_visited:
                        continue
                    folders_visited.add(folder_id)

                    items = []
                    try:
                        async for entry in client.folder_items(folder_id):
                            items.append(entry)
                    except BoxAPIError as e:
                        if e.status in (403, 404):
                            logger.warning(
                                "box: skip folder %s (%d)", folder_id, e.status,
                            )
                            continue
                        raise

                    for item in items:
                        if files_visited >= cfg.max_files:
                            break
                        item_type = str(item.get("type") or "")
                        if item_type == "folder":
                            if cfg.recursive:
                                queue.append(str(item.get("id")))
                            continue
                        if item_type != "file":
                            continue

                        name = str(item.get("name") or "")
                        ext = Path(name).suffix.lower()
                        if cfg.include_extensions and ext not in cfg.include_extensions:
                            continue

                        files_visited += 1
                        try:
                            doc = await self._fetch_file(client, cfg, item)
                        except (BoxAPIError, OSError, RuntimeError) as e:
                            logger.warning(
                                "box: fetch failed for %s: %s", name, e,
                            )
                            continue
                        if doc is None:
                            continue
                        documents.append(doc)
                        modified = _parse_iso_date(item.get("modified_at"))
                        if modified and (
                            last_modified_max is None
                            or modified > last_modified_max
                        ):
                            last_modified_max = modified
            except BoxAPIError as exc:
                return ConnectorResult(
                    success=False, source_type=self.source_type,
                    error=str(exc), documents=documents,
                )

        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{cfg.folder_id}:"
            f"{last_modified_max.isoformat() if last_modified_max else 'empty'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "folder_id": cfg.folder_id,
                "files_visited": files_visited,
                "folders_visited": len(folders_visited),
                "documents_emitted": len(documents),
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

    async def _fetch_file(
        self,
        client: BoxClient,
        cfg: BoxConnectorConfig,
        item: dict[str, Any],
    ) -> RawDocument | None:
        file_id = str(item.get("id") or "")
        name = str(item.get("name") or f"file-{file_id}")
        size = int(item.get("size") or 0)
        if size > 50 * 1024 * 1024:
            logger.info("box: skip oversized %s (%d bytes)", name, size)
            return None

        data = await client.file_content(file_id)
        ext = Path(name).suffix.lower() or ".bin"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            text = await asyncio.to_thread(parse_file, tmp_path)
            body = (text or "").strip()
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

        if not body:
            return None

        modified = _parse_iso_date(item.get("modified_at"))
        author = ((item.get("created_by") or {}).get("name") or "")
        return RawDocument(
            doc_id=f"box:{file_id}",
            title=name,
            content=body,
            source_uri=f"https://app.box.com/file/{file_id}",
            author=author,
            updated_at=modified,
            content_hash=RawDocument.sha256(body),
            metadata={
                "source_type": "box",
                "file_id": file_id,
                "file_name": name,
                "file_ext": ext,
                "file_size_bytes": size,
                "knowledge_type": cfg.name or "box",
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
