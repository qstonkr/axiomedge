"""SalesforceConnector — SOQL query → records → RawDocument."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from src.core.models import ConnectorResult, RawDocument

from .auth import SalesforceAuthError, refresh_access_token
from .client import SalesforceAPIError, SalesforceClient
from .config import SalesforceConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "salesforce:"


class SalesforceConnector:
    """Salesforce SOQL crawler — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "salesforce"

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
            cfg = SalesforceConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        try:
            access_token, instance_url = await refresh_access_token(cfg.auth_token)
        except SalesforceAuthError as e:
            return ConnectorResult(
                success=False, source_type=self.source_type,
                error=f"auth: {e}",
            )

        documents: list[RawDocument] = []
        latest_dt: datetime | None = None
        count = 0

        async with SalesforceClient(
            instance_url, access_token, api_version=cfg.api_version,
        ) as client:
            try:
                async for rec in client.query(cfg.soql):
                    if count >= cfg.max_records:
                        break
                    doc = _build_document(rec, cfg, instance_url)
                    if doc is None:
                        continue
                    documents.append(doc)
                    count += 1
                    if doc.updated_at and (
                        latest_dt is None or doc.updated_at > latest_dt
                    ):
                        latest_dt = doc.updated_at
            except SalesforceAPIError as exc:
                return ConnectorResult(
                    success=False, source_type=self.source_type,
                    error=str(exc), documents=documents,
                )

        soql_hash = hashlib.sha256(cfg.soql.encode("utf-8")).hexdigest()[:8]
        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{cfg.object_name}:{soql_hash}:"
            f"{latest_dt.isoformat() if latest_dt else 'empty'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "instance_url": instance_url,
                "object_name": cfg.object_name,
                "records_emitted": len(documents),
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
    rec: dict[str, Any],
    cfg: SalesforceConnectorConfig,
    instance_url: str,
) -> RawDocument | None:
    rec_id = str(rec.get("Id") or "")
    if not rec_id:
        return None

    title = str(rec.get(cfg.title_field) or f"{cfg.object_name} {rec_id}")
    body_parts: list[str] = []
    for field in cfg.body_fields:
        val = rec.get(field)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            body_parts.append(f"**{field}**: {text}")

    pieces: list[str] = [f"# {cfg.object_name}: {title}"]
    if body_parts:
        pieces.append("")
        pieces.extend(body_parts)

    full = "\n\n".join(p for p in pieces if p).strip()
    if not full:
        return None

    updated = _parse_iso_date(
        rec.get("LastModifiedDate") or rec.get("SystemModstamp"),
    )
    web_url = f"{instance_url}/{rec_id}"
    return RawDocument(
        doc_id=f"salesforce:{cfg.object_name}:{rec_id}",
        title=f"[{cfg.object_name}] {title}",
        content=full,
        source_uri=web_url,
        author="",
        updated_at=updated,
        content_hash=RawDocument.sha256(full),
        metadata={
            "source_type": "salesforce",
            "object_name": cfg.object_name,
            "record_id": rec_id,
            "knowledge_type": cfg.name or "salesforce",
        },
    )


def _parse_iso_date(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    # Salesforce ISO 형식: 2026-04-21T12:34:56.000+0000
    if text.endswith("+0000"):
        text = text[:-5] + "+00:00"
    elif text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
