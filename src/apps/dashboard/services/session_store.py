"""Chat session persistence for Knowledge Dashboard Local.

# STUB: Preserved for the upstream codebase interface compatibility. Not used in knowledge-local.
Simplified version - persistence is disabled by default for local use.
"""

from __future__ import annotations

from typing import Any


class SessionStore:
    """Chat session persistence stub - always returns empty/false."""

    def save_messages(self, _session_id: str, _user_id: str, _messages: list[dict[str, Any]]) -> bool:
        return False

    def load_messages(self, _session_id: str, _user_id: str) -> list[dict[str, Any]]:
        return []

    def list_sessions(self, _user_id: str, _page: int = 1, _page_size: int = 20) -> list[dict[str, Any]]:
        return []

    def delete_session(self, _session_id: str, _user_id: str) -> bool:
        return False


_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """Return the singleton SessionStore instance."""
    global _store  # noqa: PLW0603
    if _store is None:
        _store = SessionStore()
    return _store
