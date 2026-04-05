"""Unit tests for dashboard/services/auth.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Make dashboard modules importable

# Force-mock streamlit regardless of prior imports
_st_mock = MagicMock()
_st_mock.session_state = {}
_st_mock.cache_data = lambda **kw: lambda f: f
_st_mock.cache_resource = MagicMock()
sys.modules["streamlit"] = _st_mock

# Purge cached dashboard service modules so they reimport with our mock
sys.modules.pop("services.auth", None)

from services.auth import (
    LocalUser,
    ROLE_DISPLAY_KR,
    ROLE_HIERARCHY,
    SESSION_TIMEOUT_MINUTES,
    check_session_timeout,
    get_authenticated_user,
    get_user_role,
    is_session_valid,
    require_role,
    update_session_activity,
)


class TestLocalUser:
    def test_defaults(self):
        user = LocalUser()
        assert user.user_id == "local-user"
        assert user.email == "local@localhost"
        assert user.access_token == ""

    def test_custom_values(self):
        user = LocalUser(user_id="u1", email="a@b.com", access_token="tok")
        assert user.user_id == "u1"


class TestConstants:
    def test_session_timeout_disabled(self):
        assert SESSION_TIMEOUT_MINUTES == 0

    def test_role_hierarchy(self):
        assert ROLE_HIERARCHY["viewer"] < ROLE_HIERARCHY["editor"] < ROLE_HIERARCHY["admin"]

    def test_role_display_kr(self):
        assert ROLE_DISPLAY_KR["admin"] == "관리자"
        assert "viewer" in ROLE_DISPLAY_KR
        assert "editor" in ROLE_DISPLAY_KR


class TestGetAuthenticatedUser:
    def test_returns_local_user(self):
        user = get_authenticated_user()
        assert isinstance(user, LocalUser)
        assert user.user_id == "local-user"


class TestIsSessionValid:
    def test_always_true(self):
        from datetime import datetime, timezone
        assert is_session_valid(datetime.now(timezone.utc)) is True


class TestUpdateSessionActivity:
    def test_sets_session_state(self):
        import services.auth as auth_mod
        # Use the module's own st reference (bound at import time)
        auth_mod.st.session_state = {}
        auth_mod.update_session_activity()
        assert "last_activity" in auth_mod.st.session_state


class TestCheckSessionTimeout:
    def test_returns_true_when_no_activity(self):
        import services.auth as auth_mod
        auth_mod.st.session_state = {}
        result = check_session_timeout()
        assert result is True
        # update_session_activity is called internally
        assert "last_activity" in auth_mod.st.session_state

    def test_returns_true_when_activity_exists(self):
        from datetime import datetime, timezone
        import services.auth as auth_mod
        auth_mod.st.session_state = {"last_activity": datetime.now(timezone.utc)}
        result = check_session_timeout()
        assert result is True


class TestGetUserRole:
    def test_always_admin(self):
        assert get_user_role() == "admin"
        assert get_user_role("some_user") == "admin"


class TestRequireRole:
    def test_noop(self):
        # Should not raise for any role
        require_role("viewer")
        require_role("editor")
        require_role("admin")
