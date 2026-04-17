"""Tests for src/distill/data_gen/test_data_templates.py — pure functions."""

from __future__ import annotations

from src.distill.data_gen.test_data_templates import (
    TEST_QUESTION_TEMPLATES,
    _UNANSWERABLE_PATTERNS,
    _is_unanswerable,
)


class TestTemplateData:
    def test_templates_not_empty(self) -> None:
        assert len(TEST_QUESTION_TEMPLATES) > 0

    def test_all_categories_have_questions(self) -> None:
        for cat, qs in TEST_QUESTION_TEMPLATES.items():
            assert len(qs) > 0, f"Category {cat} has no questions"

    def test_patterns_not_empty(self) -> None:
        assert len(_UNANSWERABLE_PATTERNS) > 0


class TestIsUnanswerable:
    def test_clearly_unanswerable(self) -> None:
        text = (
            "제공된 정보에서 관련 내용을 찾을 수 없습니다. "
            "직접적인 정보는 포함되어 있지 않습니다."
        )
        assert _is_unanswerable(text) is True

    def test_normal_answer(self) -> None:
        text = "개점 시 오픈 절차는 다음과 같습니다: 1단계..."
        assert _is_unanswerable(text) is False

    def test_single_pattern_not_enough(self) -> None:
        text = "제공된 정보에서 일부 내용을 확인했습니다."
        assert _is_unanswerable(text) is False

    def test_two_patterns_enough(self) -> None:
        text = (
            "제공된 문서에서 해당 내용이 명시되어 있지 않습니다. "
            "구체적인 정보가 부족합니다."
        )
        assert _is_unanswerable(text) is True

    def test_long_answer_only_checks_prefix(self) -> None:
        # Patterns at position > 200 should not be detected
        text = "정상 답변 " * 50 + "제공된 정보에서 관련 내용 없음"
        assert _is_unanswerable(text) is False
