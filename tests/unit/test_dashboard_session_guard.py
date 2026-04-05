"""Unit tests for dashboard/components/session_guard.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest



from components.session_guard import (
    is_session_expired,
    mark_session_expired,
    record_auth_success,
    render_session_expiry_warning,
)


class TestSessionGuard:
    """All functions are no-ops for local development."""

    def test_record_auth_success_noop(self):
        result = record_auth_success()
        assert result is None

    def test_mark_session_expired_noop(self):
        result = mark_session_expired()
        assert result is None

    def test_is_session_expired_always_false(self):
        assert is_session_expired() is False

    def test_render_session_expiry_warning_noop(self):
        result = render_session_expiry_warning()
        assert result is None
