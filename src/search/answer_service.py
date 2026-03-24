"""Answer Service - LLM-assisted answer generation with tiered response.

Generates answers based on query type with citations and transparency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .query_classifier import QueryClassifier, QueryType

logger = logging.getLogger(__name__)

# Relationship type Korean labels
REL_TYPE_KO: dict[str, str] = {
    "RESPONSIBLE_FOR": "담당",
    "CREATED_BY": "작성",
    "MODIFIED_BY": "수정",
    "MEMBER_OF": "소속",
    "MENTIONS": "언급",
    "COVERS": "다룸",
    "OWNS": "소유",
    "BELONGS_TO": "소속",
}

NODE_TYPE_KO: dict[str, str] = {
    "Person": "담당자",
    "System": "시스템",
    "Topic": "주제",
    "Document": "문서",
    "Team": "팀",
}


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    query_type: str
    source_label: str
    confidence_indicator: str
    citations: str | None = None
    disclaimer: str | None = None
    citation_entries: list[dict[str, Any]] | None = None


# Prompt templates for tiered response
FACTUAL_PROMPT = """당신은 사내 지식 검색 어시스턴트입니다.
다음 질문에 대해 제공된 문서를 기반으로 답변하세요.

질문: {query}

참고 문서:
{context}

핵심 원칙:
- 수치 충실도: 문서의 수치는 반드시 그대로 인용하세요
- 약어/고유명사: 문서에 정의되지 않은 약어의 뜻을 추측하지 마세요
- 문서에 없는 수치, 비율, 금액, 날짜를 절대 생성하지 마세요
- 각 사실에 출처 번호 [1], [2] 등으로 표시하세요
- 문서에 없는 내용을 지어내지 마세요
"""

ANALYTICAL_PROMPT = """다음 질문에 대해 제공된 문서를 분석하여 답변하세요.

질문: {query}

참고 문서:
{context}

답변 형식:
- [문서 기반] 태그로 문서에서 직접 인용한 내용 표시
- [분석] 태그로 추론한 내용 표시
- 각 내용에 출처 번호 표시
"""

ADVISORY_PROMPT = """다음 질문에 대해 조언을 제공하세요.

질문: {query}

참고 문서:
{context}

답변 형식:
- [문서 기반] 문서 참고 내용
- [권장 사항] 일반적인 조언
"""


class AnswerService:
    """Generate tiered LLM answers based on query type."""

    def __init__(self, llm_client: Any = None, classifier: QueryClassifier | None = None):
        self._llm = llm_client
        self._classifier = classifier or QueryClassifier()

    async def enrich(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        query_type_hint: str | None = None,
    ) -> AnswerResult:
        """Generate answer + transparency info from search chunks."""
        # Classify query
        if query_type_hint:
            try:
                resolved_type = QueryType(query_type_hint)
            except ValueError:
                resolved_type = self._classifier.classify(query).query_type
        else:
            resolved_type = self._classifier.classify(query).query_type

        if resolved_type == QueryType.CHITCHAT:
            return AnswerResult(
                answer="안녕하세요! 지식 검색 시스템입니다. 궁금한 것을 물어보세요.",
                query_type="chitchat",
                source_label="",
                confidence_indicator="",
            )

        if not chunks:
            return AnswerResult(
                answer=f"'{query}'에 대한 정보를 문서에서 찾을 수 없습니다.",
                query_type=resolved_type.value,
                source_label="",
                confidence_indicator="낮음",
                disclaimer="검색 결과가 없습니다.",
            )

        # Build context
        context_parts = []
        citation_entries = []
        for i, chunk in enumerate(chunks):
            content = chunk.get("content", "")
            doc_name = chunk.get("document_name", "Unknown")
            context_parts.append(f"[{i+1}] ({doc_name})\n{content}")
            citation_entries.append({
                "index": i + 1,
                "document_name": doc_name,
                "source_uri": chunk.get("source_uri", ""),
                "score": chunk.get("score", 0),
            })

        context = "\n\n".join(context_parts)

        # Select prompt by query type
        prompt_map = {
            QueryType.FACTUAL: FACTUAL_PROMPT,
            QueryType.ANALYTICAL: ANALYTICAL_PROMPT,
            QueryType.ADVISORY: ADVISORY_PROMPT,
        }
        prompt_template = prompt_map.get(resolved_type, FACTUAL_PROMPT)
        prompt = prompt_template.format(query=query, context=context)

        # Generate answer
        if self._llm:
            try:
                answer = await self._llm.generate(prompt=prompt)
            except Exception as e:
                logger.warning("LLM generation failed: %s", e)
                answer = f"답변 생성 중 오류: {e}\n\n관련 문서 {len(chunks)}건이 검색되었습니다."
        else:
            answer = f"관련 문서 {len(chunks)}건이 검색되었습니다.\n\n" + context

        # Determine confidence
        top_score = max((c.get("score", 0) for c in chunks), default=0)
        if top_score >= 0.85:
            confidence = "높음"
        elif top_score >= 0.7:
            confidence = "보통"
        else:
            confidence = "낮음"

        # Disclaimer
        disclaimer = None
        if resolved_type == QueryType.ANALYTICAL:
            disclaimer = "이 응답은 검색된 문서를 기반으로 추론한 내용입니다."
        elif resolved_type == QueryType.ADVISORY:
            disclaimer = "이 응답에는 문서 외 일반 지식이 포함될 수 있습니다."

        return AnswerResult(
            answer=answer,
            query_type=resolved_type.value,
            source_label="document" if chunks else "",
            confidence_indicator=confidence,
            citations=f"{len(chunks)}건의 출처",
            disclaimer=disclaimer,
            citation_entries=citation_entries,
        )
