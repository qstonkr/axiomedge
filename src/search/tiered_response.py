"""Tiered Response Generator.

Tiered response generation service.
Applies different response strategies based on query type:
- FACTUAL: Document-based strict response, "not found" if absent
- ANALYTICAL: Document + inference allowed, mark inferred parts
- ADVISORY: Document + general knowledge allowed, mark opinions
- CHITCHAT: Friendly greeting/small talk response

Extracted from oreo-ecosystem tiered_response.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.config_weights import weights
from .query_classifier import QueryType
from .citation_formatter import CitationFormatter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM Client interface (simplified from oreo ILLMClient)
# ---------------------------------------------------------------------------

class ILLMClient(Protocol):
    """LLM client interface for tiered response generation."""

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        ...


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RAGContext:
    """RAG context for response generation."""

    query: str
    retrieved_chunks: list[str]
    chunk_sources: list[dict] = field(default_factory=list)
    relevance_scores: list[float] = field(default_factory=list)
    glossary_definitions: list[dict] = field(default_factory=list)
    graph_facts: list[str] = field(default_factory=list)

    def has_relevant_context(self, threshold: float = weights.response.default_relevance_threshold) -> bool:
        """Check if relevant context exists (graph facts also count as context)."""
        if self.graph_facts:
            return True
        if not self.relevance_scores:
            return len(self.retrieved_chunks) > 0
        return any(score >= threshold for score in self.relevance_scores)


@dataclass
class TieredResponse:
    """Tiered response output."""

    content: str
    query_type: QueryType
    source_type: str  # document, inference, general
    citations: list[dict]
    confidence: float
    disclaimer: str | None = None
    follow_up_suggestions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# TieredResponseGenerator
# ---------------------------------------------------------------------------

class TieredResponseGenerator:
    """Tiered response generator.

    Applies appropriate response strategy based on query type.
    """

    # Response templates
    NO_DOCUMENT_TEMPLATE = "문서에서 '{query}'에 대한 정보를 찾을 수 없습니다."
    INFERENCE_DISCLAIMER = "이 응답은 검색된 문서를 기반으로 추론한 내용입니다."
    GENERAL_KNOWLEDGE_DISCLAIMER = "이 응답에는 문서 외 일반 지식이 포함되어 있습니다."
    ERROR_DISCLAIMER = "내부 오류로 상세 정보를 제공할 수 없습니다. 잠시 후 다시 시도해 주세요."

    # Prompt templates
    FACTUAL_PROMPT = """당신은 GS리테일의 사내 지식 검색 어시스턴트입니다.
다음 질문에 대해 제공된 문서와 조직/시스템 정보를 종합하여 답변하세요.

질문: {query}
{glossary_section}
참고 문서:
{context}
{graph_facts_section}
주의 - OCR 노이즈:
- 참고 문서는 PPT/PDF에서 OCR로 추출되어 깨진 문자가 포함될 수 있습니다
- 의미 없는 영문 조합(IEYV, OJnD, ITLL 등), 깨진 한글(극Y름, 관야 이이), 무작위 기호는 무시하세요
- 깨진 텍스트를 해석하거나 추측하지 말고, 의미가 명확한 부분만 활용하세요

핵심 원칙 (반드시 준수):
- 수치 충실도: 문서에 "9.3% 신장"이라고 적혀 있으면 반드시 "9.3% 신장"이라고 답변하세요. 절대로 다른 수치로 바꾸거나 "감소"로 변환하지 마세요.
- 약어/고유명사: 문서에 정의되지 않은 약어(예: ESPA)의 뜻을 추측하지 마세요. 문서에 설명이 없으면 약어 그대로 사용하세요.
- 금지 행위: 문서에 없는 수치, 비율, 금액, 날짜를 절대 생성하지 마세요.

답변 규칙:
- 제공된 문서 전체를 분석하여 질문과 관련된 정보를 빠짐없이 추출하세요
- 제공된 문서에서 직접 확인 가능한 사실만 답변하세요
- 문서에 근거가 부족하면 일반적인 모범 사례나 추정으로 보완하지 마세요
- 관련 정보가 일부만 있으면 확인 가능한 사실만 짧게 정리하고, 부족한 정보는 없다고 명시하세요
- 문서에 포함된 구체적 수치, 명령어, 기술 용어, 담당자 정보는 문서에 적힌 그대로 포함하세요
- 각 사실에 출처 번호 [1], [2] 등으로 표시하세요
- 용어집에 정의된 용어는 정확한 정의를 반영하여 답변하세요
- 조직/시스템 정보가 있으면 담당자, 관계 정보를 우선 활용하여 답변하세요
- 문서에 없는 내용을 지어내지 마세요
- 제공된 모든 문서에 질문 주제와 관련된 정보가 전혀 없거나 근거가 부족할 때만 "해당 주제에 대한 정보를 제공된 문서에서 찾을 수 없습니다"라고 답변하세요
"""

    ANALYTICAL_PROMPT = """다음 질문에 대해 제공된 문서를 분석하여 답변하세요.
