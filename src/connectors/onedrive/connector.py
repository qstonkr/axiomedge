"""OneDriveConnector — Microsoft Graph driveItem 트리 BFS.

각 file → ``/content`` endpoint 로 binary 다운로드 후 임시 파일에 저장 →
``parse_file()`` 가 PDF/DOCX/PPTX/MD 추출. text-extract 미지원 MIME 은 skip.

Version fingerprint: ``onedrive:{drive_path}:{folder}:{lastModified_max}``.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from src.connectors._msgraph import (
    MSGraphAPIError,
    MSGraphClient,
    download_drive_item,
)
from src.core.models import ConnectorResult, RawDocument

from .config import OneDriveConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "onedrive:"


class OneDriveConnector:
    """OneDrive folder-tree crawler — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "onedrive"

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
            cfg = OneDriveConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        documents: list[RawDocument] = []
        last_modified_max: datetime | None = None
        files_visited = 0

        async with MSGraphClient(cfg.auth_token) as client:
            # Root path resolution: ``{drive_path}/root:/folder/path:/children``
            # Graph 의 path-addressable child 패턴.
            queue: deque[tuple[str, int]] = deque()
            root_path = self._build_path(cfg, "")
            queue.append((root_path, 0))

            try:
                while queue and files_visited < cfg.max_files:
                    folder_path, depth = queue.popleft()
                    children = []
                    try:
                        async for item in client.iterate_pages(folder_path):
                            children.append(item)
                    except MSGraphAPIError as e:
                        if e.status == 404:
                            logger.warning(
                                "onedrive: folder not found, skipping (%s)",
                                folder_path,
                            )
                            continue
                        raise

                    for item in children:
                        if files_visited >= cfg.max_files:
                            break
                        if "folder" in item:
                            sub_path = self._child_path(item)
                            if sub_path:
                                queue.append((sub_path, depth + 1))
                            continue
                        if "file" not in item:
                            continue

                        name = str(item.get("name") or "")
                        ext = Path(name).suffix.lower()
                        if cfg.include_extensions and ext not in cfg.include_extensions:
                            continue

                        files_visited += 1
                        try:
                            doc = await download_drive_item(
                                cfg.auth_token, item,
                                source_type="onedrive",
                                knowledge_type=cfg.name,
                            )
                        except (httpx.HTTPError, OSError, RuntimeError) as e:
                            logger.warning(
                                "onedrive: failed to download %s: %s", name, e,
                            )
                            continue
                        if doc is None:
                            continue

                        documents.append(doc)
                        modified = _parse_iso_date(item.get("lastModifiedDateTime"))
                        if modified and (
                            last_modified_max is None or modified > last_modified_max
                        ):
                            last_modified_max = modified

            except MSGraphAPIError as exc:
                return ConnectorResult(
                    success=False, source_type=self.source_type,
                    error=str(exc), documents=documents,
                )

        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{cfg.drive_path}:{cfg.folder_path}:"
            f"{last_modified_max.isoformat() if last_modified_max else 'empty'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "drive_path": cfg.drive_path,
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

    def _build_path(self, cfg: OneDriveConnectorConfig, sub: str) -> str:
        """drive_path + folder_path + sub → ``{drive_path}/root:/.../children``."""
        rel = "/".join(p for p in (cfg.folder_path, sub) if p)
        if rel:
            return f"/{cfg.drive_path}/root:/{rel}:/children"
        return f"/{cfg.drive_path}/root/children"

    def _child_path(self, folder_item: dict[str, Any]) -> str | None:
        """Folder item → 그 안의 children path (item id 기반 — path 보다 robust)."""
        item_id = folder_item.get("id")
        if not item_id:
            return None
        # parentReference 의 driveId 가 있으면 그걸로 reconstruct, 아니면 fallback.
        ref = folder_item.get("parentReference") or {}
        drive_id = ref.get("driveId")
        if drive_id:
            return f"/drives/{drive_id}/items/{item_id}/children"
        return None


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
