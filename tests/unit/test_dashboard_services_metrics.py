"""Unit tests for dashboard/services/metrics.py."""

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
sys.modules.pop("services.metrics", None)

from services.metrics import DashboardMetrics, metrics


class TestDashboardMetrics:
    def test_page_loaded_no_error(self):
        m = DashboardMetrics()
        m.page_loaded("home", 100.0)  # no-op, should not raise

    def test_search_executed_no_error(self):
        m = DashboardMetrics()
        m.search_executed("test query", 5, 250.0)

    def test_api_call_no_error(self):
        m = DashboardMetrics()
        m.api_call("GET", "/api/test", 200, 50.0)

    def test_error_no_error(self):
        m = DashboardMetrics()
        m.error("dashboard", "ValueError")

    def test_graph_query_no_error(self):
        m = DashboardMetrics()
        m.graph_query("search", 120.0)

    def test_track_search_quality_no_error(self):
        m = DashboardMetrics()
        m.track_search_quality(
            mode="hybrid",
            has_results=True,
            source_count=3,
            duration_ms=100.0,
        )

    def test_track_search_quality_all_params(self):
        m = DashboardMetrics()
        m.track_search_quality(
            mode="hybrid",
            has_results=False,
            source_count=0,
            duration_ms=50.0,
            has_stale_docs=True,
            quality_gate_passed=False,
            timed_out=True,
        )

    def test_session_active_no_error(self):
        m = DashboardMetrics()
        m.session_active(5)

    def test_timed_context_manager(self):
        m = DashboardMetrics()
        with m.timed("test_metric", tag="value"):
            pass  # should yield without error


class TestModuleLevelInstance:
    def test_metrics_is_dashboard_metrics(self):
        assert isinstance(metrics, DashboardMetrics)
