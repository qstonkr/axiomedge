"""GmailConnector — Gmail API v1 messages.list + messages.get.

각 message 가 1 RawDocument:
- title: subject
- content: subject + sender + body (decoded text/plain part)
- updated_at: internalDate (epoch ms → UTC datetime)

MIME multipart 처리: body parts BFS 로 ``text/plain`` 우선, 없으면 ``text/html``
strip 한 fallback. 첨부파일은 무시 (RawDocument 본문 길이 제한).
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from src.connectors._google import GoogleAPIError, GoogleClient, resolve_access_token
from src.connectors._google.auth import GoogleAuthError
from src.core.models import ConnectorResult, RawDocument

from .config import GmailConnectorConfig

logger = logging.getLogger(__name__)

_BASE_URL = "https://gmail.googleapis.com/gmail/v1"
_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_FINGERPRINT_PREFIX = "gmail:"
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class GmailConnector:
    """Gmail message crawler — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "gmail"

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
            cfg = GmailConnectorConfig.from_source(
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
        latest_dt: datetime | None = None

        async with GoogleClient(access_token, base_url=_BASE_URL) as client:
            list_path = f"/users/{cfg.user_id}/messages"
            list_params: dict[str, Any] = {"maxResults": 100}
            if cfg.query:
                list_params["q"] = cfg.query

            count = 0
            try:
                async for stub in client.iterate_pages(
                    list_path, params=list_params, items_key="messages",
                ):
                    if count >= cfg.max_messages:
                        break
                    msg_id = stub.get("id")
                    if not msg_id:
                        continue
                    try:
                        msg = await client.get(
                            f"/users/{cfg.user_id}/messages/{msg_id}",
                            params={"format": "full"},
                        )
                    except GoogleAPIError as e:
                        logger.warning("gmail: get %s failed: %s", msg_id, e)
                        continue

                    doc = _build_document(msg, cfg)
                    if doc is None:
                        continue
                    documents.append(doc)
                    count += 1
                    if doc.updated_at and (
                        latest_dt is None or doc.updated_at > latest_dt
                    ):
                        latest_dt = doc.updated_at
            except GoogleAPIError as exc:
                return ConnectorResult(
                    success=False, source_type=self.source_type,
                    error=str(exc), documents=documents,
                )

        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{cfg.user_id}:"
            f"{latest_dt.isoformat() if latest_dt else 'empty'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "user_id": cfg.user_id,
                "query": cfg.query,
                "messages_emitted": len(documents),
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
# Helpers — Gmail MIME 파싱
# ---------------------------------------------------------------------------


def _build_document(msg: dict[str, Any], cfg: GmailConnectorConfig) -> RawDocument | None:
    msg_id = str(msg.get("id") or "")
    headers = {
        h["name"].lower(): h["value"]
        for h in (msg.get("payload") or {}).get("headers", [])
        if isinstance(h, dict) and h.get("name")
    }
    subject = headers.get("subject", "(no subject)")
    sender = headers.get("from", "")

    body_text = ""
    if cfg.include_body:
        body_text = _extract_body(msg.get("payload") or {})

    snippet = str(msg.get("snippet") or "")
    pieces: list[str] = [f"Subject: {subject}"]
    if sender:
        pieces.append(f"From: {sender}")
    pieces.append("")
    pieces.append(body_text or snippet)
    full = "\n".join(p for p in pieces if p is not None).strip()
    if not full:
        return None

    internal_ms = int(msg.get("internalDate") or 0)
    received: datetime | None = None
    if internal_ms > 0:
        try:
            received = datetime.fromtimestamp(internal_ms / 1000, tz=UTC)
        except (ValueError, OSError):
            received = None

    return RawDocument(
        doc_id=f"gmail:{msg_id}",
        title=subject,
        content=full,
        source_uri=f"https://mail.google.com/mail/u/0/#inbox/{msg_id}",
        author=sender,
        updated_at=received,
        content_hash=RawDocument.sha256(full),
        metadata={
            "source_type": "gmail",
            "message_id": msg_id,
            "thread_id": msg.get("threadId", ""),
            "label_ids": msg.get("labelIds", []),
            "knowledge_type": cfg.name or "gmail",
        },
    )


def _extract_body(payload: dict[str, Any]) -> str:
    """multipart payload BFS — text/plain 우선, fallback text/html stripped."""
    plain_chunks: list[str] = []
    html_chunks: list[str] = []
    queue: list[dict[str, Any]] = [payload]
    while queue:
        part = queue.pop(0)
        mime = str(part.get("mimeType") or "").lower()
        body = part.get("body") or {}
        data = body.get("data")
        if data:
            try:
                decoded = base64.urlsafe_b64decode(data + "==").decode(
                    "utf-8", errors="replace",
                )
            except (ValueError, TypeError):
                decoded = ""
            if mime.startswith("text/plain"):
                plain_chunks.append(decoded)
            elif mime.startswith("text/html"):
                html_chunks.append(decoded)
        for sub in part.get("parts") or []:
            queue.append(sub)

    if plain_chunks:
        return "\n\n".join(c.strip() for c in plain_chunks if c.strip())
    if html_chunks:
        joined = "\n\n".join(html_chunks)
        stripped = _HTML_TAG_RE.sub(" ", joined)
        return _WS_RE.sub(" ", stripped).strip()
    return ""
