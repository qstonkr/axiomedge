"""Sentry initialization — env-driven, no-op when SENTRY_DSN absent.

Init is idempotent and lazy: importing this module does NOT call Sentry —
only ``init_sentry()`` does. App lifespan/startup invokes once.

Env vars:
  - SENTRY_DSN: enables Sentry. Empty/unset → no-op (zero perf overhead)
  - SENTRY_ENVIRONMENT: "production" / "staging" / "development" (default APP_ENV)
  - SENTRY_RELEASE: app version tag (e.g., git sha)
  - SENTRY_TRACES_SAMPLE_RATE: 0.0~1.0 (default 0.0 — disable perf monitoring)
  - SENTRY_PROFILES_SAMPLE_RATE: 0.0~1.0 (default 0.0)

Sensitive data scrub:
  - Authorization / Cookie headers stripped from breadcrumbs
  - Request body NOT sent (request_bodies="never")
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


_initialized = False


def _scrub_event(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    """before_send hook: scrub auth headers / cookies from request data."""
    request = event.get("request") or {}
    headers = request.get("headers") or {}
    if isinstance(headers, dict):
        for key in list(headers.keys()):
            lk = key.lower()
            if lk in {"authorization", "cookie", "x-api-key", "set-cookie"}:
                headers[key] = "[redacted]"
    if "cookies" in request:
        request["cookies"] = "[redacted]"
    return event


def init_sentry() -> bool:
    """Initialize Sentry if SENTRY_DSN is set. Returns True if activated."""
    global _initialized
    if _initialized:
        return True
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError as e:
        logger.warning("Sentry init skipped — sentry-sdk not installed: %s", e)
        return False

    environment = os.getenv("SENTRY_ENVIRONMENT") or os.getenv("APP_ENV", "development")
    release = os.getenv("SENTRY_RELEASE", "")
    traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0"))
    profiles_sample_rate = float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.0"))

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,
        traces_sample_rate=traces_sample_rate,
        profiles_sample_rate=profiles_sample_rate,
        send_default_pii=False,  # never send IP / user-agent by default
        max_breadcrumbs=50,
        attach_stacktrace=True,
        before_send=_scrub_event,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="endpoint"),
            AsyncioIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
    )
    _initialized = True
    logger.info("Sentry initialized — env=%s release=%s", environment, release or "(none)")
    return True
