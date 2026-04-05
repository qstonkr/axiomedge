"""Unit tests for scripts/load_test.py."""

from __future__ import annotations

from scripts.load_test import RequestResult, percentile


# ---------------------------------------------------------------------------
# RequestResult
# ---------------------------------------------------------------------------


class TestRequestResult:
    def test_creation(self) -> None:
        r = RequestResult(200, 0.5)
        assert r.status == 200
        assert r.latency == 0.5
        assert r.error is None

    def test_creation_with_error(self) -> None:
        r = RequestResult(0, 1.0, "connection refused")
        assert r.status == 0
        assert r.error == "connection refused"


# ---------------------------------------------------------------------------
# percentile
# ---------------------------------------------------------------------------


class TestPercentile:
    def test_empty_returns_zero(self) -> None:
        assert percentile([], 50) == 0.0

    def test_single_value(self) -> None:
        assert percentile([1.0], 50) == 1.0
        assert percentile([1.0], 99) == 1.0

    def test_p50(self) -> None:
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = percentile(data, 50)
        assert result == 3.0

    def test_p0(self) -> None:
        data = [1.0, 2.0, 3.0]
        assert percentile(data, 0) == 1.0

    def test_p100(self) -> None:
        data = [1.0, 2.0, 3.0]
        assert percentile(data, 100) == 3.0

    def test_unsorted_input(self) -> None:
        data = [5.0, 1.0, 3.0, 2.0, 4.0]
        result = percentile(data, 50)
        assert result == 3.0

    def test_interpolation(self) -> None:
        data = [1.0, 2.0]
        result = percentile(data, 50)
        assert result == 1.5
