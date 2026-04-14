"""AnswerReformatter 유닛 테스트 — validation gate + batch 흐름."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.distill.data_gen.reformatter import (
    AnswerReformatter,
    BatchSummary,
    ReformatResult,
    build_reformatted_row,
)


GOOD_ANSWER = (
    "OUT 상품 추천은 점포경영시스템이 매출 데이터와 상권 특성을 분석해 자동으로 계산합니다. "
    "중분류별 일매출 상위 상품, 결품 관리 대상, 그리고 경쟁점포 대비 부족한 카테고리를 "
    "종합적으로 고려해 점포에서 아직 취급하지 않는 상품을 우선 추천합니다.\n\n"
    "구체적으로는 ABC 분석을 통해 상위 매출 상품을 식별하고, OSC 상품의 구성비와 "
    "취급 단품 수를 검토해 구색이 부족한 영역을 찾아냅니다. 아파트 거주지 상권이면 "
    "가공식품이 상위로 올라오고, 오피스 상권이면 간편식이 상위로 올라오는 식입니다. "
    "경영주는 이 추천 목록을 검토한 뒤 안전재고율 등을 조정해 최종 확정할 수 있습니다."
)


def _reformatter() -> AnswerReformatter:
    return AnswerReformatter(llm_helper=AsyncMock(), max_retries=1, concurrency=2)


class TestValidate:
    def test_good_format_passes(self) -> None:
        ok, reason = AnswerReformatter._validate(GOOD_ANSWER)
        assert ok, reason
        assert reason == "ok"

    def test_empty_fails(self) -> None:
        ok, reason = AnswerReformatter._validate("")
        assert not ok
        assert reason == "empty"

    def test_too_short_fails(self) -> None:
        ok, reason = AnswerReformatter._validate("짧은 답.\n\n두번째도 짧음.")
        assert not ok
        assert reason.startswith("too_short")

    def test_too_long_fails(self) -> None:
        long_text = ("가" * 400) + "\n\n" + ("나" * 400)
        ok, reason = AnswerReformatter._validate(long_text)
        assert not ok
        assert reason.startswith("too_long")

    def test_single_paragraph_fails(self) -> None:
        # 빈 줄 분리 없음 → 1문단으로 인식
        single = GOOD_ANSWER.replace("\n\n", " ")
        ok, reason = AnswerReformatter._validate(single)
        assert not ok
        assert reason.startswith("wrong_paragraph_count")

    def test_three_paragraphs_fails(self) -> None:
        three = GOOD_ANSWER + "\n\n세 번째 문단입니다. 이 문단은 추가 정보를 담고 있습니다."
        ok, reason = AnswerReformatter._validate(three)
        assert not ok
        assert reason.startswith("wrong_paragraph_count")

    def test_numbered_list_fails(self) -> None:
        numbered = GOOD_ANSWER.replace(
            "구체적으로는 ABC 분석을 통해 상위 매출 상품을 식별하고,",
            "1. ABC 분석을 통해 상위 매출 상품을 식별하고,",
        )
        ok, reason = AnswerReformatter._validate(numbered)
        assert not ok
        assert "forbidden_pattern" in reason

    def test_bullet_list_fails(self) -> None:
        bulleted = GOOD_ANSWER.replace(
            "구체적으로는 ABC 분석을 통해 상위 매출 상품을 식별하고,",
            "- ABC 분석을 통해 상위 매출 상품을 식별하고,",
        )
        ok, reason = AnswerReformatter._validate(bulleted)
        assert not ok
        assert "forbidden_pattern" in reason

    def test_citation_fails(self) -> None:
        cited = GOOD_ANSWER.replace(
            "OUT 상품 추천은",
            "OUT 상품 추천은 ([문서: 매뉴얼][Slide 12])",
            1,
        )
        ok, reason = AnswerReformatter._validate(cited)
        assert not ok
        assert "forbidden_pattern" in reason

    def test_meta_header_fails(self) -> None:
        meta = "[문서 기반]\n" + GOOD_ANSWER
        ok, reason = AnswerReformatter._validate(meta)
        assert not ok
        assert "forbidden_pattern" in reason

    def test_bold_markdown_passes_validation(self) -> None:
        """볼드는 validation 에서는 통과 (strip 은 _strip_boilerplate 에서 수행)."""
        # FORBIDDEN_PATTERNS 에서 ** 제거 → validation 통과해야 함
        bold_stripped = AnswerReformatter._strip_boilerplate(
            GOOD_ANSWER.replace("OUT 상품", "**OUT 상품**", 1),
        )
        ok, reason = AnswerReformatter._validate(bold_stripped)
        assert ok, reason
        # strip 후에는 ** 가 제거돼 있어야 함
        assert "**" not in bold_stripped
        assert "OUT 상품" in bold_stripped

    def test_tiny_second_paragraph_fails(self) -> None:
        # 총 길이는 MIN_CHAR_LEN 이상 확보하되 두 번째 문단만 너무 짧게
        first = GOOD_ANSWER.split("\n\n")[0]
        # 긴 첫 문단을 한 번 반복해서 길이 확보 (length gate 통과용)
        first_padded = first + " 추가 설명을 덧붙여 길이를 확보합니다."
        tiny_second = first_padded + "\n\n짧음."
        assert len(tiny_second) >= 150
        ok, reason = AnswerReformatter._validate(tiny_second)
        assert not ok
        assert "paragraph_1_too_short" in reason


class TestStripBoilerplate:
    def test_removes_leading_reformat_notice(self) -> None:
        raw = "재작성된 답변은 다음과 같습니다:\n\n" + GOOD_ANSWER
        cleaned = AnswerReformatter._strip_boilerplate(raw)
        assert cleaned == GOOD_ANSWER

    def test_leaves_normal_answer_alone(self) -> None:
        cleaned = AnswerReformatter._strip_boilerplate(GOOD_ANSWER)
        assert cleaned == GOOD_ANSWER

    def test_removes_trailing_whitespace(self) -> None:
        raw = "   " + GOOD_ANSWER + "   \n\n"
        cleaned = AnswerReformatter._strip_boilerplate(raw)
        assert cleaned == GOOD_ANSWER

    def test_removes_section_labels(self) -> None:
        """LLM 이 '핵심 답변:' / '추가 설명:' 라벨 붙이면 제거되어야 함."""
        first, second = GOOD_ANSWER.split("\n\n")
        labeled = f"핵심 답변: {first}\n\n추가 설명: {second}"
        cleaned = AnswerReformatter._strip_boilerplate(labeled)
        assert "핵심 답변:" not in cleaned
        assert "추가 설명:" not in cleaned
        # 본문은 보존
        assert "OUT 상품 추천은" in cleaned
        assert "ABC 분석" in cleaned

    def test_removes_alternative_section_labels(self) -> None:
        """결론: / 상세: 같은 변형도 제거."""
        first, second = GOOD_ANSWER.split("\n\n")
        labeled = f"결론: {first}\n\n상세 설명: {second}"
        cleaned = AnswerReformatter._strip_boilerplate(labeled)
        assert "결론:" not in cleaned
        assert "상세 설명:" not in cleaned


class TestReformatOne:
    @pytest.mark.asyncio
    async def test_success_first_attempt(self) -> None:
        r = _reformatter()
        r.llm.call = AsyncMock(return_value=GOOD_ANSWER)

        result = await r.reformat_one({
            "id": "qa-123", "question": "OUT 상품 추천 기준?",
            "answer": "기존의 긴 답변...",
        })

        assert result.success
        assert result.attempts == 1
        assert result.reformatted_answer == GOOD_ANSWER
        assert result.source_id == "qa-123"

    @pytest.mark.asyncio
    async def test_retry_then_success(self) -> None:
        r = _reformatter()
        bad = GOOD_ANSWER.replace("구체적으로는", "1. 구체적으로는")
        r.llm.call = AsyncMock(side_effect=[bad, GOOD_ANSWER])

        result = await r.reformat_one({
            "id": "qa-456", "question": "?", "answer": "x" * 100,
        })

        assert result.success
        assert result.attempts == 2

    @pytest.mark.asyncio
    async def test_all_attempts_fail(self) -> None:
        r = _reformatter()
        bad = GOOD_ANSWER.replace(
            "구체적으로는 ABC 분석을 통해",
            "1. ABC 분석을 통해",
        )
        r.llm.call = AsyncMock(return_value=bad)

        result = await r.reformat_one({
            "id": "qa-789", "question": "?", "answer": "x" * 100,
        })

        assert not result.success
        assert result.attempts == 2
        assert result.failure_reason is not None
        assert "forbidden_pattern" in result.failure_reason

    @pytest.mark.asyncio
    async def test_empty_input_skipped(self) -> None:
        r = _reformatter()
        r.llm.call = AsyncMock()

        result = await r.reformat_one({"id": "qa-000", "question": "", "answer": ""})

        assert not result.success
        assert result.failure_reason == "empty_question_or_answer"
        r.llm.call.assert_not_called()


class TestReformatBatch:
    @pytest.mark.asyncio
    async def test_mixed_results(self) -> None:
        r = _reformatter()
        bad = GOOD_ANSWER.replace("구체적으로는", "1. 구체적으로는")
        # a: 한 번에 성공.  b: 두 번 다 실패 (max_retries=1 이라 총 2회 호출).
        r.llm.call = AsyncMock(side_effect=[GOOD_ANSWER, bad, bad])

        qa_rows = [
            {"id": "a", "question": "q1", "answer": "x" * 100},
            {"id": "b", "question": "q2", "answer": "y" * 100},
        ]
        summary, results = await r.reformat_batch(qa_rows)

        assert summary.total == 2
        assert summary.success == 1
        assert summary.failed == 1
        assert summary.avg_answer_len > 0
        assert len(results) == 2
        by_id = {r.source_id: r for r in results}
        assert by_id["a"].success
        assert not by_id["b"].success


class TestBuildReformattedRow:
    def test_builds_linked_row(self) -> None:
        original = {
            "id": "orig-1", "question": "Q", "answer": "A",
            "source_type": "usage_log", "kb_id": "kb-x",
            "consistency_score": 0.8, "source_id": "log-42",
        }
        row = build_reformatted_row(
            original, "재작성된 답변", profile_name="pbu-store", batch_id="batch-1",
        )
        assert row["id"] != "orig-1"
        assert row["question"] == "Q"
        assert row["answer"] == "재작성된 답변"
        assert row["source_type"] == "reformatted"
        assert row["augmented_from"] == "orig-1"
        assert row["generation_batch_id"] == "batch-1"
        assert row["status"] == "pending"
        assert row["profile_name"] == "pbu-store"
        assert row["kb_id"] == "kb-x"
        assert row["consistency_score"] == 0.8


class TestPreferReformatted:
    """service._prefer_reformatted 가 원본 대신 reformatted 행을 쓰는지 검증."""

    def test_no_reformatted_returns_original(self) -> None:
        from src.distill.service import _prefer_reformatted
        rows = [
            {"id": "1", "source_type": "usage_log", "answer": "원본A"},
            {"id": "2", "source_type": "usage_log", "answer": "원본B"},
        ]
        result = _prefer_reformatted(rows)
        assert len(result) == 2
        assert {r["id"] for r in result} == {"1", "2"}

    def test_reformatted_replaces_original(self) -> None:
        from src.distill.service import _prefer_reformatted
        rows = [
            {"id": "1", "source_type": "usage_log", "answer": "원본A"},
            {"id": "2", "source_type": "usage_log", "answer": "원본B"},
            {"id": "3", "source_type": "reformatted",
             "answer": "재작성A", "augmented_from": "1"},
        ]
        result = _prefer_reformatted(rows)
        # 1 은 제거되고 3 이 대체. 2 는 그대로.
        ids = [r["id"] for r in result]
        assert "1" not in ids
        assert "2" in ids
        assert "3" in ids
        assert len(result) == 2

    def test_partial_reformatted_mix(self) -> None:
        """일부만 reformatted — 점진 전환 시나리오."""
        from src.distill.service import _prefer_reformatted
        rows = [
            {"id": "a", "source_type": "usage_log", "answer": "긴원본1"},
            {"id": "b", "source_type": "usage_log", "answer": "긴원본2"},
            {"id": "c", "source_type": "usage_log", "answer": "긴원본3"},
            {"id": "r-a", "source_type": "reformatted",
             "answer": "짧게재작성1", "augmented_from": "a"},
            {"id": "r-c", "source_type": "reformatted",
             "answer": "짧게재작성3", "augmented_from": "c"},
        ]
        result = _prefer_reformatted(rows)
        ids = {r["id"] for r in result}
        # a, c 는 재작성본으로 대체. b 는 원본 유지.
        assert ids == {"b", "r-a", "r-c"}
        # b 는 원본 answer 유지
        b_row = next(r for r in result if r["id"] == "b")
        assert b_row["answer"] == "긴원본2"


def test_batch_summary_record() -> None:
    summary = BatchSummary()
    summary.record(ReformatResult(source_id="a", success=True, reformatted_answer="x" * 200))
    summary.record(ReformatResult(source_id="b", success=False, failure_reason="too_short(50)"))
    summary.record(ReformatResult(source_id="c", success=False, failure_reason="too_short(40)"))

    assert summary.total == 3
    assert summary.success == 1
    assert summary.failed == 2
    assert summary.failure_reasons == {"too_short(50)": 1, "too_short(40)": 1}
