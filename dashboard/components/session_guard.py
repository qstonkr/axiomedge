"""Session expiry guard stub for local development.

No-op: no OAuth2 session to expire.
"""

from __future__ import annotations

import streamlit as st


def record_auth_success() -> None:
    """No-op for local."""
    pass


def mark_session_expired() -> None:
    """No-op for local."""
    pass


def is_session_expired() -> bool:
    """Never expired for local."""
    return False


def render_session_expiry_warning() -> None:
    """No-op for local."""
    pass
