"""Unit tests for dashboard/services/session_store.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Make dashboard modules importable

# Force-mock streamlit regardless of prior imports
_st_mock = MagicMock()
_st_mock.session_state = MagicMock()
_st_mock.cache_data = lambda **kw: lambda f: f
_st_mock.cache_resource = MagicMock()
sys.modules["streamlit"] = _st_mock

# Purge cached dashboard service modules so they reimport with our mock
sys.modules.pop("services.session_store", None)

from services.session_store import SessionStore, get_session_store


class TestSessionStore:
    def test_save_messages_returns_false(self):
        store = SessionStore()
        result = store.save_messages("session1", "user1", [{"role": "user", "content": "hi"}])
        assert result is False

    def test_load_messages_returns_empty(self):
        store = SessionStore()
        result = store.load_messages("session1", "user1")
        assert result == []

    def test_list_sessions_returns_empty(self):
        store = SessionStore()
        result = store.list_sessions("user1")
        assert result == []

    def test_list_sessions_with_pagination(self):
        store = SessionStore()
        result = store.list_sessions("user1", 2, 10)
        assert result == []

    def test_delete_session_returns_false(self):
        store = SessionStore()
        result = store.delete_session("session1", "user1")
        assert result is False


class TestGetSessionStore:
    def test_returns_session_store(self):
        # Reset singleton
        import services.session_store as mod
        mod._store = None
        store = get_session_store()
        assert isinstance(store, SessionStore)

    def test_singleton_pattern(self):
        import services.session_store as mod
        mod._store = None
        s1 = get_session_store()
        s2 = get_session_store()
        assert s1 is s2
