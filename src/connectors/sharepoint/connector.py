"""SharePointConnector — IKnowledgeConnector impl for SharePoint sites.

각 list 의 items 를 fetch → Title + body content (textual fields) 결합
→ RawDocument. 첨부 파일은 현 시점 미지원 (추후 driveItem 연동 예정).

Version fingerprint: ``sharepoint:{site_id}:{lastModifiedDateTime_max}``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from src.connectors._msgraph import MSGraphAPIError, MSGraphClient
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

            modified = _parse_iso_date(item.get("lastModifiedDateTime"))
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
