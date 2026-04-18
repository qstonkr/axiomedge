"""Korean query planner — KiwiPy morpheme 분석으로 LLM plan 강화.

차별화 #1: 시중 영문 Agentic 와 달리 한국어 NLP 전처리로 LLM 의 plan 정확도 향상.
- 고유명사 (NNP) 감지 → graph_query 우선 힌트
- 시점 표현 감지 → time_resolver 우선 힌트
- 도메인 용어 (glossary 후보) 추출
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.agentic.protocols import AgentLLM, Plan
from src.agentic.tools import ToolRegistry

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


def _build_planner_context(enrichment: QueryEnrichment) -> str:
    """KiwiPy 분석 결과를 LLM plan prompt 의 [추가 컨텍스트] 로 변환."""
    if not (enrichment.entities or enrichment.has_time_reference or enrichment.keywords):
        return ""
    lines: list[str] = ["[Korean NLP 분석]"]
    if enrichment.entities:
        lines.append(
            f"- 감지된 고유명사 (graph_query 후보): {', '.join(enrichment.entities)}",
        )
    if enrichment.has_time_reference:
        phrases = ", ".join(enrichment.detected_time_phrases) or "감지됨"
        lines.append(f"- 시점 표현 ({phrases}) → time_resolver 권장")
    if enrichment.keywords and not enrichment.entities:
        lines.append(
            f"- 키워드 (qdrant_search 후보): {', '.join(enrichment.keywords[:10])}",
        )
    return "\n".join(lines)


class KoreanQueryPlanner:
    """LLM AgentLLM 을 wrap — Korean enrichment 자동 첨부.

    Agent loop 가 ``await planner.make_plan(query)`` 만 부르면 됨.
    """

    def __init__(self, llm: AgentLLM, registry: ToolRegistry) -> None:
        self._llm = llm
        self._registry = registry

    async def make_plan(self, query: str, *, extra_context: str = "") -> Plan:
        enrichment = _enrich_query(query)
        context = _build_planner_context(enrichment)
        if extra_context:
            context = f"{context}\n\n{extra_context}" if context else extra_context
        return await self._llm.plan(
            query, self._registry.specs(), context=context,
        )

    @staticmethod
    def enrich(query: str) -> QueryEnrichment:
        """공개 helper — 외부 (예: Streamlit Trace viz) 도 사용."""
        return _enrich_query(query)
