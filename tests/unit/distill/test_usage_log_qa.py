"""qa_generator._usage_log_row_to_qa — kb_id 단일값 보존."""

import json

from src.distill.data_gen.qa_generator import _usage_log_row_to_qa


def _row(query: str, ctx: dict) -> tuple:
    return (query, json.dumps(ctx))


def test_returns_qa_with_single_kb_id() -> None:
    row = _row(
        "영업시간?",
        {
            "answer": "9-22",
            "kb_id": "kb-store-faq",
            "crag_action": "correct",
            "crag_confidence": 0.9,
        },
    )
    qa = _usage_log_row_to_qa(row, min_crag_confidence=0.75)
    assert qa is not None
    assert qa["kb_id"] == "kb-store-faq"
    assert "," not in qa["kb_id"]


def test_returns_empty_kb_id_when_missing() -> None:
    row = _row(
        "Q",
        {
            "answer": "A",
            "crag_action": "correct",
            "crag_confidence": 0.9,
        },
    )
    qa = _usage_log_row_to_qa(row, min_crag_confidence=0.75)
    assert qa is not None
    assert qa["kb_id"] == ""


def test_skips_low_crag_confidence() -> None:
    row = _row(
        "Q",
        {
            "answer": "A",
            "kb_id": "kb-x",
            "crag_action": "correct",
            "crag_confidence": 0.5,
        },
    )
    qa = _usage_log_row_to_qa(row, min_crag_confidence=0.75)
    assert qa is None


def test_skips_non_correct_action() -> None:
    row = _row(
        "Q",
        {
            "answer": "A",
            "kb_id": "kb-x",
            "crag_action": "incorrect",
            "crag_confidence": 0.9,
        },
    )
    qa = _usage_log_row_to_qa(row, min_crag_confidence=0.75)
    assert qa is None


def test_skips_when_answer_empty() -> None:
    row = _row(
        "Q",
        {"answer": "", "kb_id": "kb-x", "crag_action": "correct", "crag_confidence": 0.9},
    )
    assert _usage_log_row_to_qa(row, min_crag_confidence=0.75) is None


def test_skips_malformed_context() -> None:
    """JSON parsing 실패 → None."""
    row = ("Q", "not-json")
    assert _usage_log_row_to_qa(row, min_crag_confidence=0.75) is None
