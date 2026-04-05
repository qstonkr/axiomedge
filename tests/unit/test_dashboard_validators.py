"""Unit tests for dashboard/services/validators.py — input validation & sanitization."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make dashboard modules importable (dashboard uses `from services.xxx` imports)
_DASHBOARD_DIR = str(Path(__file__).resolve().parents[2] / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

from services.validators import (
    sanitize_html,
    sanitize_input,
    validate_kb_id,
    validate_page_params,
    validate_query,
)


# ===========================================================================
# sanitize_input
# ===========================================================================

class TestSanitizeInput:
    def test_strips_whitespace(self):
        assert sanitize_input("  hello  ") == "hello"

    def test_empty_string_returns_empty(self):
        assert sanitize_input("") == ""

    def test_none_like_empty(self):
        # Empty string is falsy
        assert sanitize_input("") == ""

    def test_truncates_to_max_length(self):
        long_text = "a" * 2000
        result = sanitize_input(long_text, max_length=100)
        assert len(result) == 100

    def test_removes_control_characters(self):
        # \x00 is a control char, should be removed
        result = sanitize_input("hello\x00world")
        assert "\x00" not in result
        assert "helloworld" == result

    def test_preserves_newlines_and_tabs(self):
        result = sanitize_input("hello\nworld\there")
        assert "\n" in result
        assert "\t" not in result  # tabs collapsed to single space

    def test_collapses_multiple_spaces(self):
        result = sanitize_input("hello    world")
        assert result == "hello world"

    def test_collapses_tabs_to_single_space(self):
        result = sanitize_input("hello\t\tworld")
        assert result == "hello world"

    def test_korean_text_preserved(self):
        result = sanitize_input("  한국어 테스트  ")
        assert result == "한국어 테스트"

    def test_default_max_length_is_1000(self):
        text = "x" * 1500
        result = sanitize_input(text)
        assert len(result) == 1000


# ===========================================================================
# validate_query
# ===========================================================================

class TestValidateQuery:
    def test_valid_query(self):
        assert validate_query("서버 장애 대응") == "서버 장애 대응"

    def test_empty_query_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            validate_query("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            validate_query("   ")

    def test_truncates_long_query(self):
        long_q = "a" * 600
        result = validate_query(long_q, max_length=500)
        assert len(result) == 500


# ===========================================================================
# validate_page_params
# ===========================================================================

class TestValidatePageParams:
    def test_valid_params_pass_through(self):
        assert validate_page_params(2, 20) == (2, 20)

    def test_page_zero_becomes_one(self):
        page, _ = validate_page_params(0, 10)
        assert page == 1

    def test_negative_page_becomes_one(self):
        page, _ = validate_page_params(-5, 10)
        assert page == 1

    def test_page_size_capped_at_max(self):
        _, ps = validate_page_params(1, 200, max_page_size=100)
        assert ps == 100

    def test_page_size_zero_becomes_one(self):
        _, ps = validate_page_params(1, 0)
        assert ps == 1

    def test_negative_page_size_becomes_one(self):
        _, ps = validate_page_params(1, -10)
        assert ps == 1


# ===========================================================================
# validate_kb_id
# ===========================================================================

class TestValidateKbId:
    def test_valid_id(self):
        assert validate_kb_id("my-kb_01") == "my-kb_01"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            validate_kb_id("")

    def test_too_long_raises(self):
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validate_kb_id("a" * 51)

    def test_special_chars_raise(self):
        with pytest.raises(ValueError, match="invalid characters"):
            validate_kb_id("my kb!")

    def test_strips_whitespace(self):
        assert validate_kb_id("  my-kb  ") == "my-kb"

    def test_dots_not_allowed(self):
        with pytest.raises(ValueError, match="invalid characters"):
            validate_kb_id("my.kb")


# ===========================================================================
# sanitize_html
# ===========================================================================

class TestSanitizeHtml:
    def test_escapes_angle_brackets(self):
        assert "&lt;" in sanitize_html("<script>")
        assert "&gt;" in sanitize_html("<script>")

    def test_escapes_quotes(self):
        result = sanitize_html('hello "world"')
        assert "&quot;" in result

    def test_empty_returns_empty(self):
        assert sanitize_html("") == ""

    def test_none_like_falsy(self):
        assert sanitize_html("") == ""

    def test_ampersand_escaped(self):
        assert "&amp;" in sanitize_html("a & b")
