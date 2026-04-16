"""Unit tests for shared LLM utilities."""

from src.llm.utils import sanitize_text, estimate_token_count


class TestSanitizeText:
    def test_empty_string(self) -> None:
        assert sanitize_text("") == ""

    def test_none_like(self) -> None:
        assert sanitize_text("") == ""

    def test_strips_whitespace(self) -> None:
        assert sanitize_text("  hello  ") == "hello"

    def test_truncates_to_max_length(self) -> None:
        result = sanitize_text("hello world", max_length=5)
        assert result == "hello"

    def test_no_truncation_when_within_limit(self) -> None:
        result = sanitize_text("short", max_length=100)
        assert result == "short"

    def test_default_max_length_from_config(self) -> None:
        from src.config.weights import weights
        long_text = "x" * (weights.llm.max_query_length + 100)
        result = sanitize_text(long_text)
        assert len(result) == weights.llm.max_query_length


class TestEstimateTokenCount:
    def test_empty_string(self) -> None:
        assert estimate_token_count("") == 0

    def test_whitespace_only(self) -> None:
        assert estimate_token_count("   ") == 0

    def test_english_words(self) -> None:
        count = estimate_token_count("hello world")
        assert count == 2

    def test_korean_characters(self) -> None:
        count = estimate_token_count("안녕하세요")
        assert count == 5  # Each Korean char = 1 token

    def test_mixed_korean_english(self) -> None:
        count = estimate_token_count("안녕 hello")
        assert count >= 3  # 2 Korean + 1 English word

    def test_punctuation_counted(self) -> None:
        count = estimate_token_count("hello, world!")
        assert count >= 3  # 2 words + at least 1 punct

    def test_minimum_one_token(self) -> None:
        count = estimate_token_count(".")
        assert count >= 1
