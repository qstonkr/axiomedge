"""TeamsConnector — Microsoft Graph teams/channels/messages.

각 채널의 messages → 각 message + replies 결합 → 1 thread = 1 RawDocument
(Slack 패턴). HTML body 는 plain text 로 sanitize (regex 기반 — heavy parser
는 ingestion pipeline 의 document_parser 가 담당).
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from src.connectors._msgraph import MSGraphAPIError, MSGraphClient, parse_iso_date
from src.core.models import ConnectorResult, RawDocument

from .config import TeamsConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "teams:"
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


class TeamsConnector:
    """Teams channel + thread crawler — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "teams"

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
            cfg = TeamsConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        oldest_dt: datetime | None = None
        if cfg.days_back > 0:
            oldest_dt = datetime.now(UTC) - timedelta(days=cfg.days_back)

        documents: list[RawDocument] = []
        latest_dt: datetime | None = None
        skipped_channels: list[str] = []

        async with MSGraphClient(cfg.auth_token) as client:
            channel_ids = cfg.channel_ids
            if not channel_ids:
                # 모든 채널 enumerate (max 50)
                try:
                    items: list[dict[str, Any]] = []
                    async for ch in client.iterate_pages(
                        f"/teams/{cfg.team_id}/channels",
                    ):
                        items.append(ch)
                        if len(items) >= 50:
                            break
                    channel_ids = tuple(str(c["id"]) for c in items if c.get("id"))
                except MSGraphAPIError as e:
                    return ConnectorResult(
                        success=False, source_type=self.source_type,
                        error=f"teams/{cfg.team_id}/channels: {e}",
                    )

            for channel_id in channel_ids:
                try:
                    docs, ch_latest = await self._fetch_channel(
                        client, cfg, channel_id, oldest_dt,
                    )
                except MSGraphAPIError as e:
                    if e.status in (403, 404):
                        logger.warning(
                            "teams: skip channel %s (%s)", channel_id, e.code,
                        )
                        skipped_channels.append(channel_id)
                        continue
                    return ConnectorResult(
                        success=False, source_type=self.source_type,
                        error=f"channel {channel_id}: {e}", documents=documents,
                    )
                documents.extend(docs)
                if ch_latest and (latest_dt is None or ch_latest > latest_dt):
                    latest_dt = ch_latest

        ch_hash = hashlib.sha256(
            ",".join(sorted(channel_ids)).encode("utf-8"),
        ).hexdigest()[:8]
        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{cfg.team_id}:{ch_hash}:"
            f"{latest_dt.isoformat() if latest_dt else 'empty'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "team_id": cfg.team_id,
                "channels_total": len(channel_ids),
                "channels_skipped": skipped_channels,
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

    async def _fetch_channel(
        self,
        client: MSGraphClient,
        cfg: TeamsConnectorConfig,
        channel_id: str,
        oldest_dt: datetime | None,
    ) -> tuple[list[RawDocument], datetime | None]:
        documents: list[RawDocument] = []
        latest: datetime | None = None
        path = f"/teams/{cfg.team_id}/channels/{channel_id}/messages"

        count = 0
        async for msg in client.iterate_pages(path):
            count += 1
            if count > cfg.max_messages:
                logger.warning(
                    "teams channel %s: hit max_messages (%d) — truncating",
                    channel_id, cfg.max_messages,
                )
                break

            created = parse_iso_date(msg.get("createdDateTime"))
            if oldest_dt and created and created < oldest_dt:
                continue
            if created and (latest is None or created > latest):
                latest = created

            text_parts: list[str] = [_format_message(msg)]
            if cfg.include_replies and msg.get("replies@odata.count", 0):
                msg_id = msg.get("id", "")
                replies_path = f"{path}/{msg_id}/replies"
                try:
                    async for reply in client.iterate_pages(replies_path):
                        text_parts.append(_format_message(reply))
                except MSGraphAPIError as e:
                    logger.warning(
                        "teams: replies fetch failed (msg %s): %s", msg_id, e,
                    )

            full_text = "\n\n".join(p for p in text_parts if p)
            if not full_text.strip():
                continue

            msg_id = msg.get("id", "")
            web_url = msg.get("webUrl", "")
            author = ((msg.get("from") or {}).get("user") or {}).get("displayName", "")
            documents.append(RawDocument(
                doc_id=f"teams:{channel_id}:{msg_id}",
                title=f"Teams thread {msg_id}",
                content=full_text,
                source_uri=web_url,
                author=author,
                updated_at=created,
                content_hash=RawDocument.sha256(full_text),
                metadata={
                    "source_type": "teams",
                    "team_id": cfg.team_id,
                    "channel_id": channel_id,
                    "message_id": msg_id,
                    "knowledge_type": cfg.name or "teams",
                },
            ))

        return documents, latest


def _format_message(msg: dict[str, Any]) -> str:
    """Teams message → text. body.content 가 보통 HTML."""
    body = msg.get("body") or {}
    content = str(body.get("content") or "").strip()
    if not content:
        return ""
    if str(body.get("contentType") or "").lower() == "html":
        # 매우 단순한 HTML strip — 본격 파싱은 ingestion pipeline 책임.
        content = _HTML_TAG_RE.sub(" ", content)
        content = _WHITESPACE_RE.sub(" ", content).strip()
    if not content:
        return ""
    author = ((msg.get("from") or {}).get("user") or {}).get("displayName", "")
    created = msg.get("createdDateTime", "")
    if author:
        return f"**{author}** [{created[:16].replace('T', ' ')}]\n{content}"
    return content


