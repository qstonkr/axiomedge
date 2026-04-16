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
from src.llm.prompt_safety import safe_user_input
from .query_classifier import QueryType
from .citation_formatter import CitationFormatter

logger = logging.getLogger(__name__)

# Pre-compiled regex for citation extraction
_CITATION_REF_PATTERN = re.compile(r"\[(\d+)\]")


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
- 단, 테이블/표 형식의 데이터(숫자, 날짜, 금액, 점포명 등)는 형식이 깨져 있어도 문맥상 의미가 파악되면 적극적으로 추출하여 답변에 활용하세요
- OCR 특성상 숫자 사이 공백, 콤마 누락, 열 정렬 깨짐이 빈번합니다. 주변 컨텍스트(열 제목, 행 레이블)를 참고하여 올바른 값을 매핑하세요

핵심 원칙 (반드시 준수):
- 수치 충실도: 문서에 "9.3% 신장"이라고 적혀 있으면 반드시 "9.3% 신장"이라고 답변하세요. 절대로 다른 수치로 바꾸거나 "감소"로 변환하지 마세요.
- 약어/고유명사: 문서에 정의되지 않은 약어(예: ESPA)의 뜻을 추측하지 마세요. 문서에 설명이 없으면 약어 그대로 사용하세요.
- 금지 행위: 문서에 없는 수치, 비율, 금액, 날짜를 창작하지 마세요. 단, 문서에 수치가 있는데 형식이 깨져 있는 경우는 추출하여 답변하세요.

답변 규칙:
- 제공된 문서 전체를 꼼꼼히 읽고, 질문 키워드와 관련된 정보를 빠짐없이 추출하세요
- 문서에 관련 내용이 조금이라도 있으면 반드시 해당 내용을 인용하여 답변하세요
- "정보가 부족합니다", "확인되지 않았습니다", "명시적으로 기재되어 있지 않습니다" 같은 회피 표현을 사용하지 마세요
- 확신을 가지고 단호하게 서술하세요 ("~입니다", "~합니다")
- 문서에 포함된 구체적 수치, 명령어, 기술 용어, 담당자 정보는 문서에 적힌 그대로 포함하세요
- 각 사실에 출처 번호 [1], [2] 등으로 표시하세요
- 용어집에 정의된 용어는 정확한 정의를 반영하여 답변하세요
- 조직/시스템 정보가 있으면 담당자, 관계 정보를 우선 활용하여 답변하세요
- 문서에 없는 내용을 지어내지 마세요
- 질문의 핵심 키워드와 문서 주제가 다른 경우 (예: "폐기" 질문에 "폐점" 문서), 차이를 명확히 고지한 후 관련 정보를 안내하세요
- "정보를 찾을 수 없습니다"는 정말로 모든 문서에 관련 키워드가 단 하나도 없을 때만 사용하세요
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
- 문서에 관련 내용이 조금이라도 있으면 반드시 인용하세요
- "정보가 부족합니다" 같은 회피 표현 대신 확인 가능한 사실을 중심으로 답변하세요
- 질문 키워드와 문서 주제가 다르면 차이를 고지 후 관련 정보를 안내하세요
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
        except Exception as e:  # noqa: BLE001
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
        except Exception as e:  # noqa: BLE001
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
        except Exception as e:  # noqa: BLE001
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
        """Format context with metadata.

        **Prompt injection 방어**:
        - 각 chunk 는 ``<chunk>`` 태그로 delimit + instruction 키워드 중화
        - metadata (작성자/이메일/팀) 도 사용자 문서에서 올 수 있으므로 중화
        - 인덱스/source name 은 시스템이 부여하므로 그대로 두지만 Markdown 특수
          문자는 그대로 — LLM 이 citation 으로 인식하므로 필요

        악성 문서가 chunk 내용에 ``</answer>`` 같은 태그나 ``이전 지시 무시`` 같은
        instruction 을 심어도 LLM 이 regeneration 규칙을 우회하지 못하도록 한다.
        """
        formatted = []
        sources = context.chunk_sources if context.chunk_sources else ()
        for i, chunk in enumerate(context.retrieved_chunks, 1):
            source = sources[i - 1] if i - 1 < len(sources) else {}
            source_info = source.get("document_name", f"문서 {i}")
            metadata = source.get("metadata") or {}

            meta_parts: list[str] = []
            creator = metadata.get("creator_name")
            if creator:
                meta_parts.append(f"작성자: {safe_user_input('meta', creator, max_len=100)}")
            creator_email = metadata.get("creator_email")
            if creator_email:
                meta_parts.append(f"이메일: {safe_user_input('meta', creator_email, max_len=100)}")
            creator_team = metadata.get("creator_team")
            if creator_team:
                meta_parts.append(f"팀: {safe_user_input('meta', creator_team, max_len=100)}")

            meta_line = f" ({', '.join(meta_parts)})" if meta_parts else ""
            chunk_block = safe_user_input("chunk", chunk, max_len=4000)
            formatted.append(f"[{i}] {source_info}{meta_line}:\n{chunk_block}\n")
        return "\n".join(formatted)

    def _normalize_sources(self, context: RAGContext) -> list[dict]:
        """Normalize chunk_sources into a uniform list for citation formatting."""
        normalized: list[dict] = []
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
            normalized.append({
                "ref": str(index),
                "document_name": source.get("document_name"),
                "kb_name": source.get("kb_name") or metadata.get("kb_name"),
                "source_uri": source_uri,
                "url": source_uri,
                "score": source.get("score")
                if source.get("score") is not None
                else default_score,
            })
        return normalized

    @staticmethod
    def _build_citation(ref: int, source: dict, entry: Any) -> dict:
        """Build a single citation dict from source and optional CitationEntry."""
        metadata = source.get("metadata") or {}
        has_entry = entry is not None
        return {
            "ref": str(ref),
            "document_id": source.get("document_id"),
            "document_name": entry.document_name if has_entry else source.get("document_name"),
            "chunk_id": source.get("chunk_id"),
            "url": entry.source_uri if has_entry else source.get("url"),
            "source_uri": (
                entry.source_uri if has_entry
                else source.get("source_uri") or source.get("url")
            ),
            "kb_name": entry.kb_name if has_entry else source.get("kb_name"),
            "relevance_score": entry.relevance_score if has_entry else source.get("score"),
            "is_stale": bool(metadata.get("is_stale", False)),
            "freshness_warning": metadata.get("freshness_warning"),
            "days_since_update": metadata.get("days_since_update"),
        }

    def _extract_citations(self, response: str, context: RAGContext) -> list[dict]:
        """Extract citations from response."""
        raw_refs = {int(ref) for ref in _CITATION_REF_PATTERN.findall(response)}
        if not raw_refs:
            return []

        normalized_sources = self._normalize_sources(context)
        entries = CitationFormatter.from_sources(normalized_sources)
        entry_map = {entry.ref: entry for entry in entries}

        citations: list[dict] = []
        for ref in sorted(raw_refs):
            source_index = ref - 1
            if source_index < 0 or source_index >= len(context.chunk_sources):
                continue
            source = context.chunk_sources[source_index]
            entry = entry_map.get(str(ref))
            citations.append(self._build_citation(ref, source, entry))

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
        import asyncio
        await asyncio.sleep(0)
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
