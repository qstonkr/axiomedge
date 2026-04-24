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

import httpx

logger = logging.getLogger(__name__)


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


__all__ = [
    "notify_bootstrap_failure_streak",
    "notify_pending_threshold",
    "notify_yaml_pr_stale",
    "send",
]
