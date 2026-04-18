"""File Upload Knowledge Connector (local-only).

- Removed S3 loading (local file loading only).
- Inline document parsing (no external attachment_parser dependency).
- Uses domain.models for RawDocument/ConnectorResult.

Usage:
    connector = FileUploadConnector()
    result = await connector.fetch(
        {"entry_point": "/path/to/file.pdf"},
        force=True,
    )
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.models import ConnectorResult, RawDocument
from src.pipelines.document_parser import parse_file


class FileUploadConnector:
    """Connector that loads local upload files and emits one RawDocument."""

    @property
    def source_type(self) -> str:
        return "file_upload"

    async def health_check(self) -> bool:
        await asyncio.sleep(0)
        return True

    async def fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,
        last_fingerprint: str | None = None,
    ) -> ConnectorResult:
        entry = str(
            config.get("entry_point")
            or config.get("file_uri")
            or config.get("file_path")
            or ""
        ).strip()
        if not entry:
            return ConnectorResult(
                success=False,
                source_type=self.source_type,
                error="file_upload connector requires entry_point, file_uri, or file_path",
            )

        try:
            file_bytes, resolved_uri, filename = await self._load_local_bytes(entry)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            return ConnectorResult(
                success=False,
                source_type=self.source_type,
                error=f"Failed to read upload file: {exc}",
                metadata={"entry_point": entry},
            )

        fingerprint = hashlib.sha256(file_bytes).hexdigest()
        if not force and last_fingerprint and last_fingerprint == fingerprint:
            return ConnectorResult(
                success=True,
                source_type=self.source_type,
                version_fingerprint=fingerprint,
                metadata={
                    "skipped": True,
                    "reason": "No changes detected",
                    "entry_point": entry,
                    "resolved_uri": resolved_uri,
                },
            )

        # Parse file content
        parsed_text = await asyncio.to_thread(parse_file, Path(entry))
        if not parsed_text.strip():
            return ConnectorResult(
                success=False,
                source_type=self.source_type,
                error="File parsing produced empty text content",
                metadata={
                    "entry_point": entry,
                    "resolved_uri": resolved_uri,
                    "filename": filename,
                },
            )

        guessed_mime, _ = mimetypes.guess_type(filename)
        metadata: dict[str, Any] = {
            "file_name": filename,
            "file_ext": Path(filename).suffix.lower(),
            "file_size_bytes": len(file_bytes),
            "file_mime_type": str(guessed_mime or "application/octet-stream"),
            "source_type": self.source_type,
            "staged_uri": resolved_uri,
        }

        # Pass through any extra config settings
        for key, value in config.items():
            if key in ("entry_point", "file_uri", "file_path"):
                continue
            if key not in metadata:
                metadata[key] = value

        document_id = str(
            config.get("document_id")
            or config.get("upload_id")
            or f"upload-{fingerprint[:16]}"
        )
        title = str(config.get("title") or filename)
        updated_at = _parse_datetime(config.get("updated_at"))

        document = RawDocument(
            doc_id=document_id,
            title=title,
            content=parsed_text.strip(),
            source_uri=resolved_uri,
            author=str(config.get("author_id") or ""),
            updated_at=updated_at,
            content_hash=RawDocument.sha256(parsed_text.strip()),
            metadata=metadata,
        )

        return ConnectorResult(
            success=True,
            source_type=self.source_type,
            documents=[document],
            version_fingerprint=fingerprint,
            metadata={
                "entry_point": entry,
                "resolved_uri": resolved_uri,
                "filename": filename,
                "file_size_bytes": len(file_bytes),
            },
        )

    async def lazy_fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,
        last_fingerprint: str | None = None,
    ) -> AsyncIterator[RawDocument]:
        result = await self.fetch(config, force=force, last_fingerprint=last_fingerprint)
        if not result.success or result.skipped:
            return
        for document in result.documents:
            yield document

    async def _load_local_bytes(self, entry_point: str) -> tuple[bytes, str, str]:
        path = Path(entry_point).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Local upload file not found: {path}")
        content = await asyncio.to_thread(path.read_bytes)
        return content, str(path), path.name


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
