"""Unit tests for dashboard/components/sidebar.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Use the streamlit mock from conftest (or create one if running standalone)
if "streamlit" not in sys.modules:
    st_mock = MagicMock()
    sys.modules["streamlit"] = st_mock
st_mock = sys.modules["streamlit"]
# Ensure required attributes exist
if not hasattr(st_mock.session_state, 'get') or not callable(getattr(st_mock.session_state, 'get', None)):
    st_mock.session_state = MagicMock()
    st_mock.session_state.get = MagicMock(return_value=None)
if not hasattr(st_mock.cache_data, 'clear'):
    st_mock.cache_data = MagicMock()
    st_mock.cache_data.clear = MagicMock()


# Purge cached dashboard modules so they reimport with our mock
for _k in [k for k in sys.modules if k.startswith("components.")]:
    del sys.modules[_k]


# ── hide_default_nav ──


class TestHideDefaultNav:
    def test_injects_css(self):
        st_mock.reset_mock()
        from components.sidebar import hide_default_nav

        hide_default_nav()
        st_mock.markdown.assert_called()
        call_args = st_mock.markdown.call_args
        assert "stSidebarNav" in call_args[0][0]
        assert call_args[1].get("unsafe_allow_html") is True


# ── render_sidebar ──


class TestRenderSidebar:
    def test_renders_without_error(self):
        st_mock.reset_mock()
        session_mock = MagicMock()
        session_mock.get = MagicMock(return_value=None)
        st_mock.session_state = session_mock

        from components.sidebar import render_sidebar

        # Mock feature flags
        with patch(
            "components.sidebar.get_feature_flags"
        ) as mock_ff:
            ff = MagicMock()
            ff.chat_enabled = True
            ff.admin_enabled = True
            ff.operations_enabled = True
            mock_ff.return_value = ff

            render_sidebar()

        # Should have called sidebar context
        st_mock.sidebar.__enter__.assert_called()

    def test_renders_with_admin_flag(self):
        st_mock.reset_mock()
        session_mock = MagicMock()
        session_mock.get = MagicMock(return_value=None)
        st_mock.session_state = session_mock

        from components.sidebar import render_sidebar

        with patch("components.sidebar.get_feature_flags") as mock_ff:
            ff = MagicMock()
            ff.chat_enabled = True
            ff.admin_enabled = True
            ff.operations_enabled = True
            mock_ff.return_value = ff

            render_sidebar(show_admin=True)

        st_mock.sidebar.__enter__.assert_called()

    def test_chat_disabled_skips_chat_link(self):
        st_mock.reset_mock()
        session_mock = MagicMock()
        session_mock.get = MagicMock(return_value=None)
        st_mock.session_state = session_mock

        from components.sidebar import render_sidebar

        with patch("components.sidebar.get_feature_flags") as mock_ff:
            ff = MagicMock()
            ff.chat_enabled = False
            ff.admin_enabled = False
            ff.operations_enabled = False
            mock_ff.return_value = ff

            render_sidebar()

        # page_link calls should not include chat page
        # (the first page_link is find_owner when chat disabled)
        calls = st_mock.page_link.call_args_list
        chat_calls = [c for c in calls if "chat.py" in str(c)]
        assert len(chat_calls) == 0

    def test_active_group_in_label(self):
        st_mock.reset_mock()
        session_mock = MagicMock()
        session_mock.get = MagicMock(return_value="HBU")
        st_mock.session_state = session_mock

        from components.sidebar import render_sidebar

        with patch("components.sidebar.get_feature_flags") as mock_ff:
            ff = MagicMock()
            ff.chat_enabled = True
            ff.admin_enabled = False
            ff.operations_enabled = False
            mock_ff.return_value = ff

            render_sidebar()

        # Check that page_link was called with group name in label
        calls = st_mock.page_link.call_args_list
        chat_calls = [c for c in calls if "chat.py" in str(c)]
        assert len(chat_calls) == 1
        label = chat_calls[0][1].get("label", chat_calls[0][0][1] if len(chat_calls[0][0]) > 1 else "")
        assert "HBU" in str(label)
