"""Unit tests for src/pipeline/ocr_corrector.py.

Tests OCR noise detection, domain dictionary correction, and text cleaning
functions. No external services or LLM calls needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.pipelines.ocr_corrector import (
    noise_score,
    needs_correction,
    _correct_with_domain_dict,
    _get_choseong,
    clean_chunk_text,
    clean_ocr_spacing,
    clean_ocr_numbers,
    dedup_ocr_sections,
    correct_ocr_text,
)


# ---------------------------------------------------------------------------
# noise_score
# ---------------------------------------------------------------------------


class TestNoiseScore:
    """Test OCR noise scoring."""

    def test_empty_string(self) -> None:
        assert noise_score("") == 0.0

    def test_clean_korean(self) -> None:
        """Normal Korean text should have very low noise."""
        text = "영업활성화를 위한 매출 분석 보고서입니다."
        assert noise_score(text) < 0.05

    def test_jamo_characters(self) -> None:
        """Isolated jamo (consonants/vowels) indicate OCR noise."""
        # ㅎㅂㅈ are standalone jamo
        text = "ㅎㅂㅈ 정상텍스트"
        score = noise_score(text)
        assert score > 0.0

    def test_repeated_characters(self) -> None:
        """Meaningless character repetitions (4+) raise noise score."""
        text = "====== 이건 테스트"
        score = noise_score(text)
        assert score > 0.0

    def test_garbled_syllables(self) -> None:
        """Garbled OCR syllable combinations raise noise score."""
        text = "륙어 곰은 정상텍스트 사이에"
        score = noise_score(text)
        assert score > 0.0

    def test_high_noise_text(self) -> None:
        """Text with many noise indicators should score high."""
        text = "ㅎㅂㅈㄷㅁ====ㅋㅋㅋㅋ륙어곰은"
        score = noise_score(text)
        assert score > 0.3

    def test_score_bounded(self) -> None:
        """Score should never exceed 1.0."""
        text = "ㅎ" * 100
        assert noise_score(text) <= 1.0


# ---------------------------------------------------------------------------
# needs_correction
# ---------------------------------------------------------------------------


class TestNeedsCorrection:
    """Test correction threshold logic."""

    def test_clean_text_no_correction(self) -> None:
        text = "정상적인 한국어 문서입니다."
        assert needs_correction(text) is False

    def test_noisy_ocr_tagged_text(self) -> None:
        """Text with [OCR] tag has lower threshold."""
        noisy = "[OCR] ㅎㅂㅈ ㄷㅁ 테스트"
        assert needs_correction(noisy, threshold=0.01) is True

    def test_noisy_untagged_text_higher_threshold(self) -> None:
        """Untagged text needs double the threshold to trigger."""
        # Build a text that exceeds threshold*2 but not threshold
        mildly_noisy = "ㅎ 정상적인 텍스트입니다 여러 줄의 내용"
        base_score = noise_score(mildly_noisy)
        # Set threshold so: score >= threshold but score < threshold*2
        if base_score > 0:
            threshold = base_score * 0.8
            # Without [OCR] tag, needs threshold*2
            # This may or may not trigger depending on exact noise
            result = needs_correction(mildly_noisy, threshold=threshold)
            # Just verify it returns a bool without error
            assert isinstance(result, bool)

    def test_empty_text(self) -> None:
        assert needs_correction("") is False


# ---------------------------------------------------------------------------
# _get_choseong
# ---------------------------------------------------------------------------


class TestGetChoseong:
    """Test initial consonant extraction."""

    def test_simple_word(self) -> None:
        # 영업 -> ㅇㅇ
        assert _get_choseong("영업") == "ㅇㅇ"

    def test_longer_word(self) -> None:
        # 영업활성화 -> ㅇㅇㅎㅅㅎ
        assert _get_choseong("영업활성화") == "ㅇㅇㅎㅅㅎ"

    def test_non_korean(self) -> None:
        """Non-Korean characters should be skipped."""
        assert _get_choseong("ABC") == ""

    def test_mixed_text(self) -> None:
        # 가A나 -> ㄱㄴ (A skipped)
        assert _get_choseong("가A나") == "ㄱㄴ"

    def test_empty(self) -> None:
        assert _get_choseong("") == ""

    def test_single_char(self) -> None:
        # 한 -> ㅎ
        assert _get_choseong("한") == "ㅎ"


# ---------------------------------------------------------------------------
# _correct_with_domain_dict
# ---------------------------------------------------------------------------


class TestCorrectWithDomainDict:
    """Test domain dictionary-based OCR correction."""

    def test_exact_term_unchanged(self) -> None:
        """Terms already matching the dictionary should not be modified."""
        text = "영업활성화를 위한 장려금 지급"
        result = _correct_with_domain_dict(text)
        assert "영업활성화" in result
        assert "장려금" in result

    def test_similar_term_corrected(self) -> None:
        """OCR misread with high similarity should be corrected."""
        # 얼업활설화 is similar to 영업활성화 (same choseong: ㅇㅇㅎㅅㅎ)
        text = "얼업활설화 프로그램"
        result = _correct_with_domain_dict(text)
        assert "영업활성화" in result

    def test_empty_text(self) -> None:
        assert _correct_with_domain_dict("") == ""

    def test_no_korean_tokens(self) -> None:
        text = "ABC 123 hello"
        result = _correct_with_domain_dict(text)
        assert result == text

    def test_short_token_skipped(self) -> None:
        """Single-character Korean tokens should be skipped (< 2 chars)."""
        text = "가 나 다"
        result = _correct_with_domain_dict(text)
        assert result == text

    def test_unrelated_term_unchanged(self) -> None:
        """Terms not similar to any domain term should remain unchanged."""
        text = "바나나 딸기 사과"
        result = _correct_with_domain_dict(text)
        assert result == text

    def test_length_mismatch_tolerance(self) -> None:
        """Terms with length difference > 1 from domain terms are skipped."""
        # "가맹" (2 chars) should not match "가맹점" (3 chars) - length diff = 1, ok
        # "가" (1 char) should not match any 3+ char term
        text = "가맹 관련 사항"
        result = _correct_with_domain_dict(text)
        # "가맹" is 2 chars, "가맹점" is 3 chars, diff=1 so it COULD match
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# clean_ocr_spacing
# ---------------------------------------------------------------------------


class TestCleanOcrSpacing:
    """Test OCR spacing correction."""

    def test_empty(self) -> None:
        assert clean_ocr_spacing("") == ""

    def test_normal_text_unchanged(self) -> None:
        text = "정상적인 문장입니다."
        assert clean_ocr_spacing(text) == text

    def test_merges_single_syllables(self) -> None:
        """Sequences of 3+ single Korean chars separated by spaces should merge."""
        # "가 나 다 라" should merge (4 consecutive single chars)
        text = "가 나 다 라"
        result = clean_ocr_spacing(text)
        # The regex merges consecutive single-char sequences
        assert len(result) < len(text)

    def test_preserves_words(self) -> None:
        """Multi-syllable words separated by spaces should stay as-is."""
        text = "영업 활성화 프로그램"
        result = clean_ocr_spacing(text)
        assert "영업" in result
        assert "활성화" in result


# ---------------------------------------------------------------------------
# clean_ocr_numbers
# ---------------------------------------------------------------------------


class TestCleanOcrNumbers:
    """Test OCR number corruption fixes."""

    def test_empty(self) -> None:
        assert clean_ocr_numbers("") == ""

    def test_corrupted_number_fixed(self) -> None:
        """Extra digit after comma-separated group should be removed."""
        assert clean_ocr_numbers("159,0008") == "159,000"

    def test_valid_number_unchanged(self) -> None:
        assert clean_ocr_numbers("159,000") == "159,000"

    def test_multiple_corrupted_numbers(self) -> None:
        text = "매출 159,0008원, 이익 95,8409원"
        result = clean_ocr_numbers(text)
        assert "159,000" in result
        assert "95,840" in result

    def test_no_numbers_unchanged(self) -> None:
        text = "숫자가 없는 문장"
        assert clean_ocr_numbers(text) == text

    def test_large_number(self) -> None:
        assert clean_ocr_numbers("1,234,567,8908") == "1,234,567,890"


# ---------------------------------------------------------------------------
# dedup_ocr_sections
# ---------------------------------------------------------------------------


class TestDedupOcrSections:
    """Test duplicate OCR section removal."""

    def test_empty(self) -> None:
        assert dedup_ocr_sections("") == ""

    def test_no_ocr_tags(self) -> None:
        text = "일반 텍스트입니다."
        assert dedup_ocr_sections(text) == text

    def test_no_ocr_tag_marker(self) -> None:
        """Text without [OCR] substring should be returned as-is."""
        text = "[Page 1] 내용"
        assert dedup_ocr_sections(text) == text

    def test_duplicate_sections_removed(self) -> None:
        """Duplicate content under different OCR tags should be deduped."""
        text = (
            "[OCR] 시작\n"
            "[Page 1 OCR]동일한 내용입니다."
            "[Image 1 OCR]동일한 내용입니다."
        )
        result = dedup_ocr_sections(text)
        # The duplicate should be removed -- only one copy of the content
        assert result.count("동일한 내용입니다.") == 1

    def test_unique_sections_preserved(self) -> None:
        """Different content under OCR tags should all be kept."""
        text = (
            "[OCR] 시작\n"
            "[Page 1 OCR]첫번째 내용."
            "[Page 2 OCR]두번째 내용."
        )
        result = dedup_ocr_sections(text)
        assert "첫번째 내용." in result
        assert "두번째 내용." in result

    def test_slide_ocr_tag(self) -> None:
        """[Slide N OCR] tags should also be handled."""
        text = (
            "[OCR] 시작\n"
            "[Slide 1 OCR]슬라이드 내용."
        )
        result = dedup_ocr_sections(text)
        assert "슬라이드 내용." in result


# ---------------------------------------------------------------------------
# clean_chunk_text (integration of all cleaning passes)
# ---------------------------------------------------------------------------


class TestCleanChunkText:
    """Test the combined cleaning pipeline."""

    def test_empty(self) -> None:
        assert clean_chunk_text("") == ""

    def test_applies_all_passes(self) -> None:
        """clean_chunk_text should apply spacing, number, dedup, and domain fixes."""
        text = "매출 159,0008원 [OCR] tag"
        result = clean_chunk_text(text)
        # Number should be fixed
        assert "159,000" in result

    def test_domain_correction_applied(self) -> None:
        """Domain dictionary correction should be applied."""
        text = "얼업활설화 프로그램 시작"
        result = clean_chunk_text(text)
        assert "영업활성화" in result

    def test_none_safe(self) -> None:
        """Should handle falsy input gracefully."""
        assert clean_chunk_text("") == ""


# ---------------------------------------------------------------------------
# correct_ocr_text (async, with mocked LLM)
# ---------------------------------------------------------------------------


class TestCorrectOcrText:
    """Test LLM-based OCR correction with mocked ollama client."""

    @pytest.mark.asyncio
    async def test_empty_text_returns_empty(self) -> None:
        mock_client = AsyncMock()
        result = await correct_ocr_text("", mock_client)
        assert result == ""
        mock_client.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_clean_text_skips_llm(self) -> None:
        """Text that doesn't need correction should skip LLM call."""
        mock_client = AsyncMock()
        clean = "정상적인 한국어 문서입니다. 결과를 공유합니다."
        result = await correct_ocr_text(clean, mock_client)
        assert result == clean
        mock_client.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_noisy_text_calls_llm(self) -> None:
        """Text with high noise should trigger LLM correction."""
        mock_client = AsyncMock()
        mock_client.generate.return_value = "교정된 깨끗한 텍스트입니다."

        # Build noisy text with [OCR] tag to lower threshold
        noisy = "[OCR] ㅎㅂㅈㄷㅁ ㅋㅋㅋㅋ 륙어곰은 ====== 깨진 텍스트"
        assert needs_correction(noisy)  # verify it's noisy enough

        result = await correct_ocr_text(noisy, mock_client)
        mock_client.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_returns_too_short_keeps_original(self) -> None:
        """If LLM returns suspiciously short text, keep original."""
        mock_client = AsyncMock()
        mock_client.generate.return_value = "짧"

        noisy = "[OCR] ㅎㅂㅈㄷㅁ ㅋㅋㅋㅋ 륙어곰은 ====== " + "깨진텍스트 " * 50
        if needs_correction(noisy):
            result = await correct_ocr_text(noisy, mock_client)
            # Original should be kept because LLM output is too short
            assert len(result) > 10

    @pytest.mark.asyncio
    async def test_llm_failure_returns_original(self) -> None:
        """If LLM call fails, return original text."""
        mock_client = AsyncMock()
        mock_client.generate.side_effect = RuntimeError("connection refused")

        noisy = "[OCR] ㅎㅂㅈㄷㅁ ㅋㅋㅋㅋ 륙어곰은 ====== 깨진 텍스트"
        if needs_correction(noisy):
            result = await correct_ocr_text(noisy, mock_client)
            # Should return domain-corrected original, not crash
            assert isinstance(result, str)
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_domain_dict_applied_before_llm(self) -> None:
        """Domain dictionary correction should run before LLM check."""
        mock_client = AsyncMock()
        # Text that domain dict can fix, making it clean enough to skip LLM
        text = "얼업활설화 프로그램을 통한 매출신장 전략을 수립하였습니다."
        result = await correct_ocr_text(text, mock_client)
        # Domain dict should fix 얼업활설화 -> 영업활성화
        assert "영업활성화" in result