문서 기반 추론은 허용되지만, 추론 부분은 명확히 구분해주세요.

질문: {query}
{glossary_section}
참고 문서:
{context}
{graph_facts_section}
주의 - OCR 노이즈:
- 참고 문서는 PPT/PDF에서 OCR로 추출되어 깨진 문자가 포함될 수 있습니다
- 의미 없는 영문 조합(IEYV, OJnD, ITLL 등), 깨진 한글(극Y름, 관야 이이), 무작위 기호는 무시하세요
- 깨진 텍스트를 해석하거나 추측하지 말고, 의미가 명확한 부분만 활용하세요

핵심 원칙 (반드시 준수):
- 수치 충실도: 문서의 수치(%, 금액, 건수 등)는 반드시 문서에 적힌 그대로 인용하세요. 절대 다른 수치로 바꾸거나 방향(증가/감소)을 변환하지 마세요.
- 약어/고유명사: 문서에 정의되지 않은 약어의 뜻을 추측하지 마세요.
- 금지 행위: 문서에 없는 수치, 비율, 금액, 날짜를 절대 생성하지 마세요.

답변 형식:
- [문서 기반] 태그로 문서에서 직접 인용한 내용 표시 (구체적 수치/명령어 포함)
- [분석] 태그로 추론한 내용 표시
- 각 내용에 출처 번호 표시
- 용어집에 정의된 용어는 정확한 정의를 반영하여 답변
- 조직/시스템 정보가 있으면 담당자, 관계 정보를 우선 활용하여 답변
"""

    ADVISORY_PROMPT = """다음 질문에 대해 조언을 제공하세요.
제공된 문서와 일반적인 모범 사례를 참고하여 답변하세요.

질문: {query}
{glossary_section}
참고 문서:
{context}
{graph_facts_section}
주의 - OCR 노이즈:
- 참고 문서는 PPT/PDF에서 OCR로 추출되어 깨진 문자가 포함될 수 있습니다
- 의미 없는 영문 조합(IEYV, OJnD, ITLL 등), 깨진 한글(극Y름, 관야 이이), 무작위 기호는 무시하세요
- 깨진 텍스트를 해석하거나 추측하지 말고, 의미가 명확한 부분만 활용하세요

답변 형식:
- [문서 기반] 태그로 문서 참고 내용 표시
- [권장 사항] 태그로 일반적인 조언 표시
- 구체적인 실행 가능한 제안 포함
- 용어집에 정의된 용어는 정확한 정의를 반영하여 답변
- 조직/시스템 정보가 있으면 담당자, 관계 정보를 우선 활용하여 답변
"""

    CHITCHAT_PROMPT = """당신은 GS리테일의 사내 지식 검색 어시스턴트입니다.
사용자가 인사나 가벼운 대화를 했습니다. 친절하고 간결하게 응답하되,
지식 검색 기능을 안내해 주세요.

사용자 메시지: {query}

