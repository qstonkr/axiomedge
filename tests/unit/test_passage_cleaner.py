"""Unit tests for src/search/passage_cleaner.py — passage normalization and cleaning."""

from __future__ import annotations

import pytest

from src.search.passage_cleaner import (
    _trim_trailing_fragment,
    clean_chunks,
    clean_passage,
)


# ---------------------------------------------------------------------------
# clean_passage — whitespace normalization
# ---------------------------------------------------------------------------


class TestCleanPassageWhitespace:
    def test_collapses_horizontal_whitespace(self):
        text = "hello   world    test"
        result = clean_passage(text)
        assert "   " not in result
        assert "hello world test" == result

    def test_collapses_tabs_to_space(self):
        text = "hello\t\tworld"
        result = clean_passage(text)
        assert "\t" not in result
        assert "hello world" == result

    def test_limits_consecutive_newlines(self):
        text = "paragraph one\n\n\n\n\nparagraph two"
        result = clean_passage(text)
        assert "\n\n\n" not in result
        assert "paragraph one\n\nparagraph two" == result

    def test_strips_leading_trailing_whitespace(self):
        text = "  \n  content here  \n  "
        result = clean_passage(text)
        assert result == "content here"


# ---------------------------------------------------------------------------
# clean_passage — sentence deduplication
# ---------------------------------------------------------------------------


class TestCleanPassageDedup:
    def test_removes_duplicate_lines(self):
        text = "Line one.\nLine two.\nLine one.\nLine three."
        result = clean_passage(text)
        assert result.count("Line one.") == 1
        assert "Line two." in result
        assert "Line three." in result

    def test_case_insensitive_dedup(self):
        text = "Hello World.\nhello world.\nNew content."
        result = clean_passage(text)
        # Only first occurrence kept
        assert result.count("Hello World.") + result.count("hello world.") == 1
        assert "New content." in result

    def test_preserves_blank_lines(self):
        text = "Para one.\n\nPara two."
        result = clean_passage(text)
        assert "\n\n" in result


# ---------------------------------------------------------------------------
# clean_passage — edge cases
# ---------------------------------------------------------------------------


class TestCleanPassageEdgeCases:
    def test_empty_string(self):
        assert clean_passage("") == ""

    def test_none_returns_none(self):
        # function checks `not text` first
        result = clean_passage("")
        assert result == ""

    def test_short_text_below_min_length(self):
        result = clean_passage("hi", min_length=10)
        assert result == "hi"  # returned unchanged

    def test_text_exactly_at_min_length(self):
        text = "0123456789"  # 10 chars
        result = clean_passage(text, min_length=10)
        assert result == text

    def test_custom_min_length(self):
        text = "short"
        result = clean_passage(text, min_length=100)
        assert result == text  # below min_length, returned as-is


# ---------------------------------------------------------------------------
# _trim_trailing_fragment
# ---------------------------------------------------------------------------


class TestTrimTrailingFragment:
    def test_keeps_complete_sentence_ending_with_period(self):
        text = "This is a complete sentence."
        result = _trim_trailing_fragment(text)
        assert result == text

    def test_keeps_korean_sentence_ending(self):
        text = "이것은 완전한 문장입니다."
        result = _trim_trailing_fragment(text)
        assert result == text

    def test_short_text_unchanged(self):
        text = "short"
        result = _trim_trailing_fragment(text)
        assert result == text

    def test_strips_trailing_whitespace(self):
        text = "Hello world.   "
        result = _trim_trailing_fragment(text)
        assert result == "Hello world."

    def test_trims_incomplete_fragment(self):
        text = "Complete sentence. Incomplete fra"
        result = _trim_trailing_fragment(text)
        # Should trim back to the period
        assert result.endswith(".")

    def test_keeps_text_ending_with_question_mark(self):
        text = "Is this a question?"
        result = _trim_trailing_fragment(text)
        assert result == text

    def test_keeps_text_ending_with_exclamation(self):
        text = "This is important!"
        result = _trim_trailing_fragment(text)
        assert result == text


# ---------------------------------------------------------------------------
# clean_chunks
# ---------------------------------------------------------------------------


class TestCleanChunks:
    def test_cleans_content_field(self):
        chunks = [
            {"content": "Hello   world.  Good   text.", "id": "1"},
            {"content": "Another   chunk   here.", "id": "2"},
        ]
        result = clean_chunks(chunks)
        assert len(result) == 2
        # Whitespace should be normalized
        assert "   " not in result[0]["content"]

    def test_preserves_non_content_fields(self):
        chunks = [{"content": "Valid content here.", "id": "c1", "score": 0.9}]
        result = clean_chunks(chunks)
        assert result[0]["id"] == "c1"
        assert result[0]["score"] == 0.9

    def test_filters_short_content(self):
        chunks = [
            {"content": "OK content that is long enough.", "id": "1"},
            {"content": "tiny", "id": "2"},  # < 10 chars
        ]
        result = clean_chunks(chunks)
        assert len(result) == 1
        assert result[0]["id"] == "1"

    def test_filters_empty_content(self):
        chunks = [
            {"content": "", "id": "1"},
            {"content": "Valid content here.", "id": "2"},
        ]
        result = clean_chunks(chunks)
        assert len(result) == 1
        assert result[0]["id"] == "2"

    def test_does_not_mutate_original(self):
        chunks = [{"content": "Hello   world.", "id": "1"}]
        original_content = chunks[0]["content"]
        clean_chunks(chunks)
        assert chunks[0]["content"] == original_content

    def test_empty_list(self):
        assert clean_chunks([]) == []

    def test_missing_content_key(self):
        chunks = [{"id": "1", "score": 0.5}]
        result = clean_chunks(chunks)
        assert result == []  # empty content filtered out

    def test_dedup_within_chunk(self):
        chunks = [{"content": "Line A.\nLine B.\nLine A.\nLine C.", "id": "1"}]
        result = clean_chunks(chunks)
        assert result[0]["content"].count("Line A.") == 1
