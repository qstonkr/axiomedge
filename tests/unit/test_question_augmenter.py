"""QuestionAugmenter 유닛 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.distill.data_gen.question_augmenter import (
    AugmentResult,
    BatchSummary,
    QuestionAugmenter,
    _has_answer_leak,
    build_augmented_row,
)


def _aug(n: int = 4) -> QuestionAugmenter:
    return QuestionAugmenter(
        llm_helper=AsyncMock(), n_variations=n, max_retries=1, concurrency=2,
    )


class TestParse:
    def test_parses_clean_lines(self) -> None:
        a = _aug(n=4)
        raw = (
            "OUT 상품 추천 기준이 어떻게 되는지 알려주세요\n"
            "어떤 기준으로 OUT 상품을 추천하나요\n"
            "OUT 상품 선정은 무슨 기준으로 이루어지는지 궁금합니다\n"
            "OUT 추천 상품을 결정하는 기준이 뭔가요"
        )
        result = a._parse(raw, original="OUT 상품 추천은 어떤 기준?")
        assert len(result) == 4
        assert all(len(q) >= 8 for q in result)

    def test_strips_numbered_prefix(self) -> None:
        a = _aug(n=4)
        raw = (
            "1. OUT 상품 추천 기준이 어떻게 되는지 알려주세요\n"
            "2) 어떤 기준으로 OUT 상품을 추천하나요\n"
            "- OUT 상품 선정은 무슨 기준으로 이루어지는지 궁금합니다\n"
            "• OUT 추천 상품을 결정하는 기준이 뭔가요"
        )
        result = a._parse(raw, original="OUT 상품 추천은 어떤 기준?")
        assert len(result) == 4
        assert not any(q.startswith(("1.", "2)", "-", "•")) for q in result)

    def test_drops_identical_to_original(self) -> None:
        a = _aug(n=4)
        original = "OUT 상품 추천은 어떤 기준?"
        raw = f"{original}\n완전히 다른 질문 표현입니다\n또 다른 변형 질문이에요\n세 번째 변형 질문"
        result = a._parse(raw, original=original)
        # 원본 identical 은 제외
        assert original not in result

    def test_drops_too_short(self) -> None:
        a = _aug(n=4)
        raw = "뭐?\n어떻게?\n매우 긴 정상 질문 문장 하나 여기 있어요\n또 다른 정상 질문이에요"
        result = a._parse(raw, original="original")
        # 너무 짧은 건 빠짐
        assert all(len(q) >= 8 for q in result)

    def test_dedupes(self) -> None:
        a = _aug(n=4)
        raw = (
            "첫번째 변형 질문 표현입니다\n"
            "첫번째 변형 질문 표현입니다\n"
            "두번째 변형 질문 표현입니다\n"
            "세번째 변형 질문 표현입니다"
        )
        result = a._parse(raw, original="original")
        # 중복 제거되어 3개만 남음
        assert len(result) == 3


class TestValidate:
    def test_too_short(self) -> None:
        assert not QuestionAugmenter._valid("짧음", "original longer question")

    def test_too_long(self) -> None:
        long_q = "가" * 250
        assert not QuestionAugmenter._valid(long_q, "original")

    def test_identical_to_original(self) -> None:
        assert not QuestionAugmenter._valid("같은 질문", "같은 질문")

    def test_valid(self) -> None:
        assert QuestionAugmenter._valid(
            "이것은 원본과 다른 정상 질문입니다",
            "전혀 다른 원본 질문 예시 중 하나",
        )


class TestAugmentOne:
    @pytest.mark.asyncio
    async def test_success_first_attempt(self) -> None:
        a = _aug(n=4)
        good_raw = (
            "첫번째 다른 표현으로 바꾼 질문입니다\n"
            "두번째 질문 변형 표현입니다\n"
            "세번째 질문 변형 표현입니다\n"
            "네번째 질문 변형 표현입니다"
        )
        a.llm.call = AsyncMock(return_value=good_raw)

        result = await a.augment_one({
            "id": "q-1", "question": "OUT 상품 기준?", "answer": "..."
        })

        assert result.success
        assert len(result.variations) == 4
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_retry_on_partial(self) -> None:
        a = _aug(n=4)
        partial = "첫번째 정상 질문 하나 있습니다\n뭐?"  # 1개만 valid
        full = (
            "첫번째 정상 질문 하나 있습니다\n"
            "두번째 정상 질문 있습니다\n"
            "세번째 정상 질문 있습니다\n"
            "네번째 정상 질문 있습니다"
        )
        a.llm.call = AsyncMock(side_effect=[partial, full])

        result = await a.augment_one({"id": "q-2", "question": "원본 질의"})
        assert result.success
        assert result.attempts == 2
        assert len(result.variations) == 4

    @pytest.mark.asyncio
    async def test_partial_after_retries(self) -> None:
        a = _aug(n=4)
        partial = "정상 질문 하나만 있어요\n뭐?"  # 항상 1개만
        a.llm.call = AsyncMock(return_value=partial)

        result = await a.augment_one({"id": "q-3", "question": "원본"})
        # 최종 시도 후에도 부족 → partial 로 저장
        assert not result.success or len(result.variations) < 4
        if result.variations:
            assert "정상 질문 하나만 있어요" in result.variations

    @pytest.mark.asyncio
    async def test_empty_question_skipped(self) -> None:
        a = _aug(n=4)
        a.llm.call = AsyncMock()
        result = await a.augment_one({"id": "q-4", "question": ""})
        assert not result.success
        assert result.failure_reason == "empty_question"
        a.llm.call.assert_not_called()


class TestBuildAugmentedRow:
    def test_new_row_linked_to_parent(self) -> None:
        parent = {
            "id": "ref-1", "question": "원본 질문",
            "answer": "재작성된 답변입니다", "source_type": "reformatted",
            "kb_id": "pbu", "consistency_score": 0.85,
        }
        row = build_augmented_row(
            parent, "변형 질문 하나",
            profile_name="pbu-store", batch_id="batch-X",
        )
        assert row["id"] != "ref-1"
        assert row["question"] == "변형 질문 하나"
        assert row["answer"] == "재작성된 답변입니다"  # parent 답변 재사용
        assert row["source_type"] == "reformatted_aug"
        assert row["augmented_from"] == "ref-1"  # parent (reformatted) 의 id
        assert row["status"] == "pending"
        assert row["generation_batch_id"] == "batch-X"
        assert row["kb_id"] == "pbu"


class TestAnswerLeak:
    """_has_answer_leak — 변형이 답변 내용을 포함하는지 검출."""

    def test_acronym_paraphrase_leaks(self) -> None:
        """ISP 같은 약어를 풀어쓴 변형은 leak 판정."""
        original = "ISP가 뭐야?"
        variation = "인터넷 서비스 제공업체가 뭔가요?"
        answer = "ISP는 인터넷 서비스 제공업체(Internet Service Provider)를 의미합니다."
        assert _has_answer_leak(variation, original, answer)

    def test_acronym_kept_not_leak(self) -> None:
        """약어 원형 유지하는 변형은 leak 아님."""
        original = "ISP가 뭐야?"
        variation = "ISP란 무엇인가요?"
        answer = "ISP는 인터넷 서비스 제공업체입니다."
        assert not _has_answer_leak(variation, original, answer)

    def test_synonym_from_question_not_leak(self) -> None:
        """원본 질문에 이미 있는 단어의 변형은 leak 아님."""
        original = "마감 시간 할인율이 몇 %인가요?"
        variation = "마감 시간 할인이 몇 퍼센트인지 알려주세요"
        answer = "마감 시간 할인율은 10% 입니다."
        # "할인율" → "할인" 유지, 퍼센트 치환 정도는 OK
        assert not _has_answer_leak(variation, original, answer)

    def test_new_content_from_answer_leaks(self) -> None:
        """답변에만 있는 내용이 변형에 들어가면 leak."""
        original = "폐기 절차가 어떻게 돼?"
        # 답변에는 "POS", "폐기대장", "보관" 같은 구체 정보
        variation = "POS 에서 폐기대장 등록은 어떻게 하나요?"
        answer = "폐기는 POS 에서 등록하고 폐기대장에 기록합니다. 별도 보관 필요."
        # 변형에 POS, 폐기대장 가 들어옴 (답변에서 온 것) → leak
        assert _has_answer_leak(variation, original, answer)

    def test_no_new_tokens_not_leak(self) -> None:
        """변형이 원본 단어만 재조합하면 leak 아님."""
        original = "매출 분석 기준은 뭔가요?"
        variation = "기준이 뭔지 매출 분석에서 궁금해요"
        answer = "매출 분석 기준은 ABC 등급제 + 월간 추이..."
        assert not _has_answer_leak(variation, original, answer)


class TestBatchSummary:
    def test_records_success_and_failure(self) -> None:
        s = BatchSummary()
        s.record(AugmentResult(source_id="a", variations=["q1", "q2", "q3", "q4"]))
        s.record(AugmentResult(source_id="b", failure_reason="parsed_only_1_of_4"))
        assert s.total == 2
        assert s.success == 1
        assert s.failed == 1
        assert s.total_variations_generated == 4
        assert s.failure_reasons == {"parsed_only_1_of_4": 1}
