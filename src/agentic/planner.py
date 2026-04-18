"""Korean query planner — KiwiPy morpheme 분석 + Query type 기반 tiered planning.

차별화 #1 (Korean NLP):
- 고유명사 (NNP) 감지 → graph_query 우선 힌트
- 시점 표현 감지 → time_resolver 우선 힌트

차별화 #2 (GraphRAG routing):
- 엔티티 다수 감지 시 graph_query 첫 단계 강제 힌트

차별화 #4 (Tiered planning):
- QueryClassifier 활용 — CHITCHAT 은 RAG skip
- FACTUAL → complexity≤2 (1-2 step)
- ANALYTICAL/MULTI_HOP/COMPARATIVE → 최대 5 step
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace

from src.agentic.protocols import AgentLLM, Plan
from src.agentic.tools import ToolRegistry
from src.search.query_classifier import QueryClassifier, QueryType

logger = logging.getLogger(__name__)


_TIME_KEYWORDS = (
    "차주", "다음 주", "이번 주", "금주", "지난 주", "전주",
    "이번 달", "지난 달", "다음 달", "어제", "오늘", "내일", "그제", "모레",
)
_TIME_NUM_RE = re.compile(r"\d+\s*[일주달]\s*전")


@dataclass(frozen=True)
class QueryEnrichment:
    """KiwiPy 분석 결과 — LLM plan 입력에 첨부."""

    entities: list[str] = field(default_factory=list)         # 고유명사 NNP
    keywords: list[str] = field(default_factory=list)         # 일반명사 + 외국어
    has_time_reference: bool = False                          # 시점 표현 감지
    detected_time_phrases: list[str] = field(default_factory=list)


def _enrich_query(query: str) -> QueryEnrichment:
    """KiwiPy 로 query 분석. 실패 시 빈 enrichment 반환."""
    try:
        from src.nlp.korean.morpheme_analyzer import KoreanMorphemeAnalyzer
        analyzer = KoreanMorphemeAnalyzer()
        result = analyzer.analyze(query)
    except (ImportError, RuntimeError, OSError, ValueError, TypeError, AttributeError) as e:
        logger.debug("KiwiPy enrichment skipped: %s", e)
        return QueryEnrichment()

    entities: list[str] = []
    keywords: list[str] = []
    for tok in result.tokens:
        tag = getattr(tok, "tag", "")
        form = getattr(tok, "form", "")
        if not form:
            continue
        if tag == "NNP":
            entities.append(form)
        if tag in ("NNG", "NNP", "SL", "SH"):
            keywords.append(form)

    detected_phrases = [kw for kw in _TIME_KEYWORDS if kw in query]
    nrm = _TIME_NUM_RE.search(query)
    if nrm:
        detected_phrases.append(nrm.group(0))

    return QueryEnrichment(
        entities=list(dict.fromkeys(entities))[:10],     # dedup, cap 10
        keywords=list(dict.fromkeys(keywords))[:20],
        has_time_reference=bool(detected_phrases),
        detected_time_phrases=detected_phrases,
    )


def _build_planner_context(
    enrichment: QueryEnrichment, query_type: QueryType | None = None,
) -> str:
    """Korean NLP + query type 기반 LLM plan 컨텍스트 생성.

    query_type 까지 전달하면 tiered planning 가이드 추가.
    """
    lines: list[str] = []
    if enrichment.entities or enrichment.has_time_reference or enrichment.keywords:
        lines.append("[Korean NLP 분석]")
        if enrichment.entities:
            lines.append(
                f"- 감지된 고유명사 (graph_query 우선): {', '.join(enrichment.entities)}",
            )
        if enrichment.has_time_reference:
            phrases = ", ".join(enrichment.detected_time_phrases) or "감지됨"
            lines.append(f"- 시점 표현 ({phrases}) → time_resolver 권장")
        if enrichment.keywords and not enrichment.entities:
            lines.append(
                f"- 키워드 (qdrant_search 후보): {', '.join(enrichment.keywords[:10])}",
            )

    if query_type:
        lines.append("[Query type 기반 plan 가이드]")
        if query_type == QueryType.FACTUAL:
            lines.append(
                "- 단순 사실 질문 — estimated_complexity 1-2, steps 1-2개 권장",
            )
        elif query_type in (QueryType.ANALYTICAL, QueryType.ADVISORY):
            lines.append(
                "- 분석/조언 질문 — 추론 필요, estimated_complexity 3-4, "
                "관련 chunk 충분히 수집 후 synthesize",
            )
        elif query_type in (QueryType.MULTI_HOP, QueryType.COMPARATIVE):
            lines.append(
                "- 다단계/비교 질문 — estimated_complexity 4-5, "
                "여러 sub_query 분할 + graph_query 활용 권장",
            )
        elif query_type == QueryType.CHITCHAT:
            lines.append("- 인사/잡담 — RAG skip (steps 비워둘 것)")

    return "\n".join(lines)


class KoreanQueryPlanner:
    """LLM AgentLLM wrap — Korean enrichment + Query classifier 자동 첨부.

    Agent loop 가 ``await planner.make_plan(query)`` 만 부르면 됨.
    """

    def __init__(self, llm: AgentLLM, registry: ToolRegistry) -> None:
        self._llm = llm
        self._registry = registry
        self._classifier = QueryClassifier()

    async def make_plan(self, query: str, *, extra_context: str = "") -> Plan:
        enrichment = _enrich_query(query)
        classification = self._classifier.classify(query)
        context = _build_planner_context(enrichment, classification.query_type)
        if extra_context:
            context = f"{context}\n\n{extra_context}" if context else extra_context

        plan = await self._llm.plan(
            query, self._registry.specs(), context=context,
        )

        # Tiered post-processing: CHITCHAT 은 LLM 이 plan 채워도 강제 비움 (RAG skip)
        if classification.query_type == QueryType.CHITCHAT and plan.steps:
            plan = replace(plan, steps=[], estimated_complexity=1, sub_queries=[query])
        return plan

    def classify_query(self, query: str) -> QueryType:
        """공개 helper — agent loop 가 CHITCHAT 일 때 RAG skip 결정."""
        return self._classifier.classify(query).query_type

    @staticmethod
    def enrich(query: str) -> QueryEnrichment:
        """공개 helper — 외부 (예: Streamlit Trace viz) 도 사용."""
        return _enrich_query(query)
