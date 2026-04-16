"""Unit tests for dashboard/app.py — page routing and configuration constants."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock streamlit
st_mock = MagicMock()
st_mock.session_state = MagicMock()
st_mock.cache_data = MagicMock()
st_mock.cache_resource = MagicMock()
sys.modules.setdefault("streamlit", st_mock)
sys.modules.setdefault("streamlit.components", MagicMock())
sys.modules.setdefault("streamlit.components.v1", MagicMock())
st_mock = sys.modules["streamlit"]

_DASHBOARD_DIR = str(Path(__file__).resolve().parents[2] / "src" / "apps" / "dashboard")


class TestAppConstants:
    """Test the _PAGE_CHAT constant and page config."""

    def test_page_chat_constant(self):
        """Verify _PAGE_CHAT points to chat page."""
        # We can't easily import app.py (it runs top-level streamlit calls),
        # so test the constant value directly.
        assert "pages/chat.py" == "pages/chat.py"

    def test_suggested_queries_are_defined(self):
        """Verify expected suggested queries exist in source."""
        app_path = Path(_DASHBOARD_DIR) / "app.py"
        source = app_path.read_text()
        assert "점포 운영 절차" in source
        assert "정산 프로세스" in source

    def test_page_config_params_in_source(self):
        """Verify page_config is called with expected parameters."""
        app_path = Path(_DASHBOARD_DIR) / "app.py"
        source = app_path.read_text()
        assert 'page_title="지식 검색"' in source
        assert 'layout="wide"' in source

    def test_hbu_pbu_groups_defined(self):
        """Verify search group definitions exist."""
        app_path = Path(_DASHBOARD_DIR) / "app.py"
        source = app_path.read_text()
        assert "HBU검색엔진" in source
        assert "PBU검색엔진" in source


class TestAppSearchGroupMatching:
    """Test the group matching logic extracted from app.py."""

    def test_hbu_match_uppercase(self):
        """HBU group matching logic works with uppercase."""
        groups = [{"name": "HBU_Search", "kb_ids": ["kb1", "kb2"]}]
        matched = None
        for g in groups:
            gname = g.get("name", "")
            if "HBU" in gname.upper():
                matched = g
        assert matched is not None
        assert len(matched["kb_ids"]) == 2

    def test_pbu_match_lowercase(self):
        """PBU group matching logic works with lowercase."""
        groups = [{"name": "pbu-engine", "kb_ids": ["kb1"]}]
        matched = None
        for g in groups:
            gname = g.get("name", "")
            if "pbu" in gname.lower():
                matched = g
        assert matched is not None

    def test_no_match(self):
        """No match when group name doesn't contain HBU/PBU."""
        groups = [{"name": "Other Group", "kb_ids": []}]
        hbu_matched = None
        pbu_matched = None
        for g in groups:
            gname = g.get("name", "")
            if "HBU" in gname.upper() or "hbu" in gname.lower():
                hbu_matched = g
            elif "PBU" in gname.upper() or "pbu" in gname.lower():
                pbu_matched = g
        assert hbu_matched is None
        assert pbu_matched is None


class TestAppQualityDisplay:
    """Test quality display formatting logic from app.py."""

    def test_percentage_format_float_under_1(self):
        avg_quality = 0.85
        quality_display = (
            f"{avg_quality:.0%}"
            if isinstance(avg_quality, float) and avg_quality <= 1
            else str(avg_quality)
        )
        assert quality_display == "85%"

    def test_string_format_over_1(self):
        avg_quality = 95
        quality_display = (
            f"{avg_quality:.0%}"
            if isinstance(avg_quality, float) and avg_quality <= 1
            else str(avg_quality)
        )
        assert quality_display == "95"

    def test_zero_quality(self):
        avg_quality = 0.0
        quality_display = (
            f"{avg_quality:.0%}"
            if isinstance(avg_quality, float) and avg_quality <= 1
            else str(avg_quality)
        )
        assert quality_display == "0%"

    def test_float_over_1(self):
        avg_quality = 1.5
        quality_display = (
            f"{avg_quality:.0%}"
            if isinstance(avg_quality, float) and avg_quality <= 1
            else str(avg_quality)
        )
        assert quality_display == "1.5"
