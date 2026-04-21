"""DropboxConnector — folder list + per-file download → RawDocument."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.models import ConnectorResult, RawDocument
from src.pipelines.document_parser import parse_file

from .client import DropboxAPIError, DropboxClient
from .config import DropboxConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "dropbox:"
_FILE_TAG = "file"


class DropboxConnector:
    """Dropbox file crawler — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "dropbox"

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
            cfg = DropboxConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        documents: list[RawDocument] = []
        last_modified_max: datetime | None = None
        files_visited = 0

        async with DropboxClient(cfg.auth_token) as client:
            try:
                async for entry in client.list_folder(
                    cfg.folder_path, recursive=cfg.recursive,
                ):
                    if files_visited >= cfg.max_files:
                        break
                    if str(entry.get(".tag") or "") != _FILE_TAG:
                        continue
                    name = str(entry.get("name") or "")
                    ext = Path(name).suffix.lower()
                    if cfg.include_extensions and ext not in cfg.include_extensions:
                        continue

                    files_visited += 1
                    try:
                        doc = await self._fetch_file(client, cfg, entry)
                    except (DropboxAPIError, OSError, RuntimeError) as e:
                        logger.warning(
                            "dropbox: fetch failed for %s: %s", name, e,
                        )
                        continue
                    if doc is None:
                        continue
                    documents.append(doc)
                    modified = _parse_iso_date(entry.get("server_modified"))
                    if modified and (
                        last_modified_max is None or modified > last_modified_max
                    ):
                        last_modified_max = modified
            except DropboxAPIError as exc:
                return ConnectorResult(
                    success=False, source_type=self.source_type,
                    error=str(exc), documents=documents,
                )

        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{cfg.folder_path or '/'}:"
            f"{last_modified_max.isoformat() if last_modified_max else 'empty'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "folder_path": cfg.folder_path,
                "files_visited": files_visited,
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
        client: DropboxClient,
        cfg: DropboxConnectorConfig,
        entry: dict[str, Any],
    ) -> RawDocument | None:
        path = str(entry.get("path_display") or entry.get("path_lower") or "")
        if not path:
            return None
        size = int(entry.get("size") or 0)
        if size > 50 * 1024 * 1024:
            logger.info("dropbox: skip oversized %s (%d bytes)", path, size)
            return None

        data = await client.download(path)
        ext = Path(path).suffix.lower() or ".bin"
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

        modified = _parse_iso_date(entry.get("server_modified"))
        file_id = str(entry.get("id") or path)
        name = str(entry.get("name") or Path(path).name)
        return RawDocument(
            doc_id=f"dropbox:{file_id}",
            title=name,
            content=body,
            source_uri=f"https://www.dropbox.com/home{path}",
            author="",
            updated_at=modified,
            content_hash=RawDocument.sha256(body),
            metadata={
                "source_type": "dropbox",
                "path": path,
                "file_name": name,
                "file_ext": ext,
                "file_size_bytes": size,
                "knowledge_type": cfg.name or "dropbox",
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
