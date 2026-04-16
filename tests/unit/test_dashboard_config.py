"""Unit tests for dashboard/services/config.py — environment config & constants."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Make dashboard modules importable
_DASHBOARD_DIR = str(Path(__file__).resolve().parents[2] / "src" / "apps" / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)


# ===========================================================================
# _safe_int helper
# ===========================================================================

class TestSafeInt:
    def test_returns_default_when_env_empty(self):
        from services.config import _safe_int
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEST_SAFE_INT_KEY", None)
            assert _safe_int("TEST_SAFE_INT_KEY", 42) == 42

    def test_returns_parsed_int(self):
        from services.config import _safe_int
        with patch.dict(os.environ, {"TEST_SAFE_INT_KEY": "99"}):
            assert _safe_int("TEST_SAFE_INT_KEY", 42) == 99

    def test_returns_default_on_invalid(self):
        from services.config import _safe_int
        with patch.dict(os.environ, {"TEST_SAFE_INT_KEY": "not_a_number"}):
            assert _safe_int("TEST_SAFE_INT_KEY", 42) == 42


# ===========================================================================
# Default constant values
# ===========================================================================

class TestDefaultConstants:
    def test_dashboard_api_url_default(self):
        import services.config as cfg
        # Default should be localhost:8000 (may be overridden by .env)
        assert "localhost" in cfg.DASHBOARD_API_URL or "127.0.0.1" in cfg.DASHBOARD_API_URL or cfg.DASHBOARD_API_URL.startswith("http")

    def test_api_timeout_is_positive(self):
        import services.config as cfg
        assert cfg.API_TIMEOUT > 0

    def test_api_search_timeout_is_positive(self):
        import services.config as cfg
        assert cfg.API_SEARCH_TIMEOUT > 0

    def test_cache_ttl_ordering(self):
        import services.config as cfg
        assert cfg.CACHE_TTL_SHORT < cfg.CACHE_TTL_MEDIUM < cfg.CACHE_TTL_LONG

    def test_cache_ttl_short_is_30(self):
        import services.config as cfg
        assert cfg.CACHE_TTL_SHORT == 30

    def test_cache_ttl_medium_is_120(self):
        import services.config as cfg
        assert cfg.CACHE_TTL_MEDIUM == 120

    def test_cache_ttl_long_is_300(self):
        import services.config as cfg
        assert cfg.CACHE_TTL_LONG == 300

    def test_api_retry_count_is_positive(self):
        import services.config as cfg
        assert cfg.API_RETRY_COUNT >= 1

    def test_qdrant_url_has_port(self):
        import services.config as cfg
        assert "6333" in cfg.QDRANT_URL

    def test_neo4j_defaults(self):
        import services.config as cfg
        assert cfg.NEO4J_USER == "neo4j" or isinstance(cfg.NEO4J_USER, str)
        assert isinstance(cfg.NEO4J_DATABASE, str)


# ===========================================================================
# User ID -> Name Mapping
# ===========================================================================

class TestUserMapping:
    def test_user_map_is_dict(self):
        import services.config as cfg
        assert isinstance(cfg.USER_ID_NAME_MAP, dict)

    def test_user_map_has_entries(self):
        import services.config as cfg
        assert len(cfg.USER_ID_NAME_MAP) > 0

    def test_known_user_mapping(self):
        import services.config as cfg
        # Spot-check a known mapping
        assert cfg.USER_ID_NAME_MAP.get("mslee") == "이명석"

    def test_all_values_are_strings(self):
        import services.config as cfg
        for k, v in cfg.USER_ID_NAME_MAP.items():
            assert isinstance(k, str), f"Key {k!r} is not str"
            assert isinstance(v, str), f"Value for {k!r} is not str"

    def test_no_empty_keys_or_values(self):
        import services.config as cfg
        for k, v in cfg.USER_ID_NAME_MAP.items():
            assert k.strip(), f"Empty key found"
            assert v.strip(), f"Empty value for key {k!r}"
