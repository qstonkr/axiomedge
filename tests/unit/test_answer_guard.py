"""Unit tests for src/search/answer_guard.py."""

from src.search.answer_guard import AnswerGuard, _should_replace, _grounded_snippet, _build_fallback_answer


def _make_chunks(n: int = 2) -> list[dict]:
    return [
        {"content": f"Chunk {i} content text here.", "document_name": f"Doc {i}", "kb_id": "kb1"}
        for i in range(1, n + 1)
    ]


class TestShouldReplace:
    """Test the _should_replace detection logic."""

    def test_empty_answer(self) -> None:
        assert _should_replace("", chunks=_make_chunks()) is True

    def test_whitespace_answer(self) -> None:
        assert _should_replace("   \n  ", chunks=_make_chunks()) is True

    def test_no_chunks(self) -> None:
        assert _should_replace("Good answer", chunks=[]) is True

    def test_disclaimer_no_grounding(self) -> None:
        assert _should_replace(
            "Some answer",
            chunks=_make_chunks(),
            disclaimer="검색된 근거 문서 없이 생성된 답변입니다.",
        ) is True

    def test_generic_korean_pattern_short(self) -> None:
        # Short generic "no info found" pattern
        assert _should_replace(
            "제공된 문서에는 해당 정보가 포함되어 있지 않습니다.",
            chunks=_make_chunks(),
        ) is True

    def test_generic_contact_pattern(self) -> None:
        assert _should_replace(
            "담당자에게 문의해 주시기 바랍니다",
            chunks=_make_chunks(),
        ) is True

    def test_specific_answer_passes(self) -> None:
        specific = "VPN 접속을 위해서는 먼저 OTP 인증을 완료해야 합니다."
        assert _should_replace(specific, chunks=_make_chunks()) is False

    def test_long_answer_passes(self) -> None:
        # Answers > 200 chars are not considered generic
        long_answer = "A" * 201
        assert _should_replace(long_answer, chunks=_make_chunks()) is False

    def test_info_not_found_pattern(self) -> None:
        assert _should_replace(
            "정보를 찾을 수 없습니다",
            chunks=_make_chunks(),
        ) is True


class TestGroundedSnippet:
    """Test snippet generation."""

    def test_short_content(self) -> None:
        assert _grounded_snippet("Hello") == "Hello"

    def test_long_content_truncated(self) -> None:
        long_text = "A" * 300
        snippet = _grounded_snippet(long_text, limit=180)
        assert len(snippet) <= 180
        assert snippet.endswith("…")

    def test_empty_content(self) -> None:
        assert _grounded_snippet("") == "본문 미리보기를 제공할 수 없습니다."

    def test_none_content(self) -> None:
        assert _grounded_snippet(None) == "본문 미리보기를 제공할 수 없습니다."


class TestBuildFallbackAnswer:
    """Test fallback answer construction."""

    def test_no_chunks(self) -> None:
        answer = _build_fallback_answer("test query", [])
        assert "찾지 못했습니다" in answer

    def test_with_chunks(self) -> None:
        chunks = _make_chunks(3)
        answer = _build_fallback_answer("VPN 접속", chunks)
        assert "VPN 접속" in answer
        assert "Doc 1" in answer
        assert "Doc 2" in answer

    def test_extra_chunks_note(self) -> None:
        chunks = _make_chunks(5)
        answer = _build_fallback_answer("query", chunks)
        # Only top 3 shown, rest mentioned
        assert "2건" in answer  # 5 - 3 = 2


class TestAnswerGuard:
    """Test the public AnswerGuard.guard() API."""

    def setup_method(self) -> None:
        self.guard = AnswerGuard()

    def test_good_answer_passes_through(self) -> None:
        answer = "VPN은 192.168.1.1로 접속하며, OTP 인증이 필요합니다."
        result = self.guard.guard(answer, _make_chunks(), "VPN 접속 방법")
        assert result == answer

    def test_generic_answer_replaced(self) -> None:
        generic = "제공된 문서에는 해당 정보가 포함되어 있지 않습니다."
        result = self.guard.guard(generic, _make_chunks(), "VPN 접속 방법")
        assert result != generic
        assert "VPN 접속 방법" in result

    def test_none_answer_replaced(self) -> None:
        result = self.guard.guard(None, _make_chunks(), "test")
        assert "test" in result

    def test_empty_answer_replaced(self) -> None:
        result = self.guard.guard("", _make_chunks(), "test")
        assert "test" in result

    def test_no_chunks_fallback(self) -> None:
        result = self.guard.guard("any answer", [], "test")
        assert "찾지 못했습니다" in result
