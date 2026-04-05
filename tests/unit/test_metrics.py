"""Unit tests for metrics module."""

from __future__ import annotations

import importlib

import pytest

# Import the module so we can reset state between tests
import src.api.routes.metrics as metrics_mod
from src.api.routes.metrics import (
    inc,
    observe_request,
    observe_search_duration,
    set_info,
    inc_connections,
    dec_connections,
    _normalize_path,
    _get_snapshot,
    _render_prometheus,
)


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Reset all metrics state before each test."""
    with metrics_mod._lock:
        for key in metrics_mod._counters:
            metrics_mod._counters[key] = 0
        metrics_mod._request_counts.clear()
        metrics_mod._request_duration_buckets.clear()
        metrics_mod._request_duration_sum.clear()
        metrics_mod._request_duration_count.clear()
        metrics_mod._search_duration_buckets = {b: 0 for b in metrics_mod._DURATION_BUCKETS}
        metrics_mod._search_duration_sum = 0.0
        metrics_mod._search_duration_count = 0
        metrics_mod._active_connections = 0
        metrics_mod._info_labels.clear()
    yield


# ---------------------------------------------------------------------------
# inc()
# ---------------------------------------------------------------------------

class TestInc:
    def test_increment_existing_counter(self) -> None:
        inc("search_requests")
        inc("search_requests")
        snapshot = _get_snapshot()
        assert snapshot["search_requests"] == 2

    def test_increment_by_value(self) -> None:
        inc("ingest_chunks", 10)
        snapshot = _get_snapshot()
        assert snapshot["ingest_chunks"] == 10

    def test_increment_new_counter(self) -> None:
        inc("custom_counter")
        snapshot = _get_snapshot()
        assert snapshot["custom_counter"] == 1


# ---------------------------------------------------------------------------
# observe_request()
# ---------------------------------------------------------------------------

class TestObserveRequest:
    def test_request_counted(self) -> None:
        observe_request("GET", "/api/v1/search", 200, 0.05)
        snapshot = _get_snapshot()
        assert "GET /api/v1/search 200" in snapshot["request_counts"]
        assert snapshot["request_counts"]["GET /api/v1/search 200"] == 1

    def test_multiple_requests(self) -> None:
        observe_request("GET", "/api/v1/search", 200, 0.1)
        observe_request("GET", "/api/v1/search", 200, 0.2)
        observe_request("POST", "/api/v1/ingest", 201, 1.5)
        snapshot = _get_snapshot()
        assert snapshot["request_counts"]["GET /api/v1/search 200"] == 2
        assert snapshot["request_counts"]["POST /api/v1/ingest 201"] == 1

    def test_duration_recorded(self) -> None:
        observe_request("GET", "/api/v1/search", 200, 0.5)
        # Duration sum should be recorded
        with metrics_mod._lock:
            assert metrics_mod._request_duration_sum[("GET", "/api/v1/search")] == pytest.approx(0.5)
            assert metrics_mod._request_duration_count[("GET", "/api/v1/search")] == 1


# ---------------------------------------------------------------------------
# observe_search_duration()
# ---------------------------------------------------------------------------

class TestObserveSearchDuration:
    def test_search_duration(self) -> None:
        observe_search_duration(0.123)
        observe_search_duration(0.456)
        snapshot = _get_snapshot()
        assert snapshot["search_duration_seconds_sum"] == pytest.approx(0.579, abs=0.001)
        assert snapshot["search_duration_seconds_count"] == 2

    def test_bucket_counting(self) -> None:
        observe_search_duration(0.005)  # <= 0.01
        with metrics_mod._lock:
            assert metrics_mod._search_duration_buckets[0.01] >= 1


# ---------------------------------------------------------------------------
# _normalize_path()
# ---------------------------------------------------------------------------

class TestNormalizePath:
    def test_uuid_replaced(self) -> None:
        path = "/api/v1/kb/550e8400-e29b-41d4-a716-446655440000/search"
        assert "{id}" in _normalize_path(path)

    def test_numeric_id_replaced(self) -> None:
        path = "/api/v1/documents/12345"
        assert _normalize_path(path) == "/api/v1/documents/{id}"

    def test_no_id(self) -> None:
        path = "/api/v1/health"
        assert _normalize_path(path) == "/api/v1/health"


# ---------------------------------------------------------------------------
# Gauges and info
# ---------------------------------------------------------------------------

class TestGaugesAndInfo:
    def test_connections(self) -> None:
        inc_connections()
        inc_connections()
        dec_connections()
        snapshot = _get_snapshot()
        assert snapshot["active_connections"] == 1

    def test_set_info(self) -> None:
        set_info("embedding_provider", "tei")
        snapshot = _get_snapshot()
        assert snapshot["info"]["embedding_provider"] == "tei"


# ---------------------------------------------------------------------------
# Prometheus format
# ---------------------------------------------------------------------------

class TestPrometheusFormat:
    def test_basic_format(self) -> None:
        inc("search_requests", 5)
        output = _render_prometheus()
        assert "search_requests_total 5" in output
        assert "# TYPE search_requests_total counter" in output

    def test_histogram_format(self) -> None:
        observe_request("GET", "/search", 200, 0.05)
        output = _render_prometheus()
        assert "request_duration_seconds_bucket" in output
        assert 'le="0.1"' in output
        assert 'le="+Inf"' in output

    def test_search_histogram(self) -> None:
        observe_search_duration(1.5)
        output = _render_prometheus()
        assert "search_duration_seconds_sum" in output
        assert "search_duration_seconds_count 1" in output

    def test_info_label(self) -> None:
        set_info("version", "1.0.0")
        output = _render_prometheus()
        assert 'knowledge_api_info{version="1.0.0"} 1' in output

    def test_uptime_present(self) -> None:
        output = _render_prometheus()
        assert "uptime_seconds" in output


# ---------------------------------------------------------------------------
# JSON snapshot
# ---------------------------------------------------------------------------

class TestJsonSnapshot:
    def test_snapshot_has_uptime(self) -> None:
        snapshot = _get_snapshot()
        assert "uptime_seconds" in snapshot
        assert snapshot["uptime_seconds"] >= 0

    def test_snapshot_all_counters(self) -> None:
        snapshot = _get_snapshot()
        assert "search_requests" in snapshot
        assert "ingest_documents" in snapshot
        assert "errors" in snapshot
