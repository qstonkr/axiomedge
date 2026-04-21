"""OutlookConnector — Microsoft Graph mail messages.

Endpoints:
- list: ``/users/{user}/mailFolders/{folder}/messages``
- get : ``/users/{user}/messages/{id}``  (list 가 이미 본문 포함이라 안 씀)

Body 는 list 응답에 포함 (``$select`` 로 명시). HTML 본문은 regex strip
(heavy parser 는 ingestion pipeline 의 document_parser 가 담당).

Version fingerprint: ``outlook:{user}:{folder}:{latest_received}``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from src.connectors._msgraph import MSGraphAPIError, MSGraphClient
from src.core.models import ConnectorResult, RawDocument

from .config import OutlookConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "outlook:"
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# 메시지에서 가져올 field — 명시해야 응답 크기 제어 (default 는 body 포함 X).
_SELECT_FIELDS = (
    "id,subject,from,sender,toRecipients,receivedDateTime,bodyPreview,"
    "body,webLink,isRead,importance"
)


class OutlookConnector:
    """Outlook mail crawler — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "outlook"

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
            cfg = OutlookConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        path = f"/users/{cfg.user_id}/mailFolders/{cfg.folder}/messages"
        params: dict[str, Any] = {
            "$select": _SELECT_FIELDS,
            "$top": 50,
            "$orderby": "receivedDateTime desc",
        }
        if cfg.days_back > 0:
            since = datetime.now(UTC) - timedelta(days=cfg.days_back)
            params["$filter"] = (
                f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            )

        documents: list[RawDocument] = []
        latest_dt: datetime | None = None

        async with MSGraphClient(cfg.auth_token) as client:
            count = 0
            try:
                async for msg in client.iterate_pages(path, params=params):
                    if count >= cfg.max_messages:
                        break
                    doc = _build_document(msg, cfg)
                    if doc is None:
                        continue
                    documents.append(doc)
                    count += 1
                    if doc.updated_at and (
                        latest_dt is None or doc.updated_at > latest_dt
                    ):
                        latest_dt = doc.updated_at
            except MSGraphAPIError as exc:
                return ConnectorResult(
                    success=False, source_type=self.source_type,
                    error=str(exc), documents=documents,
                )

        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{cfg.user_id}:{cfg.folder}:"
            f"{latest_dt.isoformat() if latest_dt else 'empty'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "user_id": cfg.user_id,
                "folder": cfg.folder,
                "messages_emitted": len(documents),
                "days_back": cfg.days_back,
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
# Helpers — Outlook MIME 파싱
# ---------------------------------------------------------------------------


def _build_document(msg: dict[str, Any], cfg: OutlookConnectorConfig) -> RawDocument | None:
    msg_id = str(msg.get("id") or "")
    subject = str(msg.get("subject") or "(no subject)")
    sender = _extract_email(msg.get("from") or msg.get("sender") or {})
    received = _parse_iso_date(msg.get("receivedDateTime"))
    web_link = str(msg.get("webLink") or "")

    body_text = ""
    if cfg.include_body:
        body_text = _extract_body(msg.get("body") or {})
    if not body_text:
        body_text = str(msg.get("bodyPreview") or "")

    pieces: list[str] = [f"Subject: {subject}"]
    if sender:
        pieces.append(f"From: {sender}")
    pieces.append("")
    pieces.append(body_text)
    full = "\n".join(p for p in pieces if p is not None).strip()
    if not full:
        return None

    return RawDocument(
        doc_id=f"outlook:{msg_id}",
        title=subject,
        content=full,
        source_uri=web_link or f"https://outlook.office.com/mail/inbox/id/{msg_id}",
        author=sender,
        updated_at=received,
        content_hash=RawDocument.sha256(full),
        metadata={
            "source_type": "outlook",
            "message_id": msg_id,
            "folder": cfg.folder,
            "user_id": cfg.user_id,
            "is_read": bool(msg.get("isRead", False)),
            "importance": str(msg.get("importance") or ""),
            "knowledge_type": cfg.name or "outlook",
        },
    )


def _extract_email(addr: dict[str, Any]) -> str:
    """``{"emailAddress": {"name": ..., "address": ...}}`` → "Name <addr>"."""
    if not isinstance(addr, dict):
        return ""
    ea = addr.get("emailAddress") or {}
    name = str(ea.get("name") or "").strip()
    address = str(ea.get("address") or "").strip()
    if name and address:
        return f"{name} <{address}>"
    return address or name


def _extract_body(body: dict[str, Any]) -> str:
    """body.content 추출 — html 이면 strip."""
    if not isinstance(body, dict):
        return ""
    content = str(body.get("content") or "").strip()
    if not content:
        return ""
    if str(body.get("contentType") or "").lower() == "html":
        content = _HTML_TAG_RE.sub(" ", content)
        content = _WS_RE.sub(" ", content).strip()
    return content


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
