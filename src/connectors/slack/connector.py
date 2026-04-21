"""SlackConnector — IKnowledgeConnector impl for Slack channel messages.

각 channel 의 ``conversations.history`` → 메시지 + (옵션) ``conversations.replies``
로 thread reply 결합 → RawDocument (1 thread = 1 doc, 또는 stand-alone 메시지
1개 = 1 doc).

Version fingerprint: ``slack:{channel_ids_hash}:{latest_ts}`` — 가장 최근
메시지의 ts. 새 메시지 없으면 fingerprint 동일 → 호출자가 skip 결정.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.models import ConnectorResult, RawDocument

from .client import SlackAPIError, SlackClient
from .config import SlackConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "slack:"
# Slack mention regex — <@U12345> 또는 <#C12345|name> 같은 entity.
_USER_MENTION_RE = re.compile(r"<@([A-Z0-9]+)>")
_CHANNEL_MENTION_RE = re.compile(r"<#[A-Z0-9]+\|([^>]+)>")
_LINK_RE = re.compile(r"<(https?://[^|>]+)\|([^>]+)>")
_BARE_LINK_RE = re.compile(r"<(https?://[^>]+)>")


class SlackConnector:
    """Slack channel + thread crawler — ``IKnowledgeConnector`` 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "slack"

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
            cfg = SlackConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        oldest_ts: float | None = None
        if cfg.days_back > 0:
            oldest_ts = (
                datetime.now(UTC) - timedelta(days=cfg.days_back)
            ).timestamp()

        documents: list[RawDocument] = []
        latest_ts = ""
        skipped_channels: list[str] = []

        async with SlackClient(cfg.auth_token) as client:
            for channel_id in cfg.channel_ids:
                try:
                    channel_info = await client.conversations_info(channel_id)
                    channel_name = (
                        (channel_info.get("channel") or {}).get("name") or channel_id
                    )
                except SlackAPIError as e:
                    if e.code in ("not_in_channel", "channel_not_found"):
                        logger.warning(
                            "slack: skip channel %s (%s)", channel_id, e.code,
                        )
                        skipped_channels.append(channel_id)
                        continue
                    return ConnectorResult(
                        success=False, source_type=self.source_type,
                        error=str(e), documents=documents,
                    )

                try:
                    ch_docs, ch_latest = await self._fetch_channel(
                        client, cfg, channel_id, channel_name, oldest_ts,
                    )
                except SlackAPIError as e:
                    return ConnectorResult(
                        success=False, source_type=self.source_type,
                        error=f"channel {channel_id}: {e}", documents=documents,
                    )
                documents.extend(ch_docs)
                if ch_latest > latest_ts:
                    latest_ts = ch_latest

        ch_hash = hashlib.sha256(
            ",".join(sorted(cfg.channel_ids)).encode("utf-8"),
        ).hexdigest()[:8]
        fingerprint = f"{_FINGERPRINT_PREFIX}{ch_hash}:{latest_ts or 'empty'}"

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "channels_total": len(cfg.channel_ids),
                "channels_skipped": skipped_channels,
                "documents_emitted": len(documents),
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

    async def _fetch_channel(
        self,
        client: SlackClient,
        cfg: SlackConnectorConfig,
        channel_id: str,
        channel_name: str,
        oldest_ts: float | None,
    ) -> tuple[list[RawDocument], str]:
        """One channel → (docs, latest_ts). thread reply 옵션."""
        documents: list[RawDocument] = []
        latest = ""
        cursor: str | None = None
        while True:
            page = await client.conversations_history(
                channel_id, oldest=oldest_ts, cursor=cursor, limit=cfg.page_size,
            )
            for msg in page.get("messages") or []:
                if not cfg.include_bot_messages and (
                    msg.get("subtype") == "bot_message" or msg.get("bot_id")
                ):
                    continue
                # thread parent — replies 도 같이 묶음
                ts = str(msg.get("ts") or "")
                if ts > latest:
                    latest = ts
                if (
                    cfg.include_threads
                    and msg.get("thread_ts") == ts
                    and (msg.get("reply_count") or 0) > 0
                ):
                    thread = await client.conversations_replies(channel_id, ts)
                    text_parts: list[str] = []
                    for rep in thread.get("messages") or []:
                        rep_text = await _format_message(client, rep)
                        if rep_text:
                            text_parts.append(rep_text)
                    full_text = "\n\n".join(text_parts)
                else:
                    full_text = await _format_message(client, msg)

                if not full_text.strip():
                    continue

                doc_id = f"slack:{channel_id}:{ts}"
                ts_dt = _ts_to_datetime(ts)
                documents.append(RawDocument(
                    doc_id=doc_id,
                    title=f"#{channel_name} — {ts_dt.strftime('%Y-%m-%d %H:%M') if ts_dt else ts}",
                    content=full_text,
                    source_uri=f"slack://channel/{channel_id}/p{ts.replace('.', '')}",
                    author=msg.get("user", ""),
                    updated_at=ts_dt,
                    content_hash=RawDocument.sha256(full_text),
                    metadata={
                        "source_type": "slack",
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "ts": ts,
                        "thread": cfg.include_threads
                        and msg.get("thread_ts") == ts,
                        "knowledge_type": cfg.name or "slack",
                    },
                ))

            if not page.get("has_more"):
                break
            cursor = (page.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break

        return documents, latest


# ---------------------------------------------------------------------------
# Helpers — message → markdown-like text
# ---------------------------------------------------------------------------


async def _format_message(client: SlackClient, msg: dict[str, Any]) -> str:
    """Slack 메시지 dict → text. user mention/channel mention/link 정규화 + author."""
    raw = str(msg.get("text") or "")
    # <@U12345> → @username
    for match in _USER_MENTION_RE.finditer(raw):
        uid = match.group(1)
        username = await client.users_info(uid)
        raw = raw.replace(match.group(0), f"@{username}")
    # <#C12345|name> → #name
    raw = _CHANNEL_MENTION_RE.sub(r"#\1", raw)
    # <https://...|label> → [label](url)
    raw = _LINK_RE.sub(r"[\2](\1)", raw)
    # <https://...> → url
    raw = _BARE_LINK_RE.sub(r"\1", raw)

    user_id = str(msg.get("user") or "")
    if user_id:
        author = await client.users_info(user_id)
        ts_dt = _ts_to_datetime(str(msg.get("ts") or ""))
        ts_label = ts_dt.strftime("%H:%M") if ts_dt else ""
        return f"**{author}** [{ts_label}]\n{raw}"
    return raw


def _ts_to_datetime(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC)
    except (ValueError, TypeError, OSError):
        return None
