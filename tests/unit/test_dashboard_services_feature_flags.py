"""Unit tests for dashboard/services/feature_flags.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Make dashboard modules importable

_st_mock = MagicMock()
_st_mock.cache_data = lambda **kw: lambda f: f
sys.modules.setdefault("streamlit", _st_mock)

from services.feature_flags import FeatureFlags, get_feature_flags, is_enabled


class TestFeatureFlags:
    def test_defaults(self):
        ff = FeatureFlags()
        assert ff.chat_enabled is True
        assert ff.graph_enabled is True
        assert ff.operations_enabled is True
        assert ff.admin_enabled is True
        assert ff.auth_required is False
        assert ff.metrics_enabled is False
        assert ff.session_persistence_enabled is False

    def test_repr(self):
        ff = FeatureFlags()
        r = repr(ff)
        assert r.startswith("FeatureFlags(")
        assert "chat_enabled=True" in r
        assert "auth_required=False" in r


class TestGetFeatureFlags:
    def test_returns_feature_flags(self):
        # Clear lru_cache
        get_feature_flags.cache_clear()
        ff = get_feature_flags()
        assert isinstance(ff, FeatureFlags)

    def test_cached_singleton(self):
        get_feature_flags.cache_clear()
        f1 = get_feature_flags()
        f2 = get_feature_flags()
        assert f1 is f2


class TestIsEnabled:
    def test_chat_enabled(self):
        get_feature_flags.cache_clear()
        assert is_enabled("chat") is True

    def test_graph_enabled(self):
        get_feature_flags.cache_clear()
        assert is_enabled("graph") is True

    def test_admin_enabled(self):
        get_feature_flags.cache_clear()
        assert is_enabled("admin") is True

    def test_metrics_disabled(self):
        get_feature_flags.cache_clear()
        assert is_enabled("metrics") is False

    def test_session_persistence_disabled(self):
        get_feature_flags.cache_clear()
        assert is_enabled("session_persistence") is False

    def test_auth_required_special_case(self):
        get_feature_flags.cache_clear()
        assert is_enabled("auth_required") is False

    def test_already_suffixed_with_enabled(self):
        get_feature_flags.cache_clear()
        assert is_enabled("chat_enabled") is True

    def test_operations_enabled(self):
        get_feature_flags.cache_clear()
        assert is_enabled("operations") is True
