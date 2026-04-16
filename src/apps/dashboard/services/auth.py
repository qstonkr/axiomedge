"""Local authentication stub for Knowledge Dashboard.

All users are always authenticated as admin. No OAuth2 needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import streamlit as st

logger = logging.getLogger(__name__)

SESSION_TIMEOUT_MINUTES: int = 0  # Disabled for local

ROLE_HIERARCHY: dict[str, int] = {
    "viewer": 0,
    "editor": 1,
    "admin": 2,
}

ROLE_DISPLAY_KR: dict[str, str] = {
    "viewer": "뷰어",
    "editor": "편집자",
    "admin": "관리자",
}


@dataclass
class LocalUser:
    """Local user stub."""
    user_id: str = "local-user"
    email: str = "local@localhost"
    access_token: str = ""


def get_authenticated_user() -> LocalUser:
    """Return a local admin user (always authenticated)."""
    return LocalUser()


def is_session_valid(_last_activity: datetime) -> bool:
    """Always valid for local."""
    return True


def update_session_activity() -> None:
    """Record the current time as last activity."""
    st.session_state["last_activity"] = datetime.now(timezone.utc)


def check_session_timeout() -> bool:
    """Always valid for local."""
    last_activity = st.session_state.get("last_activity")
    if last_activity is None:
        update_session_activity()
    return True


def get_user_role(_user=None) -> str:
    """Always return admin for local."""
    return "admin"


def require_role(minimum_role: str) -> None:
    """No-op for local - all roles are accessible."""
    pass
