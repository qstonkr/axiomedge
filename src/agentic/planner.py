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
from typing import Any

import uuid

from src.agentic.protocols import AgentLLM, AgentStep, Plan
from src.agentic.tools import ToolRegistry
from src.search.query_classifier import QueryClassifier, QueryType

logger = logging.getLogger(__name__)

# LLM context 에 KB 목록을 토큰 효율적으로 노출하기 위한 상한.
# 너무 많으면 prompt 가 비대해지고 plan 품질이 오히려 떨어짐.
_PLANNER_KB_CONTEXT_LIMIT = 30


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


def _kb_query_match_score(
    kb: dict[str, Any], query: str, enrichment: QueryEnrichment,
) -> int:
    """KB 의 id/name/description 과 query (+ Korean NLP keywords) 의 어휘 매칭 점수.

    문서 수와 무관하게, query 키워드가 KB 메타데이터에 등장하면 가산:
      - name/description 매칭: +3점 (가장 강함 — 사람이 KB 이름을 그렇게 지은 이유)
      - kb_id 매칭: +2점 (예: query 'ESPA' ↔ kb_id 'g-espa')
      - 한글 명사 부분 일치도 동일 가산 (ESPA ↔ espa case-insensitive)

    LLM 이 "문서 수가 많은 KB" 만 고르는 편향을 방지하기 위함.
    """
    haystack_name_desc = (
        (kb.get("name") or "") + " " + (kb.get("description") or "")
    ).lower()
    haystack_id = (kb.get("kb_id") or kb.get("id") or "").lower()
    # query 본문 + Korean NLP 분해 키워드 모두 사용
    candidates: list[str] = []
    q = query.strip().lower()
    if q:
        candidates.append(q)
    candidates.extend(s.lower() for s in enrichment.entities)
    candidates.extend(s.lower() for s in enrichment.keywords)
    # query 자체를 공백/구분자로 split — "수지우남점 ESPA 활동" 같은 multi-token query
    for tok in re.split(r"[\s,()\[\]\.\?\!\-/]+", q):
        tok = tok.strip()
        if len(tok) >= 2:  # 1글자는 noise (조사/접속사) 무시
            candidates.append(tok)
    seen: set[str] = set()
    score = 0
    for kw in candidates:
        if not kw or kw in seen:
            continue
        seen.add(kw)
        if kw in haystack_name_desc:
            score += 3
        if kw in haystack_id:
            score += 2
    return score