응답 규칙:
- 1-2문장으로 간결하게
- 친절한 톤
- 업무 관련 질문이 있으면 도움이 될 수 있다고 안내
"""

    def __init__(self, llm_client: ILLMClient) -> None:
        """Initialize tiered response generator.

        Args:
            llm_client: LLM client (e.g., OllamaClient)
        """
        self.llm = llm_client

    async def generate(
        self,
        query_type: QueryType,
        context: RAGContext,
    ) -> TieredResponse:
        """Generate response based on query type.

        Args:
            query_type: Query type
            context: RAG context

        Returns:
            Tiered response
        """
        if query_type == QueryType.CHITCHAT:
            return await self._generate_chitchat(context)
        elif query_type == QueryType.FACTUAL:
            return await self._generate_factual(context)
        elif query_type == QueryType.ANALYTICAL:
            return await self._generate_analytical(context)
        elif query_type == QueryType.ADVISORY:
            return await self._generate_advisory(context)
        else:
            # UNKNOWN - treat as FACTUAL by default
            return await self._generate_factual(context)

    async def _generate_chitchat(self, context: RAGContext) -> TieredResponse:
        """Generate greeting/small talk response (no KB search, direct LLM)."""
        prompt = self.CHITCHAT_PROMPT.format(query=context.query)
        content = await self.llm.generate(prompt)
        return TieredResponse(
            content=content,
            query_type=QueryType.CHITCHAT,
            source_type="general",
            citations=[],
            confidence=1.0,
            follow_up_suggestions=[],
        )

    async def _generate_factual(self, context: RAGContext) -> TieredResponse:
        """Generate fact-based response.

        No inference or general knowledge allowed.
        Returns "not found" if no relevant documents.
        """
        if not context.has_relevant_context(threshold=weights.response.factual_relevance_threshold):
            return TieredResponse(
                content=self.NO_DOCUMENT_TEMPLATE.format(query=context.query),
                query_type=QueryType.FACTUAL,
                source_type="document",
                citations=[],
                confidence=1.0,
                follow_up_suggestions=[
                    "다른 키워드로 검색해 보시겠습니까?",
                    "담당자에게 직접 문의하시겠습니까?",
                ],
            )

        prompt = self.FACTUAL_PROMPT.format(
            query=context.query,
            glossary_section=self._format_glossary_section(context),
            context=self._format_context(context),
            graph_facts_section=self._format_graph_facts_section(context),
        )

        try:
            response = await self.llm.generate(prompt)
            citations = self._extract_citations(response, context)

            return TieredResponse(
                content=response,
                query_type=QueryType.FACTUAL,
                source_type="document",
                citations=citations,
                confidence=self._calculate_confidence(citations, context),
            )
        except Exception as e:
            logger.error("Factual response generation failed: %s", e)
            return TieredResponse(
                content="응답 생성 중 오류가 발생했습니다.",
                query_type=QueryType.FACTUAL,
                source_type="document",
                citations=[],
                confidence=0.0,
                disclaimer=self.ERROR_DISCLAIMER,
            )

    async def _generate_analytical(self, context: RAGContext) -> TieredResponse:
        """Generate analysis-based response.

        Document-based inference allowed, clearly marked.
        """
        prompt = self.ANALYTICAL_PROMPT.format(
            query=context.query,
            glossary_section=self._format_glossary_section(context),
            context=self._format_context(context),
            graph_facts_section=self._format_graph_facts_section(context),
        )

        try:
            response = await self.llm.generate(prompt)
            citations = self._extract_citations(response, context)

            has_inference = "[분석]" in response or "[추론]" in response

            return TieredResponse(
                content=response,
                query_type=QueryType.ANALYTICAL,
                source_type="inference" if has_inference else "document",
                citations=citations,
                confidence=self._calculate_confidence(citations, context),
                disclaimer=self.INFERENCE_DISCLAIMER if has_inference else None,
            )
        except Exception as e:
            logger.error("Analytical response generation failed: %s", e)
            return TieredResponse(
                content="분석 응답 생성 중 오류가 발생했습니다.",
                query_type=QueryType.ANALYTICAL,
                source_type="document",
                citations=[],
                confidence=0.0,
                disclaimer=self.ERROR_DISCLAIMER,
            )

    async def _generate_advisory(self, context: RAGContext) -> TieredResponse:
        """Generate advisory response.

        Document + general knowledge allowed, clearly marked.
        """
        prompt = self.ADVISORY_PROMPT.format(
            query=context.query,
            glossary_section=self._format_glossary_section(context),
            context=self._format_context(context),
            graph_facts_section=self._format_graph_facts_section(context),
        )

        try:
            response = await self.llm.generate(prompt)
            citations = self._extract_citations(response, context)

            has_general = "[권장 사항]" in response or "[일반]" in response

            return TieredResponse(
                content=response,
                query_type=QueryType.ADVISORY,
                source_type="general" if has_general else "document",
                citations=citations,
                confidence=self._calculate_confidence(citations, context),
                disclaimer=self.GENERAL_KNOWLEDGE_DISCLAIMER if has_general else None,
                follow_up_suggestions=[
                    "추가 질문이 있으시면 말씀해 주세요.",
                    "구체적인 상황을 알려주시면 더 맞춤 조언이 가능합니다.",
                ],
            )
        except Exception as e:
            logger.error("Advisory response generation failed: %s", e)
            return TieredResponse(
                content="조언 응답 생성 중 오류가 발생했습니다.",
                query_type=QueryType.ADVISORY,
                source_type="document",
                citations=[],
                confidence=0.0,
                disclaimer=self.ERROR_DISCLAIMER,
            )

    @staticmethod
    def _format_glossary_section(context: RAGContext) -> str:
        """Format glossary definitions section (empty string if none)."""
        if not context.glossary_definitions:
            return ""
        lines = ["\n관련 용어집 정의:"]
        for entry in context.glossary_definitions[:10]:
            term = entry.get("term", "")
            term_ko = entry.get("term_ko", "")
            definition = entry.get("definition", "")
            label = f"{term} ({term_ko})" if term_ko else term
            lines.append(f"- {label}: {definition}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_graph_facts_section(context: RAGContext) -> str:
        """Format graph facts section (empty string if none)."""
        if not context.graph_facts:
            return ""
        lines = ["\n관련 조직/시스템 정보 (지식 그래프):"]
        for fact in context.graph_facts[:10]:
            lines.append(f"- {fact}")
        return "\n".join(lines) + "\n"

    def _format_context(self, context: RAGContext) -> str:
        """Format context with metadata."""
        formatted = []
        for i, (chunk, source) in enumerate(
            zip(
                context.retrieved_chunks,
                context.chunk_sources or [{}] * len(context.retrieved_chunks),
                strict=False,
            ),
            1,
        ):
            source_info = source.get("document_name", f"문서 {i}")
            metadata = source.get("metadata") or {}

            meta_parts: list[str] = []
            creator = metadata.get("creator_name")
            if creator:
                meta_parts.append(f"작성자: {creator}")
            creator_email = metadata.get("creator_email")
            if creator_email:
                meta_parts.append(f"이메일: {creator_email}")
            creator_team = metadata.get("creator_team")
            if creator_team:
                meta_parts.append(f"팀: {creator_team}")

            meta_line = f" ({', '.join(meta_parts)})" if meta_parts else ""
            formatted.append(f"[{i}] {source_info}{meta_line}:\n{chunk}\n")
        return "\n".join(formatted)

    def _extract_citations(self, response: str, context: RAGContext) -> list[dict]:
        """Extract citations from response."""
        citations: list[dict] = []
        raw_refs = {int(ref) for ref in re.findall(r"\[(\d+)\]", response)}
        if not raw_refs:
            return citations

        normalized_sources: list[dict] = []
        for index, source in enumerate(context.chunk_sources, start=1):
            metadata = source.get("metadata") or {}
            default_score = (
                context.relevance_scores[index - 1]
                if index - 1 < len(context.relevance_scores)
                else None
            )
            source_uri = (
                source.get("source_uri")
                or source.get("url")
                or metadata.get("source_uri")
                or metadata.get("url")
            )
            normalized_sources.append(
                {
                    "ref": str(index),
                    "document_name": source.get("document_name"),
                    "kb_name": source.get("kb_name") or metadata.get("kb_name"),
                    "source_uri": source_uri,
                    "url": source_uri,
                    "score": source.get("score")
                    if source.get("score") is not None
                    else default_score,
                }
            )

        entries = CitationFormatter.from_sources(normalized_sources)
        entry_map = {entry.ref: entry for entry in entries}

        for ref in sorted(raw_refs):
            source_index = ref - 1
            if source_index < 0 or source_index >= len(context.chunk_sources):
                continue
            source = context.chunk_sources[source_index]
            metadata = source.get("metadata") or {}
            entry = entry_map.get(str(ref))
            citations.append(
                {
                    "ref": str(ref),
                    "document_id": source.get("document_id"),
                    "document_name": (
                        entry.document_name
                        if entry is not None
                        else source.get("document_name")
                    ),
                    "chunk_id": source.get("chunk_id"),
                    "url": entry.source_uri if entry is not None else source.get("url"),
                    "source_uri": (
                        entry.source_uri
                        if entry is not None
                        else source.get("source_uri") or source.get("url")
                    ),
                    "kb_name": entry.kb_name if entry is not None else source.get("kb_name"),
                    "relevance_score": (
                        entry.relevance_score
                        if entry is not None
                        else source.get("score")
                    ),
                    "is_stale": bool(metadata.get("is_stale", False)),
                    "freshness_warning": metadata.get("freshness_warning"),
                    "days_since_update": metadata.get("days_since_update"),
                }
            )

        return citations

    def _calculate_confidence(self, citations: list[dict], context: RAGContext) -> float:
        """Calculate response confidence."""
        if not citations:
            return 0.5

        citation_score = min(1.0, len(citations) / 3)

        if context.relevance_scores:
            relevance_score = sum(context.relevance_scores) / len(context.relevance_scores)
        else:
            relevance_score = 0.7

        return (citation_score + relevance_score) / 2


class NoOpTieredResponseGenerator:
    """TieredResponseGenerator NoOp implementation (for testing/development)."""

    async def generate(
        self,
        query_type: QueryType,
        context: RAGContext,
    ) -> TieredResponse:
        return TieredResponse(
            content=f"[NoOp] {query_type.value} 유형에 대한 테스트 응답입니다.",
            query_type=query_type,
            source_type="document",
            citations=[],
            confidence=1.0,
            disclaimer="[NoOp] 테스트 응답입니다.",
        )


__all__ = [
    "ILLMClient",
    "NoOpTieredResponseGenerator",
    "RAGContext",
    "TieredResponse",
    "TieredResponseGenerator",
]
