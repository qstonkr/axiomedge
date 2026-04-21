"""GoogleDriveConnector — Drive v3 API folder BFS + MIME-aware export."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.connectors._google import GoogleAPIError, GoogleClient, resolve_access_token
from src.connectors._google.auth import GoogleAuthError
from src.core.models import ConnectorResult, RawDocument
from src.pipelines.document_parser import parse_file

from .config import GoogleDriveConnectorConfig

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.googleapis.com/drive/v3"
_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
_FINGERPRINT_PREFIX = "google_drive:"

_GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
_GOOGLE_SLIDES_MIME = "application/vnd.google-apps.presentation"
_GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"


class GoogleDriveConnector:
    """Google Drive folder BFS — ``IKnowledgeConnector`` 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "google_drive"

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
            cfg = GoogleDriveConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        try:
            access_token = await resolve_access_token(cfg.auth_token, _SCOPES)
        except GoogleAuthError as e:
            return ConnectorResult(
                success=False, source_type=self.source_type,
                error=f"auth: {e}",
            )

        documents: list[RawDocument] = []
        last_modified_max: datetime | None = None
        files_visited = 0
        folders_visited: set[str] = set()

        async with GoogleClient(access_token, base_url=_BASE_URL) as client:
            queue: deque[str] = deque([cfg.folder_id])

            try:
                while queue and files_visited < cfg.max_files:
                    folder_id = queue.popleft()
                    if folder_id in folders_visited:
                        continue
                    folders_visited.add(folder_id)

                    children = []
                    params = {
                        "q": f"'{folder_id}' in parents and trashed = false",
                        "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,owners,size)",
                        "pageSize": 100,
                    }
                    async for item in client.iterate_pages(
                        "/files", params=params, items_key="files",
                    ):
                        children.append(item)

                    for item in children:
                        if files_visited >= cfg.max_files:
                            break
                        mime = str(item.get("mimeType") or "").lower()

                        if mime == _GOOGLE_FOLDER_MIME:
                            if cfg.recursive:
                                queue.append(item["id"])
                            continue

                        if cfg.include_mime_types and mime not in cfg.include_mime_types:
                            continue

                        files_visited += 1
                        try:
                            doc = await self._fetch_file(client, cfg, item, mime)
                        except (GoogleAPIError, OSError, RuntimeError) as e:
                            logger.warning(
                                "google_drive: fetch failed for %s: %s",
                                item.get("name"), e,
                            )
                            continue
                        if doc is None:
                            continue

                        documents.append(doc)
                        modified = _parse_iso_date(item.get("modifiedTime"))
                        if modified and (
                            last_modified_max is None
                            or modified > last_modified_max
                        ):
                            last_modified_max = modified

            except GoogleAPIError as exc:
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
        client: GoogleClient,
        cfg: GoogleDriveConnectorConfig,
        item: dict[str, Any],
        mime: str,
    ) -> RawDocument | None:
        """File item → RawDocument. Google docs 는 export, 그 외 binary."""
        file_id = item["id"]
        name = str(item.get("name") or f"file-{file_id}")
        size = int(item.get("size") or 0)

        if size > 50 * 1024 * 1024:
            logger.info("google_drive: skip oversized %s (%d bytes)", name, size)
            return None

        body: str | None = None
        ext_used = ""

        if mime in (_GOOGLE_DOC_MIME, _GOOGLE_SLIDES_MIME):
            # Google native — text/plain export
            data = await client.get_raw(
                f"/files/{file_id}/export",
                params={"mimeType": "text/plain"},
            )
            body = data.decode("utf-8", errors="replace").strip()
            ext_used = ".txt"
        else:
            # Binary download → tempfile → parse_file
            data = await client.get_raw(
                f"/files/{file_id}",
                params={"alt": "media"},
            )
            ext_used = Path(name).suffix.lower() or ".bin"
            with tempfile.NamedTemporaryFile(suffix=ext_used, delete=False) as tmp:
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

        modified = _parse_iso_date(item.get("modifiedTime"))
        web_url = str(item.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}")
        owners = item.get("owners") or []
        author = ""
        if owners and isinstance(owners[0], dict):
            author = str(owners[0].get("displayName") or owners[0].get("emailAddress") or "")

        return RawDocument(
            doc_id=f"google_drive:{file_id}",
            title=name,
            content=body,
            source_uri=web_url,
            author=author,
            updated_at=modified,
            content_hash=RawDocument.sha256(body),
            metadata={
                "source_type": "google_drive",
                "file_id": file_id,
                "file_name": name,
                "file_ext": ext_used,
                "mime_type": mime,
                "knowledge_type": cfg.name or "google_drive",
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
