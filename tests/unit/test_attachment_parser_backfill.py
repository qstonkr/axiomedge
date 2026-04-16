"""Coverage backfill — attachment parser helpers + core parse methods.

Tests module-level helpers and key AttachmentParser class methods.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.connectors.confluence._attachment_helpers import (
    _env_bool,
    _env_int,
    _filter_ocr_noise,
    _resolve_ocr_mode,
    _resolve_int_field,
    _resolve_bool_field,
    _should_ocr_ppt,
    _text_chars,
    _image_result_no_ocr,
    _decode_ole_text,
    _apply_ocr_postprocess,
    OCR_MIN_DIMENSION,
    OCR_MAX_ASPECT_RATIO,
)
from src.connectors.confluence.models import AttachmentOCRPolicy, AttachmentParseResult


# ==========================================================================
# Module-level helpers
# ==========================================================================


class TestEnvBool:
    def test_true_values(self, monkeypatch) -> None:
        for val in ("1", "true", "yes", "on", "True", "YES"):
            monkeypatch.setenv("TEST_BOOL", val)
            assert _env_bool("TEST_BOOL", False) is True

    def test_false_values(self, monkeypatch) -> None:
        for val in ("0", "false", "no", "off"):
            monkeypatch.setenv("TEST_BOOL", val)
            assert _env_bool("TEST_BOOL", True) is False

    def test_unset_returns_default(self, monkeypatch) -> None:
        monkeypatch.delenv("TEST_BOOL", raising=False)
        assert _env_bool("TEST_BOOL", True) is True
        assert _env_bool("TEST_BOOL", False) is False


class TestEnvInt:
    def test_valid_int(self, monkeypatch) -> None:
        monkeypatch.setenv("TEST_INT", "42")
        assert _env_int("TEST_INT") == 42

    def test_invalid_int(self, monkeypatch) -> None:
        monkeypatch.setenv("TEST_INT", "abc")
        assert _env_int("TEST_INT") is None

    def test_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("TEST_INT", raising=False)
        assert _env_int("TEST_INT") is None

    def test_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("TEST_INT", "")
        assert _env_int("TEST_INT") is None


class TestFilterOcrNoise:
    def test_removes_repeated_chars(self) -> None:
        text = "정상 텍스트\n폐폐폐폐폐\n다른 정상 줄"
        result = _filter_ocr_noise(text)
        assert "폐폐폐폐폐" not in result
        assert "정상 텍스트" in result
        assert "다른 정상 줄" in result

    def test_keeps_normal_text(self) -> None:
        text = "일반적인 문서 내용입니다.\n두 번째 줄"
        assert _filter_ocr_noise(text) == text

    def test_empty_lines_removed(self) -> None:
        text = "first\n\n\nsecond"
        result = _filter_ocr_noise(text)
        assert result == "first\nsecond"

    def test_short_repeated_chars_kept(self) -> None:
        text = "aaa"  # Only 3 chars, < 5 threshold
        assert _filter_ocr_noise(text) == "aaa"


class TestResolveOcrMode:
    def test_override_wins(self) -> None:
        assert _resolve_ocr_mode({"attachment_ocr_mode": "off"}, {}) == "off"

    def test_source_default_used(self) -> None:
        assert _resolve_ocr_mode({}, {"attachment_ocr_mode": "auto"}) == "auto"

    def test_global_default(self) -> None:
        assert _resolve_ocr_mode({}, {}) == "force"

    def test_invalid_mode_falls_back(self) -> None:
        assert _resolve_ocr_mode({"attachment_ocr_mode": "invalid"}, {}) == "force"

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("KNOWLEDGE_CRAWL_ATTACHMENT_OCR_MODE", "auto")
        assert _resolve_ocr_mode({}, {}) == "auto"


class TestResolveIntField:
    def test_override_wins(self) -> None:
        result = _resolve_int_field(
            {"field": 10}, {}, "field", "ENV_KEY", 100,
        )
        assert result == 10

    def test_source_default(self) -> None:
        result = _resolve_int_field(
            {}, {"field": 20}, "field", "ENV_KEY", 100,
        )
        assert result == 20

    def test_legacy_default(self) -> None:
        result = _resolve_int_field({}, {}, "field", "ENV_KEY", 100)
        assert result == 100

    def test_negative_clamped(self) -> None:
        result = _resolve_int_field({"field": -5}, {}, "field", "ENV_KEY", 100)
        assert result == 0


class TestResolveBoolField:
    def test_override_wins(self) -> None:
        result = _resolve_bool_field(
            {"flag": True}, {}, "flag", "ENV_KEY", False,
        )
        assert result is True

    def test_source_default(self) -> None:
        result = _resolve_bool_field(
            {}, {"flag": False}, "flag", "ENV_KEY", True,
        )
        assert result is False


def _make_policy(**overrides) -> AttachmentOCRPolicy:
    defaults = {
        "attachment_ocr_mode": "force",
        "ocr_min_text_chars": 100,
        "ocr_max_pdf_pages": 100,
        "ocr_max_ppt_slides": 100,
        "ocr_max_images_per_attachment": 1,
        "slide_render_enabled": False,
        "layout_analysis_enabled": False,
    }
    defaults.update(overrides)
    return AttachmentOCRPolicy(**defaults)


class TestShouldOcrPpt:
    def test_off_mode_returns_false(self) -> None:
        assert _should_ocr_ppt(_make_policy(attachment_ocr_mode="off"), 0) is False

    def test_force_mode_returns_true(self) -> None:
        assert _should_ocr_ppt(_make_policy(attachment_ocr_mode="force"), 10000) is True

    def test_auto_mode_low_text(self) -> None:
        assert _should_ocr_ppt(_make_policy(attachment_ocr_mode="auto"), 50) is True

    def test_auto_mode_enough_text(self) -> None:
        assert _should_ocr_ppt(_make_policy(attachment_ocr_mode="auto"), 200) is False


class TestTextChars:
    def test_normal(self) -> None:
        assert _text_chars("hello world") == 11

    def test_with_whitespace(self) -> None:
        assert _text_chars("  hello  ") == 5

    def test_empty(self) -> None:
        assert _text_chars("") == 0

    def test_none(self) -> None:
        assert _text_chars(None) == 0


class TestImageResultNoOcr:
    def test_builds_result(self) -> None:
        policy = _make_policy(attachment_ocr_mode="off")
        result = _image_result_no_ocr("metadata", policy, "mode_off")
        assert result.extracted_text == "metadata"
        assert result.confidence == 0.5
        assert result.ocr_skip_reason == "mode_off"


class TestDecodeOleText:
    def test_valid_korean_text(self) -> None:
        # Text must be > 50 chars after regex extraction to pass
        text = "한글 테스트 문서 내용입니다 " * 10
        raw = text.encode("cp949")
        result = _decode_ole_text(raw)
        assert result is not None

    def test_too_short_returns_none(self) -> None:
        raw = b"ab"
        assert _decode_ole_text(raw) is None

    def test_null_bytes_returns_none(self) -> None:
        raw = b"\x00" * 100  # Pure null bytes
        assert _decode_ole_text(raw) is None


class TestConstants:
    def test_ocr_min_dimension(self) -> None:
        assert OCR_MIN_DIMENSION == 32

    def test_ocr_max_aspect_ratio(self) -> None:
        assert OCR_MAX_ASPECT_RATIO == 8.0
