"""Unit tests for scripts/enrich_metadata.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.enrich_metadata import (
    _extract_date_tokens,
    _is_valid_name,
    extract_date_from_docname,
)


# ---------------------------------------------------------------------------
# extract_date_from_docname
# ---------------------------------------------------------------------------


class TestExtractDateFromDocname:
    def test_underscore_format(self) -> None:
        assert extract_date_from_docname("report_2024_04.pdf") == "2024-04"

    def test_dash_format(self) -> None:
        assert extract_date_from_docname("report-2024-04.pdf") == "2024-04"

    def test_dot_format(self) -> None:
        assert extract_date_from_docname("report.2024.04.pdf") == "2024-04"

    def test_yyyymmdd_format(self) -> None:
        assert extract_date_from_docname("report_20240430.pdf") == "2024-04"

    def test_korean_year_month(self) -> None:
        assert extract_date_from_docname("2024년 4월 보고서") == "2024-04"

    def test_dash_single_digit_month(self) -> None:
        assert extract_date_from_docname("report-2024-4-30") == "2024-04"

    def test_no_date(self) -> None:
        assert extract_date_from_docname("report.pdf") == ""

    def test_empty_string(self) -> None:
        assert extract_date_from_docname("") == ""

    def test_none_returns_empty(self) -> None:
        assert extract_date_from_docname(None) == ""

    def test_slash_format(self) -> None:
        assert extract_date_from_docname("report_2024/04/data") == "2024-04"


# ---------------------------------------------------------------------------
# _is_valid_name
# ---------------------------------------------------------------------------


class TestIsValidName:
    def test_valid_name(self) -> None:
        assert _is_valid_name("김철수") is True
        assert _is_valid_name("이영희") is True

    def test_single_char(self) -> None:
        assert _is_valid_name("김") is False

    def test_blacklisted_name(self) -> None:
        assert _is_valid_name("담당자") is False
        assert _is_valid_name("매니저") is False
        assert _is_valid_name("팀장") is False
        assert _is_valid_name("서울") is False
        assert _is_valid_name("비고") is False
        assert _is_valid_name("시스템") is False


# ---------------------------------------------------------------------------
# _extract_date_tokens
# ---------------------------------------------------------------------------


class TestExtractDateTokens:
    def test_year_month_doc(self) -> None:
        tokens = _extract_date_tokens("보고서_2024_04.pdf")
        assert "2024" in tokens
        assert "2024년" in tokens
        assert "4월" in tokens
        assert "2024_04" in tokens

    def test_week_pattern(self) -> None:
        tokens = _extract_date_tokens("4월 3주차 보고서")
        assert "4월" in tokens
        assert "3주차" in tokens
        assert "4월 3주차" in tokens

    def test_no_date_returns_empty(self) -> None:
        tokens = _extract_date_tokens("general_report.pdf")
        assert tokens == []

    def test_combined_date_and_week(self) -> None:
        tokens = _extract_date_tokens("2024_04_4월 2주차 보고서")
        assert "2024" in tokens
        assert "2주차" in tokens
