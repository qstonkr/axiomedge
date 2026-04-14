"""답변 Reformatter — 기존 RAG-style 긴 답변을 1B 모델이 학습 가능한 포맷으로 재작성.

배경:
    usage_log에서 수집한 RAG 답변은 번호 목록 + 인용 태그 + 다중 문단 구조로
    500+ 토큰에 달한다. 1B 모델(Gemma-3-1B)이 이런 복잡한 구조를 memorize 하기엔
    capacity 가 부족해서 train_loss 가 1.7 수준에서 정체된다 (검증됨).

    해결: 원본 fact 는 보존하되, 포맷을 "핵심 2~3문장 + 빈줄 + 추가 2~4문장"
    구조로 일관되게 재작성. 인용/번호/메타 태그 제거. 1B 에게 학습 가능한
    consistent answer template 을 제공.

배치 처리 원칙:
    - 원본 training_data 행은 건드리지 않음
    - 재작성된 행은 source_type="reformatted" + augmented_from=<원본 id> 로 저장
    - 템플릿 개선 시 같은 코드로 다시 돌려 v2/v3 생성 가능
    - 검증 실패 샘플은 원본 유지 (누락 없음)
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from src.distill.data_gen.llm_helper import LLMHelper

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 프롬프트 — 1B 모델이 학습 가능한 풍부하지만 일관된 포맷
# ---------------------------------------------------------------------------

REFORMAT_PROMPT_TEMPLATE = """\
아래 질문과 원본 답변을 참고하여, 답변을 지정된 형식으로 재작성하세요.

[질문]
{question}

[원본 답변]
{answer}

[재작성 규칙]
1. 반드시 정확히 두 개의 문단으로 출력. 한 덩어리 문단으로 뭉쳐 쓰면 안 됨.
   두 문단 사이에는 반드시 **빈 줄 하나** (\\n\\n) 를 넣어 분리할 것.
   - 1문단: 질문에 직접 답하는 결론과 그 핵심 이유 (2~3문장).
   - 2문단: 관련 맥락, 구체 절차, 예시, 주의사항 중 2개 이상 포함 (2~4문장).
2. 섹션 라벨 금지 — "핵심 답변:", "추가 설명:", "결론:", "상세:" 같은 말머리 쓰지 말 것.
   각 문단은 바로 내용 문장으로 시작해야 함.
3. 금지 사항 (반드시 모두 제거):
   - 번호 목록 ("1.", "2.", "①" 등)
   - 들여쓰기된 하위 bullet ("-", "•")
   - 인용/출처 태그 ("[문서: ...]", "[Slide N]", "[KB: ...]", "출처:", "([...][...])")
   - 메타 헤더 ("[문서 기반]", "[권장 사항]", "참고:")
   - 마크다운 볼드/이탤릭 (별표 두 개, 별표 하나로 강조하기)
4. 자연스러운 한국어 문장 흐름. 존댓말(습니다 체) 사용.
5. 원본의 핵심 정보(fact)는 모두 유지. 압축하되 생략하지 말 것.
6. 전체 길이: 200~450자 한국어.

[출력 형식 예시 — 형식만 참고하고 내용은 질문/원본에 맞게]
첫번째 결론 문장입니다. 핵심 이유를 설명합니다. 짧은 보강 문장이 올 수도 있습니다.

구체 절차나 맥락을 설명하는 첫 문장입니다. 예시나 주의사항을 담은 두번째 문장입니다. 필요하면 세번째 문장까지 이어 쓸 수 있습니다.

