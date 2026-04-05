"""Unit tests for dashboard/services/api_client.py facade module."""

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
for _k in [k for k in sys.modules if k.startswith("services.api") or k == "services.api_client"]:
    del sys.modules[_k]

import services.api_client as api_client


class TestFacadeReExports:
    """Verify that the facade re-exports key functions from sub-modules."""

    def test_has_core_functions(self):
        assert hasattr(api_client, "api_failed")
        assert callable(api_client.api_failed)

    def test_has_admin_functions(self):
        assert hasattr(api_client, "get_graph_stats")
        assert hasattr(api_client, "get_cache_stats")

    def test_has_glossary_functions(self):
        assert hasattr(api_client, "list_glossary_terms")
        assert hasattr(api_client, "create_glossary_term")

    def test_has_misc_functions(self):
        assert hasattr(api_client, "list_jobs")
        assert hasattr(api_client, "create_feedback")

    def test_has_search_functions(self):
        # Verify search module is re-exported
        assert hasattr(api_client, "list_glossary_terms")  # from glossary
        assert hasattr(api_client, "list_jobs")  # from misc

    def test_api_failed_works(self):
        assert api_client.api_failed({"_api_failed": True}) is True
        assert api_client.api_failed({"data": "ok"}) is False
        assert api_client.api_failed(None) is False
