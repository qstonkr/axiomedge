"""Slack webhook notification layer for graph-schema alerts (Phase 5b).

Spec §5.6 — three alert events:
- bootstrap 연속 3회 실패 → on-call
- pending candidates > 50 → admin
- YAML PR 48h 미머지 → admin 리마인더

All sends are best-effort: a notification failure must never break the
business flow that emitted it. The module is async throughout so it
composes with async jobs / FastAPI handlers without a thread hop.
"""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)


# P1-8 — PII / sensitive-path patterns scrubbed from Slack reason text.
# Slack workspace 가 외부 사용자에게 공유될 가능성 → file path, email,
# token-like hex, IPv4 등을 mask 처리.
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/Users/[^\s/]+(/[^\s]*)?"), "/Users/<USER>"),
    (re.compile(r"/home/[^\s/]+(/[^\s]*)?"), "/home/<USER>"),
    (re.compile(r"/var/secrets/[^\s]*"), "/var/secrets/<MASKED>"),
    (re.compile(r"/etc/[^\s]*"), "/etc/<MASKED>"),
    (re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    ), "<EMAIL>"),
    (re.compile(r"\b(\d{1,3}\.){3}\d{1,3}\b"), "<IP>"),
    # 32~64자 hex (token-like): UUID 36자(- 포함) 는 제외 패턴
    (re.compile(r"\b[a-f0-9]{32,64}\b"), "<HEX>"),
]


def mask_pii(text: str) -> str:
    """Replace common PII / sensitive paths with placeholders."""
    if not text:
        return text
    out = text
    for pat, repl in _PII_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _get_webhook_url() -> str | None:
    """Read webhook URL at call time (respects env reload + patch)."""
    from src.config import get_settings

    return get_settings().notifications.slack_webhook_url


async def send(text: str) -> bool:
    """Post ``text`` to the configured Slack webhook.

    Returns True if the webhook returned 2xx. Silent no-op when no
    webhook URL is configured. Network / Slack failures are logged
    and swallowed.
    """
    url = _get_webhook_url()
    if not url:
        return False

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json={"text": text})
            if resp.status_code >= 300:
                logger.warning(
                    "Slack webhook returned %s: %s",
                    resp.status_code,
                    getattr(resp, "text", "")[:200],
                )
                return False
    except (httpx.HTTPError, RuntimeError, OSError) as exc:
        logger.warning("Slack send failed: %s", exc)
        return False
    return True


async def notify_bootstrap_failure_streak(*, kb_id: str, count: int) -> bool:
    return await send(
        f":warning: GraphRAG schema bootstrap failed {count} times in a row "
        f"for `{kb_id}`. On-call please investigate.",
    )


async def notify_pending_threshold(*, kb_id: str, pending: int) -> bool:
    return await send(
        f":inbox_tray: GraphRAG schema — `{kb_id}` has {pending} pending "
        f"candidates awaiting admin review.",
    )


async def notify_yaml_pr_stale(*, branch: str, hours: int) -> bool:
    return await send(
        f":clock3: YAML PR branch `{branch}` unmerged for {hours}h. "
        f"Please review or close.",
    )


async def notify_ingestion_failure_streak(
    *,
    kb_id: str,
    count: int,
    sample_failures: list[dict] | None = None,
) -> bool:
    """Ingest run 이 N회 연속 실패했을 때 운영팀에 통지 (PR-6 E)."""
    samples = ""
    if sample_failures:
        lines = []
        for f in sample_failures[:3]:
            doc_id = (f.get("doc_id") or "?")[:12]
            stage = f.get("stage", "?")
            # P1-8 — reason 에서 path/email/IP/token 등 PII mask 후 60자로 cap
            raw_reason = (f.get("reason") or "").replace("\n", " ")
            reason = mask_pii(raw_reason)[:60]
            lines.append(f"  • `{doc_id}…` stage=`{stage}` reason={reason}")
        if lines:
            samples = "\n" + "\n".join(lines)
    return await send(
        f":rotating_light: Ingestion failed {count} runs in a row "
        f"for `{kb_id}` (last 24h).{samples}"
    )


__all__ = [
    "mask_pii",
    "notify_bootstrap_failure_streak",
    "notify_ingestion_failure_streak",
    "notify_pending_threshold",
    "notify_yaml_pr_stale",
    "send",
]