[출력은 위 두 문단 형식만. 다른 머리말/꼬리말/설명 추가 금지]
"""

# LLM 이 가끔 붙이는 섹션 라벨 — 통과돼도 학습에 해로우니 post-process 에서 제거
SECTION_LABEL_PATTERNS = [
    re.compile(r"^\s*(핵심\s*답변|결론|답변|요약)\s*[:：]\s*", re.MULTILINE),
    re.compile(r"^\s*(추가\s*설명|상세|상세\s*설명|부연|부가\s*설명|참고)\s*[:：]\s*", re.MULTILINE),
]

# 마크다운 볼드/이탤릭 — reject 하지 말고 strip (의미 손실 없음)
BOLD_PATTERN = re.compile(r"\*\*([^*\n]+)\*\*")
ITALIC_PATTERN = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")

# ---------------------------------------------------------------------------
# 검증 게이트 — 프롬프트로 지시해도 LLM 이 실수할 수 있어서 이중 체크
# ---------------------------------------------------------------------------

# 포맷 gate 에서 탈락시킬 패턴들 — 남아 있으면 1B 학습 신호에 노이즈
# 참고: 볼드/이탤릭 마크다운 (**..**, *..*) 은 reject 하지 않고
# _strip_boilerplate 에서 제거. 내용 손실 없이 복구 가능해서.
FORBIDDEN_PATTERNS = [
    re.compile(r"\[문서[:：]"),
    re.compile(r"\[Slide\s*\d+\]"),
    re.compile(r"\[KB[:：]"),
    re.compile(r"^\s*출처[:：]", re.MULTILINE),
    re.compile(r"\[문서\s*기반\]"),
    re.compile(r"\[권장\s*사항\]"),
    re.compile(r"^\s*\d+[\.)]\s", re.MULTILINE),  # "1. " / "1) "
    re.compile(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩]", re.MULTILINE),
    re.compile(r"^\s*[-•·]\s", re.MULTILINE),      # bullet points
]

# 길이 범위 — 너무 짧으면 정보 손실, 너무 길면 1B 가 학습 못함
MIN_CHAR_LEN = 150
MAX_CHAR_LEN = 700


@dataclass
class ReformatResult:
    """재작성 결과 — 성공/실패 여부와 사유를 함께."""

    source_id: str
    success: bool
    reformatted_answer: str | None = None
    failure_reason: str | None = None
    attempts: int = 1

    def __bool__(self) -> bool:
        return self.success


@dataclass
class BatchSummary:
    """배치 전체 결과 요약 — 대시보드/로그 출력용."""

    total: int = 0
    success: int = 0
    failed: int = 0
    failure_reasons: dict[str, int] = field(default_factory=dict)
    avg_answer_len: float = 0.0

    def record(self, result: ReformatResult) -> None:
        self.total += 1
        if result.success:
            self.success += 1
        else:
            self.failed += 1
            reason = result.failure_reason or "unknown"
            self.failure_reasons[reason] = self.failure_reasons.get(reason, 0) + 1


class AnswerReformatter:
    """Teacher LLM 기반 답변 재작성기.

    Usage:
        reformatter = AnswerReformatter(llm_helper)
        result = await reformatter.reformat_one(qa_row)
        if result.success:
            save_as_reformatted(result)

        summary, results = await reformatter.reformat_batch(qa_rows)
    """

    def __init__(
        self,
        llm_helper: LLMHelper,
        max_retries: int = 2,
        concurrency: int = 4,
    ) -> None:
        self.llm = llm_helper
        self.max_retries = max_retries
        self._semaphore = asyncio.Semaphore(concurrency)

    async def reformat_one(self, qa: dict[str, Any]) -> ReformatResult:
        """QA 한 건 재작성 — LLM 호출 + 검증 + 실패 시 재시도."""
        source_id = qa.get("id", "")
        question = qa.get("question", "").strip()
        answer = qa.get("answer", "").strip()

        if not question or not answer:
            return ReformatResult(
                source_id=source_id, success=False,
                failure_reason="empty_question_or_answer",
            )

        prompt = REFORMAT_PROMPT_TEMPLATE.format(question=question, answer=answer)

        last_reason = "llm_no_response"
        for attempt in range(1, self.max_retries + 2):
            async with self._semaphore:
                # 낮은 temperature — 일관된 포맷이 목표. 창의성 불필요.
                response = await self.llm.call(prompt, temperature=0.2)

            cleaned = self._strip_boilerplate(response)
            ok, reason = self._validate(cleaned)
            if ok:
                return ReformatResult(
                    source_id=source_id, success=True,
                    reformatted_answer=cleaned, attempts=attempt,
                )
            last_reason = reason
            logger.debug(
                "Reformat attempt %d failed (id=%s, reason=%s)",
                attempt, source_id[:8], reason,
            )

        return ReformatResult(
            source_id=source_id, success=False,
            failure_reason=last_reason, attempts=self.max_retries + 1,
        )

    async def reformat_batch(
        self, qa_rows: list[dict[str, Any]],
    ) -> tuple[BatchSummary, list[ReformatResult]]:
        """배치 재작성 — 동시성은 내부 semaphore 로 관리.

        실패한 샘플은 그대로 결과에 포함되고 호출자가 "원본 유지" 결정을 내림.
        """
        summary = BatchSummary()
        tasks = [self.reformat_one(qa) for qa in qa_rows]
        results = await asyncio.gather(*tasks)

        total_len = 0
        for r in results:
            summary.record(r)
            if r.success and r.reformatted_answer:
                total_len += len(r.reformatted_answer)

        if summary.success:
            summary.avg_answer_len = round(total_len / summary.success, 1)

        logger.info(
            "Reformat batch done: %d/%d success (failures: %s, avg_len=%.1f)",
            summary.success, summary.total,
            summary.failure_reasons, summary.avg_answer_len,
        )
        return summary, results

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_boilerplate(raw: str) -> str:
        """LLM 이 가끔 붙이는 머리말 + 섹션 라벨 제거.

        1단계: "재작성된 답변은 다음과 같습니다:" 같은 도입부 제거
        2단계: "핵심 답변:", "추가 설명:" 같은 섹션 라벨 제거
               (프롬프트에서 금지했어도 LLM 이 붙일 때가 있음 — 학습에 해로우므로 반드시 제거)
        """
        text = raw.strip()
        # 1단계: 맨 앞 전문성 표현 제거 (최대 1줄)
        lines = text.split("\n")
        if lines and any(
            kw in lines[0] for kw in ("재작성", "다음과 같", "아래와 같")
        ) and lines[0].rstrip().endswith((":", ":", ".", "다")):
            lines = lines[1:]
            while lines and not lines[0].strip():
                lines.pop(0)
        text = "\n".join(lines).strip()

        # 2단계: 각 문단 앞 섹션 라벨 제거
        for pat in SECTION_LABEL_PATTERNS:
            text = pat.sub("", text)

        # 3단계: 마크다운 볼드/이탤릭을 텍스트만 남김
        # (LLM 이 가끔 "**중요**" 처럼 붙이는데, 의미 보존하려면 reject 보다 strip 이 나음)
        text = BOLD_PATTERN.sub(r"\1", text)
        text = ITALIC_PATTERN.sub(r"\1", text)

        return text.strip()

    @staticmethod
    def _validate(text: str) -> tuple[bool, str]:
        """포맷 gate — 실패 시 (False, reason) 반환."""
        if not text:
            return False, "empty"

        if len(text) < MIN_CHAR_LEN:
            return False, f"too_short({len(text)})"
        if len(text) > MAX_CHAR_LEN:
            return False, f"too_long({len(text)})"

        for pat in FORBIDDEN_PATTERNS:
            if pat.search(text):
                return False, f"forbidden_pattern({pat.pattern})"

        # 2 문단 체크 — 빈 줄 하나로 분리된 2개 문단
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if len(paragraphs) != 2:
            return False, f"wrong_paragraph_count({len(paragraphs)})"

        # 각 문단 최소 길이 (너무 짧은 문단은 정보 부족)
        for i, p in enumerate(paragraphs):
            if len(p) < 40:
                return False, f"paragraph_{i}_too_short({len(p)})"

        return True, "ok"


# ---------------------------------------------------------------------------
# 배치 저장 헬퍼 — 재작성 결과를 DB 에 원본과 별도 행으로 저장
# ---------------------------------------------------------------------------

def build_reformatted_row(
    original: dict[str, Any],
    reformatted_answer: str,
    profile_name: str,
    batch_id: str,
) -> dict[str, Any]:
    """재작성 결과를 training_data 저장용 row dict 로 변환.

    새 행의 특징:
        - 새 id (uuid4)
        - source_type="reformatted" — 원본(`usage_log` 등)과 구분
        - augmented_from=<원본 id> — 계보 추적
        - status="pending" — 사람이 리뷰하고 approve
        - generation_batch_id=<batch_id> — 배치 단위 통계/롤백
    """
    return {
        "id": str(uuid.uuid4()),
        "profile_name": profile_name,
        "question": original["question"],
        "answer": reformatted_answer,
        "source_type": "reformatted",
        "source_id": original.get("source_id"),
        "kb_id": original.get("kb_id"),
        "status": "pending",
        "consistency_score": original.get("consistency_score"),
        "generality_score": original.get("generality_score"),
        "augmentation_verified": None,
        "augmented_from": original.get("id"),
        "generation_batch_id": batch_id,
    }
