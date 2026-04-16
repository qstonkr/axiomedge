"""Unit tests for scripts/run_llm_enrichment.py."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from scripts.backfill.run_llm_enrichment import (
    _classify_l2_name,
    _generate_definition,
)


# ---------------------------------------------------------------------------
# _classify_l2_name
# ---------------------------------------------------------------------------


class TestClassifyL2Name:
    def _make_sm_response(self, content: str) -> MagicMock:
        body = BytesIO(json.dumps({
            "choices": [{"message": {"content": content}}],
        }).encode())
        return MagicMock(invoke_endpoint=MagicMock(return_value={"Body": body}))

    def test_valid_category(self) -> None:
        sm_client = self._make_sm_response("주간보고")
        result = _classify_l2_name(sm_client, "endpoint", "prompt")
        assert result == "주간보고"

    def test_strips_quotes(self) -> None:
        sm_client = self._make_sm_response('"주간보고"')
        result = _classify_l2_name(sm_client, "endpoint", "prompt")
        assert result == "주간보고"

    def test_strips_parentheses(self) -> None:
        sm_client = self._make_sm_response("주간보고(weekly)")
        result = _classify_l2_name(sm_client, "endpoint", "prompt")
        assert result == "주간보고"

    def test_truncates_long_name(self) -> None:
        sm_client = self._make_sm_response("이것은매우긴카테고리이름입니다추가")
        result = _classify_l2_name(sm_client, "endpoint", "prompt")
        # Should be truncated to 10 chars and still valid (len >= 2)
        assert result is not None
        assert len(result) <= 10

    def test_too_short_returns_none(self) -> None:
        sm_client = self._make_sm_response("A")
        result = _classify_l2_name(sm_client, "endpoint", "prompt")
        assert result is None

    def test_empty_returns_none(self) -> None:
        sm_client = self._make_sm_response("")
        result = _classify_l2_name(sm_client, "endpoint", "prompt")
        assert result is None

    def test_multiline_takes_first_line(self) -> None:
        sm_client = self._make_sm_response("주간보고\n이것은 설명입니다")
        result = _classify_l2_name(sm_client, "endpoint", "prompt")
        assert result == "주간보고"


# ---------------------------------------------------------------------------
# _generate_definition
# ---------------------------------------------------------------------------


class TestGenerateDefinition:
    def _make_sm_response(self, content: str) -> MagicMock:
        body = BytesIO(json.dumps({
            "choices": [{"message": {"content": content}}],
        }).encode())
        return MagicMock(invoke_endpoint=MagicMock(return_value={"Body": body}))

    def test_valid_definition(self) -> None:
        sm_client = self._make_sm_response("GS25 점포에서 사용하는 판매 시스템입니다.")
        result = _generate_definition(sm_client, "endpoint", "prompt")
        assert result is not None
        assert "GS25" in result
        assert result.endswith(".")

    def test_truncates_at_period(self) -> None:
        sm_client = self._make_sm_response("이것은 정의입니다. 추가 설명은 여기에.")
        result = _generate_definition(sm_client, "endpoint", "prompt")
        assert result == "이것은 정의입니다."

    def test_truncates_at_korean_period(self) -> None:
        sm_client = self._make_sm_response("시스템을 관리합니다. 더 많은 내용.")
        result = _generate_definition(sm_client, "endpoint", "prompt")
        assert result.endswith("다.")

    def test_too_short_returns_none(self) -> None:
        sm_client = self._make_sm_response("정의")
        result = _generate_definition(sm_client, "endpoint", "prompt")
        assert result is None

    def test_max_length_200(self) -> None:
        long_def = "가" * 300
        sm_client = self._make_sm_response(long_def)
        result = _generate_definition(sm_client, "endpoint", "prompt")
        assert result is not None
        assert len(result) <= 200

    def test_empty_returns_none(self) -> None:
        sm_client = self._make_sm_response("")
        result = _generate_definition(sm_client, "endpoint", "prompt")
        assert result is None
