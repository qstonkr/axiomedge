"""Edge ↔ HQ LLM 라우팅 — 차별화 #3.

Plan 의 estimated_complexity 기준으로 synthesize 를 edge / HQ 분기:
- 단순 (1-2): edge LLM (sub-second, 비용 ~0)
- 복잡 (3-5): HQ LLM (multi-step reasoning)

plan / reflect 는 항상 HQ (작은 edge 모델은 JSON 구조 신뢰성 낮음).

env:
  AGENTIC_EDGE_URL — 설정되면 routing 활성, 아니면 HQ-only
  AGENTIC_EDGE_COMPLEXITY_THRESHOLD — edge 사용 max complexity (default 2)
"""

from __future__ import annotations

import logging
import os

from src.agentic.protocols import (
    AgentLLM,
    Critique,
    Plan,
    ToolResult,
    ToolSpec,
)

logger = logging.getLogger(__name__)


class RoutingAgentLLM:
    """HQ LLM 을 baseline 으로 두고, synthesize 만 complexity 따라 edge 로 routing.

    plan / reflect 는 HQ 우선 — JSON 구조 응답 신뢰성 위해.
    AgentLLM Protocol 만족.
    """

    def __init__(
        self, hq: AgentLLM, edge: AgentLLM | None = None,
        complexity_threshold: int | None = None,
    ) -> None:
        self._hq = hq
        self._edge = edge
        self._threshold = complexity_threshold or int(
            os.getenv("AGENTIC_EDGE_COMPLEXITY_THRESHOLD", "2"),
        )
        # plan/reflect 호출 시 가장 최근 plan 의 complexity 기록 (synthesize routing 결정 용)
        self._last_complexity: int = 5  # default = HQ

    @property
    def provider_name(self) -> str:
        # Routing 모드 표시 — 실 호출은 hq 또는 edge
        if self._edge is None:
            return self._hq.provider_name
        return f"routing(hq={self._hq.provider_name},edge={self._edge.provider_name})"

    async def plan(
        self, query: str, available_tools: list[ToolSpec], context: str = "",
    ) -> Plan:
        plan = await self._hq.plan(query, available_tools, context)
        # 다음 synthesize 가 routing 결정에 사용
        self._last_complexity = plan.estimated_complexity
        return plan

    async def reflect(
        self, query: str, evidence: list[ToolResult], answer: str | None,
    ) -> Critique:
        # reflect 는 작은 edge LLM 으론 신뢰성 부족 — 항상 HQ
        return await self._hq.reflect(query, evidence, answer)

    async def synthesize(self, query: str, evidence: list[ToolResult]) -> str:
        # complexity 기준 edge / HQ 분기
        if self._edge is not None and self._last_complexity <= self._threshold:
            try:
                logger.info(
                    "routing.synthesize: edge (complexity=%d ≤ %d)",
                    self._last_complexity, self._threshold,
                )
                return await self._edge.synthesize(query, evidence)
            except Exception as e:  # noqa: BLE001 — fallback to HQ on edge failure
                logger.warning("edge synthesize failed (%s) — fallback HQ", e)
        return await self._hq.synthesize(query, evidence)


def maybe_wrap_with_routing(hq: AgentLLM) -> AgentLLM:
    """env 가 routing 활성 indicate 하면 RoutingAgentLLM 으로 wrap, 아니면 hq 그대로.

    AGENTIC_EDGE_URL 이 set 되어 있고 EdgeAgentLLM init 가능하면 routing 활성.
    """
    edge_url = os.getenv("AGENTIC_EDGE_URL", "").strip()
    if not edge_url:
        return hq
    try:
        from src.agentic.llm.edge import EdgeAgentLLM
        edge_llm = EdgeAgentLLM()
    except (RuntimeError, OSError, ValueError) as e:
        logger.warning("edge routing unavailable (%s) — HQ only", e)
        return hq
    return RoutingAgentLLM(hq=hq, edge=edge_llm)
