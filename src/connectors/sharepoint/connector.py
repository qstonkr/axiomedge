"""SharePointConnector — IKnowledgeConnector impl for SharePoint sites.

### 커버리지

1. **Lists** (``/sites/{id}/lists/{list_id}/items``): Title + body textual field
   (Body/Description/Comments/Content) 결합 → RawDocument.
2. **Document Library** (``/sites/{id}/drives/*``): driveItem 트리 BFS, 각 파일
   다운로드 + ``parse_file()`` 로 PDF/DOCX/PPTX/MD 본문 추출. OneDrive connector
   와 공유 helper (``_msgraph.download_drive_item``) 사용.

``include_document_libraries=False`` 로 Document Library 만 off 할 수 있음.
``drive_ids`` 로 특정 drive 만 제한 가능 (빈 리스트면 site 의 모든 drive).

Version fingerprint: ``sharepoint:{site_id}:{lastModifiedDateTime_max}`` —
list items 와 drive items 중 max.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from src.connectors._msgraph import (
    MSGraphAPIError,
    MSGraphClient,
    download_drive_item,
    make_download_client,
    parse_iso_date,
)
from src.core.models import ConnectorResult, RawDocument

from .config import SharePointConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "sharepoint:"


class SharePointConnector:
    """SharePoint site/list crawler — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "sharepoint"

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
            cfg = SharePointConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        documents: list[RawDocument] = []
        last_modified_max: datetime | None = None
        skipped_lists: list[str] = []

        async with MSGraphClient(cfg.auth_token) as client:
            list_ids = cfg.list_ids
            if not list_ids:
                # 전체 list 조회 후 ID 수집
                try:
                    lists_path = f"/sites/{cfg.site_id}/lists"
                    items: list[dict[str, Any]] = []
                    async for lst in client.iterate_pages(lists_path):
                        items.append(lst)
                        if len(items) >= 100:  # 안전 cap
                            break
                    list_ids = tuple(str(lst["id"]) for lst in items if lst.get("id"))
                except MSGraphAPIError as e:
                    return ConnectorResult(
                        success=False, source_type=self.source_type,
                        error=f"sites/{cfg.site_id}/lists: {e}",
                    )

            for list_id in list_ids:
                try:
                    docs, last_dt = await self._fetch_list(
                        client, cfg, list_id,
                    )
                except MSGraphAPIError as e:
                    if e.status in (403, 404):
                        logger.warning(
                            "sharepoint: skip list %s (%s)", list_id, e.code,
                        )
                        skipped_lists.append(list_id)
                        continue
                    return ConnectorResult(
                        success=False, source_type=self.source_type,
                        error=f"list {list_id}: {e}", documents=documents,
                    )
                documents.extend(docs)
                if last_dt and (last_modified_max is None or last_dt > last_modified_max):
                    last_modified_max = last_dt

            drive_files_visited = 0
            drives_attempted: list[str] = []
            drives_ok: list[str] = []
            if cfg.include_document_libraries:
                try:
                    (
                        drive_docs, drive_last_dt,
                        drive_files_visited, drives_attempted, drives_ok,
                    ) = await self._fetch_site_drives(client, cfg)
                except MSGraphAPIError as e:
                    logger.warning(
                        "sharepoint: drives enumeration failed (%s) — returning list items only",
                        e,
                    )
                else:
                    documents.extend(drive_docs)
                    if drive_last_dt and (
                        last_modified_max is None or drive_last_dt > last_modified_max
                    ):
                        last_modified_max = drive_last_dt

        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{cfg.site_id}:"
            f"{last_modified_max.isoformat() if last_modified_max else 'empty'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "site_id": cfg.site_id,
                "lists_total": len(list_ids),
                "lists_skipped": skipped_lists,
                # drives_attempted = 열거 성공한 drive 수. drives_ok = fetch 완료
                # (partial 허용) 한 drive 수. 실패 시 attempted > ok 로 드러남.
                "drives_attempted": len(drives_attempted),
                "drives_ok": len(drives_ok),
                "drive_files_visited": drive_files_visited,
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

    async def _fetch_list(
        self,
        client: MSGraphClient,
        cfg: SharePointConnectorConfig,
        list_id: str,
    ) -> tuple[list[RawDocument], datetime | None]:
        """One list 의 items → RawDocument list. items + fields expansion."""
        documents: list[RawDocument] = []
        last_dt: datetime | None = None

        # ``$expand=fields`` 로 list field 한번에 가져옴.
        path = f"/sites/{cfg.site_id}/lists/{list_id}/items"
        params = {"$expand": "fields", "$top": 200}

        count = 0
        async for item in client.iterate_pages(path, params=params):
            count += 1
            if count > cfg.max_items:
                logger.warning(
                    "sharepoint list %s: hit max_items cap (%d) — truncating",
                    list_id, cfg.max_items,
                )
                break

            fields = item.get("fields") or {}
            title = str(fields.get("Title") or fields.get("LinkTitle") or "")
            # body 후보 — Description / Body / Comments 같은 textual field
            body_parts: list[str] = []
            for key in ("Body", "Description", "Comments", "Content"):
                val = fields.get(key)
                if isinstance(val, str) and val.strip():
                    body_parts.append(val.strip())
            body = "\n\n".join(body_parts) or title
            if not body.strip():
                continue

            modified = parse_iso_date(item.get("lastModifiedDateTime"))
            if modified and (last_dt is None or modified > last_dt):
                last_dt = modified
            created_by = (
                ((item.get("createdBy") or {}).get("user") or {}).get("displayName", "")
            )

            item_id = item.get("id", "")
            web_url = item.get("webUrl", "")
            documents.append(RawDocument(
                doc_id=f"sharepoint:{list_id}:{item_id}",
                title=title or f"Item-{item_id}",
                content=body,
                source_uri=web_url,
                author=created_by,
                updated_at=modified,
                content_hash=RawDocument.sha256(body),
                metadata={
                    "source_type": "sharepoint",
                    "site_id": cfg.site_id,
                    "list_id": list_id,
                    "item_id": item_id,
                    "knowledge_type": cfg.name or "sharepoint",
                },
            ))

        return documents, last_dt

    async def _fetch_site_drives(
        self,
        client: MSGraphClient,
        cfg: SharePointConnectorConfig,
    ) -> tuple[list[RawDocument], datetime | None, int, list[str], list[str]]:
        """Site 의 Document Library (drives) BFS → 각 file 다운로드 + parse.

        Returns:
            ``(documents, last_modified_max, files_visited,
               drives_attempted, drives_ok)`` —
            ``drives_attempted`` 는 처리 시도한 drive ID, ``drives_ok`` 는 BFS
            중단 없이 완료된 drive ID (partial 허용). 차이가 나는 drive 는
            중간에 ``MSGraphAPIError`` 로 abort 된 drive.
        """
        documents: list[RawDocument] = []
        last_dt: datetime | None = None
        files_visited = 0
        drives_attempted: list[str] = []
        drives_ok: list[str] = []

        drive_ids = cfg.drive_ids
        if not drive_ids:
            # Site 전체 drives 열거 — 실패는 상위 fetch() 에서 잡음
            drives_path = f"/sites/{cfg.site_id}/drives"
            drive_list: list[dict[str, Any]] = []
            async for drv in client.iterate_pages(drives_path):
                drive_list.append(drv)
                if len(drive_list) >= 50:  # 안전 cap
                    break
            drive_ids = tuple(
                str(d["id"]) for d in drive_list if d.get("id")
            )

        async with make_download_client(cfg.auth_token) as http:
            for drive_id in drive_ids:
                drives_attempted.append(drive_id)
                queue: deque[str] = deque()
                queue.append(f"/drives/{drive_id}/root/children")
                drive_succeeded = True
                try:
                    while queue and files_visited < cfg.max_files:
                        folder_path = queue.popleft()
                        try:
                            children = [
                                item async for item in client.iterate_pages(folder_path)
                            ]
                        except MSGraphAPIError as e:
                            if e.status in (403, 404):
                                logger.warning(
                                    "sharepoint: skip drive folder %s (%s)",
                                    folder_path, e.code,
                                )
                                continue
                            raise

                        for item in children:
                            if files_visited >= cfg.max_files:
                                break
                            if "folder" in item:
                                item_id = item.get("id")
                                if item_id:
                                    queue.append(
                                        f"/drives/{drive_id}/items/{item_id}/children",
                                    )
                                continue
                            if "file" not in item:
                                continue

                            # 확장자 필터는 helper 에서 단일 지점 처리 (OneDrive
                            # 와 일관 — counter 의미도 같음).
                            files_visited += 1
                            name = str(item.get("name") or "")
                            try:
                                doc = await download_drive_item(
                                    cfg.auth_token, item,
                                    source_type="sharepoint",
                                    knowledge_type=cfg.name,
                                    include_extensions=cfg.include_extensions,
                                    http_client=http,
                                )
                            except (httpx.HTTPError, OSError, RuntimeError) as e:
                                logger.warning(
                                    "sharepoint: failed to download %s: %s", name, e,
                                )
                                continue
                            if doc is None:
                                continue

                            documents.append(doc)
                            modified = parse_iso_date(item.get("lastModifiedDateTime"))
                            if modified and (last_dt is None or modified > last_dt):
                                last_dt = modified
                except MSGraphAPIError as exc:
                    drive_succeeded = False
                    logger.warning(
                        "sharepoint: drive %s aborted (%s) — partial result kept",
                        drive_id, exc,
                    )
                    continue
                finally:
                    if drive_succeeded:
                        drives_ok.append(drive_id)

        return documents, last_dt, files_visited, drives_attempted, drives_ok
