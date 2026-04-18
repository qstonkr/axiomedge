"""Tests for perf baseline comparison logic."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "perf_check",
    Path(__file__).parent.parent.parent / "scripts" / "perf_check.py",
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["perf_check"] = _mod
_spec.loader.exec_module(_mod)
extract_metrics = _mod.extract_metrics
compare = _mod.compare


def test_extract_metrics_from_k6_summary() -> None:
    summary = {
        "metrics": {
            "http_req_duration": {"values": {"med": 100.0, "p(95)": 500.0, "p(99)": 1000.0}},
            "http_req_failed": {"values": {"rate": 0.01}},
            "http_reqs": {"values": {"rate": 50.0}},
        }
    }
    metrics = extract_metrics(summary)
    assert metrics["http_req_duration_p50"] == 100.0
    assert metrics["http_req_duration_p95"] == 500.0
    assert metrics["http_req_duration_p99"] == 1000.0
    assert metrics["http_req_failed_rate"] == 0.01
    assert metrics["throughput_min"] == 50.0


def test_compare_no_regression() -> None:
    actual = {"http_req_duration_p95": 800.0, "throughput_min": 10.0, "http_req_failed_rate": 0.001}
    baseline = {"http_req_duration_p95": 1000.0, "throughput_min": 8.0, "http_req_failed_rate": 0.005}
    assert compare(actual, baseline, tolerance=0.20) == []


def test_compare_latency_regression_within_tolerance() -> None:
    actual = {"http_req_duration_p95": 1100.0}  # 10% slower
    baseline = {"http_req_duration_p95": 1000.0}
    # 20% tol → 1200 cap → 1100 OK
    assert compare(actual, baseline, tolerance=0.20) == []


def test_compare_latency_regression_exceeds_tolerance() -> None:
    actual = {"http_req_duration_p95": 1300.0}
    baseline = {"http_req_duration_p95": 1000.0}
    regs = compare(actual, baseline, tolerance=0.20)
    assert len(regs) == 1
    assert "http_req_duration_p95" in regs[0]


def test_compare_throughput_regression() -> None:
    actual = {"throughput_min": 5.0}
    baseline = {"throughput_min": 8.0}
    # tol 20% → floor 6.4 — 5.0 < 6.4 → regression
    regs = compare(actual, baseline, tolerance=0.20)
    assert len(regs) == 1
    assert "throughput_min" in regs[0]


def test_compare_throughput_within_tolerance() -> None:
    actual = {"throughput_min": 7.0}
    baseline = {"throughput_min": 8.0}
    # tol 20% → floor 6.4 — 7.0 ≥ 6.4 → OK
    assert compare(actual, baseline, tolerance=0.20) == []


def test_compare_error_rate_regression() -> None:
    actual = {"http_req_failed_rate": 0.05}
    baseline = {"http_req_failed_rate": 0.01}
    # 20% tol → 0.012 cap — 0.05 way over
    regs = compare(actual, baseline, tolerance=0.20)
    assert len(regs) == 1


def test_compare_skips_underscore_keys() -> None:
    actual = {"http_req_duration_p95": 500.0}
    baseline = {"_comment": "ignore", "http_req_duration_p95": 1000.0}
    assert compare(actual, baseline, tolerance=0.20) == []