def _build_planner_context(
    enrichment: QueryEnrichment,
    query_type: QueryType | None = None,
    kb_summary: list[dict[str, Any]] | None = None,
    query: str = "",
) -> str:
    """Korean NLP + query type 기반 LLM plan 컨텍스트 생성.

    query_type 까지 전달하면 tiered planning 가이드 추가.
    kb_summary 가 있으면 LLM 이 적절한 ``qdrant_search.kb_ids`` 를 채울 수 있도록
    가용 KB 목록 + 메타 (id/name/desc/문서수) 를 포함. query 가 함께 전달되면
    KB 목록을 query↔KB-name 매칭 점수로 정렬하고 ★ 마크 + 명시적 우선 가이드.
    """
    lines: list[str] = []
    if enrichment.entities or enrichment.has_time_reference or enrichment.keywords:
        lines.append("[Korean NLP 분석]")
        if enrichment.entities:
            # 고유명사가 있으면 graph_query 가 도움이 되지만, graph index 가 비어
            # 있는 KB 도 많아서 graph_query 만 쓰면 0 chunks 로 끝남. 항상
            # qdrant_search 를 함께 사용해 vector 결과로 fallback 가능하게.
            lines.append(
                f"- 감지된 고유명사: {', '.join(enrichment.entities)} "
                "→ graph_query 와 qdrant_search 를 **둘 다** plan 에 포함할 것 "
                "(graph 0건이어도 vector 결과로 답변 가능)",
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

    if kb_summary:
        # query↔KB 매칭 점수로 정렬 — LLM 이 "문서 수 많은 KB" 만 고르지 않도록
        # 의미적으로 가장 가까운 KB 가 목록 맨 위에 오게 한다. 동점이면 문서 수.
        scored = [
            (_kb_query_match_score(kb, query, enrichment), kb) for kb in kb_summary
        ]
        scored.sort(
            key=lambda pair: (-pair[0], -(pair[1].get("document_count") or 0)),
        )
        any_match = any(score > 0 for score, _ in scored)
        intro = (
            "[가용 KB 목록 — qdrant_search 의 kb_ids 인자에 적절한 id 를 채울 것. "
            "비우면 모든 KB fan-out 되어 정확도가 떨어짐. "
            "**아래 목록의 kb_id 를 한 글자도 바꾸지 말고 정확히 그대로 복사**할 것 "
            "(예: 'g-espa' 를 'espa' 로 줄이거나, '홈쇼핑AX' 를 'home_shopping_AX' 로 "
            "변환하지 말 것 — 존재하지 않는 KB 가 됨). "
        )
        if any_match:
            intro += (
                "★ 표시는 query 의 키워드가 KB 이름/설명에 등장한 것 — "
                "**문서 수보다 query 매칭이 우선**. ★ KB 부터 검토해서 채울 것.]"
            )
        else:
            intro += "현재 query 와 직접 매칭되는 KB 이름이 없음 — 의미상 가장 가까운 KB 선택.]"
        lines.append(intro)
        for score, kb in scored[:_PLANNER_KB_CONTEXT_LIMIT]:
            kb_id = kb.get("kb_id") or kb.get("id") or "?"
            name = (kb.get("name") or "").strip() or "(이름 없음)"
            tier = kb.get("tier") or ""
            doc_count = kb.get("document_count") or 0
            desc = (kb.get("description") or "").strip()
            desc_part = f" — {desc[:80]}" if desc else ""
            tier_part = f" [{tier}]" if tier else ""
            mark = "★ " if score > 0 else ""
            lines.append(
                f"- {mark}{kb_id}{tier_part}: {name} ({doc_count} docs){desc_part}",
            )
        if len(kb_summary) > _PLANNER_KB_CONTEXT_LIMIT:
            lines.append(
                f"- ... 외 {len(kb_summary) - _PLANNER_KB_CONTEXT_LIMIT}개 KB 생략 "
                "(필요 시 kb_list tool 로 전체 조회)",
            )

    return "\n".join(lines)


async def _summarize_kbs(state: dict[str, Any]) -> list[dict[str, Any]]:
    """state["kb_registry"] 에서 active KB 목록을 slim 하게 추출.

    실패 / 누락 시 빈 list 반환 — context 에서 KB 섹션이 그냥 빠짐 (안전 fallback).
    org / personal-KB owner 필터는 kb_lister 와 동일한 규칙.
    """
    kb_registry = state.get("kb_registry")
    if kb_registry is None:
        return []
    organization_id = state.get("organization_id")
    current_user_id = state.get("current_user_id")
    try:
        kbs = await kb_registry.list_all(organization_id=organization_id)
    except Exception as e:  # noqa: BLE001 — 실패해도 plan 진행
        logger.warning("planner KB summary skipped — list_all failed: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for k in kbs:
        if k.get("status") != "active":
            continue
        if k.get("tier") == "personal" and k.get("owner_id") != current_user_id:
            continue
        kb_id = k.get("id") or k.get("kb_id")
        if not kb_id:
            continue
        out.append({
            "kb_id": kb_id,
            "name": k.get("name") or "",
            "description": k.get("description") or "",
            "tier": k.get("tier") or "",
            "document_count": k.get("document_count") or 0,
        })
    # global → team → personal 순으로 정렬해 prompt 안에서 일관된 시퀀스 유지
    tier_order = {"global": 0, "team": 1, "personal": 2}
    out.sort(key=lambda kb: (tier_order.get(kb.get("tier", ""), 9), kb["kb_id"]))
    return out


class KoreanQueryPlanner:
    """LLM AgentLLM wrap — Korean enrichment + Query classifier 자동 첨부.

    Agent loop 가 ``await planner.make_plan(query)`` 만 부르면 됨.
    """

    def __init__(self, llm: AgentLLM, registry: ToolRegistry) -> None:
        self._llm = llm
        self._registry = registry
        self._classifier = QueryClassifier()

    async def make_plan(
        self,
        query: str,
        *,
        extra_context: str = "",
        state: dict[str, Any] | None = None,
    ) -> Plan:
        enrichment = _enrich_query(query)
        classification = self._classifier.classify(query)
        kb_summary = await _summarize_kbs(state) if state else []
        context = _build_planner_context(
            enrichment,
            classification.query_type,
            kb_summary=kb_summary,
            query=query,
        )
        if extra_context:
            context = f"{context}\n\n{extra_context}" if context else extra_context

        plan = await self._llm.plan(
            query, self._registry.specs(), context=context,
        )

        # Tiered post-processing: CHITCHAT 은 LLM 이 plan 채워도 강제 비움 (RAG skip)
        if classification.query_type == QueryType.CHITCHAT and plan.steps:
            plan = replace(plan, steps=[], estimated_complexity=1, sub_queries=[query])
            return plan

        # Vector-search safety net: 작은 LLM 이 entity 감지 시 graph_query 만
        # plan 에 넣고 qdrant_search 를 빠뜨리는 케이스가 잦다 (graph index 가
        # 비어 있는 KB 면 0 chunks → 답변 못 함). 코드로 강제 보강.
        if plan.steps and "qdrant_search" in self._registry.names():
            tools_in_plan = {s.tool for s in plan.steps}
            if "qdrant_search" not in tools_in_plan:
                # 다른 step (예: graph_query) 의 kb_ids 를 재활용 — 이미 LLM 이
                # 적합한 KB 를 골랐다는 신호.
                inherited_kb_ids: list[str] = []
                for s in plan.steps:
                    candidate = s.args.get("kb_ids") if isinstance(s.args, dict) else None
                    if isinstance(candidate, list) and candidate:
                        inherited_kb_ids = list(candidate)
                        break
                qdrant_step = AgentStep(
                    step_id=f"auto-qdrant-{uuid.uuid4().hex[:8]}",
                    plan_index=len(plan.steps),
                    tool="qdrant_search",
                    args={
                        "query": query,
                        "kb_ids": inherited_kb_ids,
                        "top_k": 5,
                    },
                    rationale=(
                        "auto-added by planner — LLM 이 qdrant_search 를 빠뜨려 "
                        "vector retrieval 안전망으로 강제 추가"
                    ),
                )
                plan = replace(plan, steps=[*plan.steps, qdrant_step])
        return plan

    def classify_query(self, query: str) -> QueryType:
        """공개 helper — agent loop 가 CHITCHAT 일 때 RAG skip 결정."""
        return self._classifier.classify(query).query_type

    @staticmethod
    def enrich(query: str) -> QueryEnrichment:
        """공개 helper — 외부 (예: Streamlit Trace viz) 도 사용."""
        return _enrich_query(query)
