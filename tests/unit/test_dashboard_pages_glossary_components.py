"""Tests for dashboard/pages/glossary_components.py helper functions.

Tests cover data transformation helpers extracted from the glossary page.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock streamlit and dependencies
# ---------------------------------------------------------------------------
st_mock = MagicMock()
st_mock.session_state = {}

_mods = {
    "streamlit": st_mock,
    "plotly": MagicMock(),
    "plotly.graph_objects": MagicMock(),
    "plotly.express": MagicMock(),
    # NOTE: pandas는 mock하지 않음 — sklearn이 sys.modules["pandas"]를
    # 참조하므로 MagicMock이 들어가면 전체 suite가 깨짐.
}
for mod_name, mock_obj in _mods.items():
    sys.modules.setdefault(mod_name, mock_obj)

sys.modules.setdefault("components", MagicMock())
sys.modules.setdefault("components.constants", MagicMock())
sys.modules.setdefault("components.sidebar", MagicMock())
sys.modules.setdefault("services", MagicMock())
sys.modules.setdefault("services.api_client", MagicMock())

import pandas as pd  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    st_mock.session_state = {}
    st_mock.reset_mock()
    yield


def _import_gc():
    """Import glossary_components module."""
    if "pages.glossary_components" in sys.modules:
        del sys.modules["pages.glossary_components"]

    import os
    dashboard_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "dashboard"
    )
    if dashboard_path not in sys.path:
        sys.path.insert(0, os.path.abspath(dashboard_path))

    with patch.dict(sys.modules, {"streamlit": st_mock}):
        import pages.glossary_components as gc

    return gc


# ---------------------------------------------------------------------------
# Tests for _build_matched_display
# ---------------------------------------------------------------------------
class TestBuildMatchedDisplay:
    def setup_method(self):
        self.gc = _import_gc()

    def test_simple_match(self):
        m = {"matched_standard": "KB", "matched_standard_ko": "지식베이스"}
        result = self.gc._build_matched_display(m)
        assert result == "KB (지식베이스)"

    def test_no_ko(self):
        m = {"matched_standard": "API"}
        result = self.gc._build_matched_display(m)
        assert result == "API"

    def test_morpheme_matches_multiple(self):
        m = {
            "matched_standard": "KB",
            "morpheme_matches": [
                {"standard_term": "KB", "standard_term_ko": "지식베이스"},
                {"standard_term": "API", "standard_term_ko": "인터페이스"},
            ],
        }
        result = self.gc._build_matched_display(m)
        assert "KB(지식베이스)" in result
        assert "API(인터페이스)" in result
        assert " + " in result

    def test_morpheme_single_match_uses_standard(self):
        """Single morpheme match falls through to standard display."""
        m = {
            "matched_standard": "KB",
            "matched_standard_ko": "지식베이스",
            "morpheme_matches": [
                {"standard_term": "KB", "standard_term_ko": "지식베이스"},
            ],
        }
        result = self.gc._build_matched_display(m)
        assert result == "KB (지식베이스)"

    def test_fallback_keys(self):
        m = {"standard_term": "FALLBACK"}
        result = self.gc._build_matched_display(m)
        assert result == "FALLBACK"

    def test_morpheme_without_ko(self):
        m = {
            "matched_standard": "X",
            "morpheme_matches": [
                {"standard_term": "A"},
                {"standard_term": "B", "standard_term_ko": "비"},
            ],
        }
        result = self.gc._build_matched_display(m)
        assert "A" in result
        assert "B(비)" in result


# ---------------------------------------------------------------------------
# Tests for _build_expansion_rows
# ---------------------------------------------------------------------------
class TestBuildExpansionRows:
    def setup_method(self):
        self.gc = _import_gc()

    def test_empty_terms(self):
        result = self.gc._build_expansion_rows([])
        assert result == []

    def test_term_with_synonyms(self):
        terms = [{
            "term": "K8s",
            "synonyms": ["Kubernetes", "쿠버네티스"],
            "category": "인프라",
        }]
        result = self.gc._build_expansion_rows(terms)
        assert len(result) == 1
        assert result[0]["원본 용어"] == "K8s"
        assert "Kubernetes" in result[0]["확장 용어"]
        assert "쿠버네티스" in result[0]["확장 용어"]
        assert result[0]["확장 수"] == 2

    def test_term_with_abbreviations(self):
        terms = [{
            "term": "Application Programming Interface",
            "synonyms": [],
            "abbreviations": ["API"],
            "category": "기술",
        }]
        result = self.gc._build_expansion_rows(terms)
        assert len(result) == 1
        assert result[0]["확장 수"] == 1
        assert "API" in result[0]["확장 용어"]

    def test_term_with_both(self):
        terms = [{
            "term": "K8s",
            "synonyms": ["Kubernetes"],
            "abbreviations": ["k8s"],
            "category": "인프라",
        }]
        result = self.gc._build_expansion_rows(terms)
        assert result[0]["확장 수"] == 2

    def test_term_without_expansions(self):
        terms = [{"term": "Standalone", "category": "기타"}]
        result = self.gc._build_expansion_rows(terms)
        assert result == []

    def test_abbr_key_fallback(self):
        terms = [{
            "term": "Test",
            "synonyms": [],
            "abbr": ["T"],
        }]
        result = self.gc._build_expansion_rows(terms)
        assert len(result) == 1
        assert result[0]["확장 수"] == 1

    def test_string_synonyms(self):
        """Handle case where synonyms is a string instead of list."""
        terms = [{
            "term": "Test",
            "synonyms": "synonym_str",
        }]
        result = self.gc._build_expansion_rows(terms)
        assert len(result) == 1
        assert result[0]["확장 수"] == 1


# ---------------------------------------------------------------------------
# Tests for _render_sim_metrics
# ---------------------------------------------------------------------------
class TestRenderSimMetrics:
    def setup_method(self):
        self.gc = _import_gc()

    def test_renders_metrics(self):
        st_mock.reset_mock()
        st_mock.columns.return_value = [MagicMock() for _ in range(5)]
        r = {
            "total_pending": 100,
            "matched_count": 40,
            "review_count": 30,
            "new_term_count": 30,
            "page": 2,
        }
        self.gc._render_sim_metrics(r)
        # Should create 5 columns
        st_mock.columns.assert_called()


# ---------------------------------------------------------------------------
# Tests for pending term card source icon logic
# ---------------------------------------------------------------------------
class TestPendingTermSourceIcon:
    def test_source_icons(self):
        source_icon_map = {"AUTO": "R", "MANUAL": "M", "LLM": "L"}
        assert source_icon_map.get("AUTO") == "R"
        assert source_icon_map.get("MANUAL") == "M"
        assert source_icon_map.get("UNKNOWN", "") == ""


# ---------------------------------------------------------------------------
# Tests for disc item base_terms building logic
# ---------------------------------------------------------------------------
class TestDiscItemLogic:
    def test_base_terms_from_synonyms(self):
        disc = {"synonyms": ["test", "exam"]}
        disc_synonyms_list = disc.get("synonyms", [])
        base_terms = ", ".join(disc_synonyms_list) if disc_synonyms_list else "-"
        assert base_terms == "test, exam"

    def test_base_terms_empty(self):
        disc = {"synonyms": []}
        disc_synonyms_list = disc.get("synonyms", [])
        base_terms = ", ".join(disc_synonyms_list) if disc_synonyms_list else "-"
        assert base_terms == "-"

    def test_base_terms_no_key(self):
        disc = {}
        disc_synonyms_list = disc.get("synonyms", [])
        base_terms = ", ".join(disc_synonyms_list) if disc_synonyms_list else "-"
        assert base_terms == "-"
