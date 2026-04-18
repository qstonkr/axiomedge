"""Tests for Sentry init guard (env-driven, no-op when DSN absent)."""

from __future__ import annotations

from unittest.mock import patch

from src.core.observability import sentry as sentry_mod


def test_no_dsn_returns_false() -> None:
    sentry_mod._initialized = False
    with patch.dict("os.environ", {"SENTRY_DSN": ""}, clear=False):
        assert sentry_mod.init_sentry() is False


def test_unset_dsn_returns_false() -> None:
    sentry_mod._initialized = False
    with patch.dict("os.environ", {}, clear=False):
        # Ensure SENTRY_DSN is removed
        import os
        os.environ.pop("SENTRY_DSN", None)
        assert sentry_mod.init_sentry() is False


def test_idempotent_when_already_initialized() -> None:
    sentry_mod._initialized = True
    # Even with empty DSN, returns True because flag is set
    assert sentry_mod.init_sentry() is True


def test_scrub_redacts_auth_headers() -> None:
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer secret-token",
                "Cookie": "session=abc",
                "X-API-Key": "key-123",
                "User-Agent": "test",
            },
        },
    }
    out = sentry_mod._scrub_event(event, {})
    assert out is not None
    headers = out["request"]["headers"]
    assert headers["Authorization"] == "[redacted]"
    assert headers["Cookie"] == "[redacted]"
    assert headers["X-API-Key"] == "[redacted]"
    assert headers["User-Agent"] == "test"  # untouched


def test_scrub_redacts_cookies_dict() -> None:
    event = {"request": {"cookies": {"session": "abc"}}}
    out = sentry_mod._scrub_event(event, {})
    assert out is not None
    assert out["request"]["cookies"] == "[redacted]"


def test_scrub_handles_missing_request() -> None:
    event = {"message": "test"}
    out = sentry_mod._scrub_event(event, {})
    assert out == {"message": "test"}
