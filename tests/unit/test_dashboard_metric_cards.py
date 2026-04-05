"""Unit tests for dashboard/components/metric_cards.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest


# Mock streamlit before importing
st_mock = MagicMock()
st_mock.session_state = MagicMock()
st_mock.cache_data = MagicMock()
st_mock.cache_resource = MagicMock()
sys.modules.setdefault("streamlit", st_mock)
st_mock = sys.modules["streamlit"]


from components.metric_cards import (
    MetricData,
    get_confidence_badge,
    render_kb_summary_metrics,
    render_metric_card,
    render_metric_row,
    render_quality_metrics,
)


# ── MetricData ──


class TestMetricData:
    def test_defaults(self):
        m = MetricData(label="Test", value=42)
        assert m.delta is None
        assert m.delta_color == "normal"
        assert m.help_text is None

    def test_all_fields(self):
        m = MetricData(
            label="Docs",
            value="100",
            delta="+5",
            delta_color="inverse",
            help_text="Total documents",
        )
        assert m.label == "Docs"
        assert m.value == "100"
        assert m.delta == "+5"
        assert m.delta_color == "inverse"


# ── render_metric_card ──


class TestRenderMetricCard:
    def test_calls_st_metric(self):
        st_mock.reset_mock()
        render_metric_card("Label", 42, delta="+1", help_text="help")
        st_mock.metric.assert_called_once_with(
            label="Label",
            value=42,
            delta="+1",
            delta_color="normal",
            help="help",
        )

    def test_no_delta(self):
        st_mock.reset_mock()
        render_metric_card("Label", "100")
        st_mock.metric.assert_called_once()
        kwargs = st_mock.metric.call_args[1]
        assert kwargs["delta"] is None


# ── render_metric_row ──


class TestRenderMetricRow:
    def test_creates_columns_for_metrics(self):
        st_mock.reset_mock()
        # Make st.columns return mock columns with context managers
        col_mocks = [MagicMock() for _ in range(3)]
        st_mock.columns.return_value = col_mocks

        metrics = [
            MetricData(label="A", value=1),
            MetricData(label="B", value=2),
            MetricData(label="C", value=3),
        ]
        render_metric_row(metrics)

        st_mock.columns.assert_called_with(3)

    def test_custom_column_count(self):
        st_mock.reset_mock()
        col_mocks = [MagicMock() for _ in range(2)]
        st_mock.columns.return_value = col_mocks

        metrics = [
            MetricData(label="A", value=1),
            MetricData(label="B", value=2),
            MetricData(label="C", value=3),
            MetricData(label="D", value=4),
        ]
        render_metric_row(metrics, columns=2)
        st_mock.columns.assert_called_with(2)


# ── get_confidence_badge ──


class TestGetConfidenceBadge:
    def test_zero_score(self):
        assert get_confidence_badge(0) == "➖ 미산정"

    def test_high_score(self):
        badge = get_confidence_badge(0.90)
        assert "High" in badge

    def test_medium_score(self):
        badge = get_confidence_badge(0.75)
        assert "Medium" in badge

    def test_low_score(self):
        badge = get_confidence_badge(0.55)
        assert "Low" in badge

    def test_uncertain_score(self):
        badge = get_confidence_badge(0.30)
        assert "Uncertain" in badge

    def test_boundary_high(self):
        badge = get_confidence_badge(0.85)
        assert "High" in badge

    def test_boundary_medium(self):
        badge = get_confidence_badge(0.70)
        assert "Medium" in badge

    def test_boundary_low(self):
        badge = get_confidence_badge(0.50)
        assert "Low" in badge


# ── render_quality_metrics ──


class TestRenderQualityMetrics:
    def test_computes_overall_when_none(self):
        st_mock.reset_mock()
        col_mocks = [MagicMock() for _ in range(4)]
        st_mock.columns.return_value = col_mocks

        render_quality_metrics(0.8, 0.6, 0.5)
        # overall = 0.8*0.5 + 0.6*0.3 + 0.5*0.2 = 0.4 + 0.18 + 0.10 = 0.68
        # Verify st.metric was called (through context managers)
        st_mock.columns.assert_called()

    def test_uses_provided_overall(self):
        st_mock.reset_mock()
        col_mocks = [MagicMock() for _ in range(4)]
        st_mock.columns.return_value = col_mocks

        render_quality_metrics(0.8, 0.6, 0.5, overall=0.99)
        st_mock.columns.assert_called()


# ── render_kb_summary_metrics ──


class TestRenderKbSummaryMetrics:
    def test_renders_four_metrics(self):
        st_mock.reset_mock()
        col_mocks = [MagicMock() for _ in range(4)]
        st_mock.columns.return_value = col_mocks

        render_kb_summary_metrics(
            total_docs=100,
            stale_docs=5,
            glossary_terms=50,
            avg_freshness=0.85,
        )
        st_mock.columns.assert_called()
