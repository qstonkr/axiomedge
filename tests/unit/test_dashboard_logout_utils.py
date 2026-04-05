"""Unit tests for dashboard/components/logout_utils.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest



from components.logout_utils import render_logout_button


class TestLogoutUtils:
    def test_render_logout_button_noop(self):
        result = render_logout_button()
        assert result is None

    def test_importable(self):
        """Module can be imported without side effects."""
        import components.logout_utils as mod
        assert hasattr(mod, "render_logout_button")
