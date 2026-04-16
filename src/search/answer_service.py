"""Answer Service - LLM-assisted answer generation with tiered response.

Generates answers based on query type with citations and transparency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.config.weights import weights as _w
from .query_classifier import QueryClassifier, QueryType
from .tiered_response import TieredResponseGenerator as _TRG

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


# Prompt templates — SSOT: tiered_response.TieredResponseGenerator
FACTUAL_PROMPT = _TRG.FACTUAL_PROMPT
ANALYTICAL_PROMPT = _TRG.ANALYTICAL_PROMPT
ADVISORY_PROMPT = _TRG.ADVISORY_PROMPT


def _build_context(chunks: list[dict[str, Any]]) -> tuple[str, list[dict]]:
    """Build LLM context string and citation entries from search chunks."""
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
    return "\n\n".join(context_parts), citation_entries


def _determine_confidence(chunks: list[dict[str, Any]]) -> str:
    """Determine confidence level from chunk scores — SSOT: config_weights.ConfidenceConfig."""
    top_score = max((c.get("score", 0) for c in chunks), default=0)
    if top_score >= _w.confidence.high:
        return "높음"
    if top_score >= _w.confidence.medium:
        return "보통"
    return "낮음"


def _determine_disclaimer(query_type: QueryType) -> str | None:
    """Return disclaimer text based on query type."""
    _disclaimers = {
        QueryType.ANALYTICAL: "이 응답은 검색된 문서를 기반으로 추론한 내용입니다.",
        QueryType.ADVISORY: "이 응답에는 문서 외 일반 지식이 포함될 수 있습니다.",
    }
    return _disclaimers.get(query_type)


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
        resolved_type = None
        if query_type_hint:
            try:
                resolved_type = QueryType(query_type_hint)
            except ValueError:
                pass
        if resolved_type is None:
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
        context, citation_entries = _build_context(chunks)

        # Select prompt by query type
        prompt_map = {
            QueryType.FACTUAL: FACTUAL_PROMPT,
            QueryType.ANALYTICAL: ANALYTICAL_PROMPT,
            QueryType.ADVISORY: ADVISORY_PROMPT,
        }
        prompt_template = prompt_map.get(resolved_type, FACTUAL_PROMPT)
        prompt = prompt_template.format(
            query=query, context=context,
            glossary_section="", graph_facts_section="",
        )

        # Generate answer
        if self._llm:
            try:
                answer = await self._llm.generate(prompt=prompt)
            except Exception as e:  # noqa: BLE001
                logger.warning("LLM generation failed: %s", e)
                answer = f"답변 생성 중 오류: {e}\n\n관련 문서 {len(chunks)}건이 검색되었습니다."
        else:
            answer = f"관련 문서 {len(chunks)}건이 검색되었습니다.\n\n" + context

        confidence = _determine_confidence(chunks)
        disclaimer = _determine_disclaimer(resolved_type)

        return AnswerResult(
            answer=answer,
            query_type=resolved_type.value,
            source_label="document" if chunks else "",
            confidence_indicator=confidence,
            citations=f"{len(chunks)}건의 출처",
            disclaimer=disclaimer,
            citation_entries=citation_entries,
        )
