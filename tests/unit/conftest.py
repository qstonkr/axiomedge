"""Shared fixtures for unit tests.

Ensures dashboard modules are importable and streamlit is mocked
before any dashboard test imports.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Make dashboard modules importable from all test files
_DASHBOARD_DIR = str(Path(__file__).resolve().parents[2] / "src" / "apps" / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

# Ensure streamlit is mocked before any dashboard import
if "streamlit" not in sys.modules:
    _st_mock = MagicMock()
    _cache_mock = MagicMock(side_effect=lambda **kw: lambda f: f)
    _cache_mock.clear = MagicMock()
    _st_mock.cache_data = _cache_mock
    _cache_res_mock = MagicMock(side_effect=lambda **kw: lambda f: f)
    _cache_res_mock.clear = MagicMock()
    _st_mock.cache_resource = _cache_res_mock
    _st_mock.session_state = {}
    sys.modules["streamlit"] = _st_mock
