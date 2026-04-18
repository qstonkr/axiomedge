"""Agent loop — plan → execute tools → synthesize → reflect → (retry).

이번 단계 (Day 5) 는 single-iteration baseline.
Day 7 에서 reflection-driven retry loop 추가.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import replace

from src.agentic.cost_guard import CostGuard, CostGuardConfig
from src.agentic.llm import create_agent_llm
from src.agentic.planner import KoreanQueryPlanner
from src.agentic.protocols import (
    AgentLLM,
    AgentStep,
    AgentTrace,
    Critique,
    ToolResult,
)
from src.agentic.tools import ToolRegistry, build_default_registry

logger = logging.getLogger(__name__)


class Agent:
    """Agentic RAG 의 단일 entry point.

    LLM + tool registry + cost guard 를 캡슐화. ``run(query, state)`` 호출만으로
    plan → execute → synthesize → reflect 한 사이클 완료.

    DI:
        llm: AgentLLM (env-driven 으로 자동 생성 가능)
        registry: 5개 기본 tool (custom tool 추가 가능)
        cost_guard: 한도 (env 로 조정)
    """

    def __init__(
        self,
        llm: AgentLLM | None = None,
        registry: ToolRegistry | None = None,
        cost_guard_config: CostGuardConfig | None = None,
    ) -> None:
        self._llm = llm or create_agent_llm()
        self._registry = registry or build_default_registry()
        self._planner = KoreanQueryPlanner(self._llm, self._registry)
        self._cost_guard_config = cost_guard_config

    async def run(self, query: str, state: dict) -> AgentTrace:
        """단일 iteration 실행.

        state: FastAPI app state — qdrant_search/graph_repo/glossary/embedder/kb_registry 가용.
        """
        guard = CostGuard(self._cost_guard_config)
        trace_id = str(uuid.uuid4())

        # 1) Plan
        plan = await self._planner.make_plan(query)
        guard.begin_iteration()

        # 2) Execute steps (한도 내)
        executed_steps: list[AgentStep] = []
        results: list[ToolResult] = []
        for step in plan.steps:
            stop, reason = guard.should_stop()
            if stop:
                logger.info("agent loop stopping early: %s", reason)
                break
            t0 = time.perf_counter()
            try:
                tool = self._registry.get(step.tool)
                result = await tool.execute(step.args, state)
            except Exception as e:  # noqa: BLE001 — defensive at agent boundary
                logger.warning("tool %s raised: %s", step.tool, e)
                result = ToolResult(success=False, data=None, error=str(e))
            duration_ms = (time.perf_counter() - t0) * 1000
            executed_steps.append(replace(step, result=result, duration_ms=duration_ms))
            results.append(result)
            guard.record_step(result)

        # 3) Synthesize answer
        answer = ""
        if results:
            try:
                answer = await self._llm.synthesize(query, results)
            except Exception as e:  # noqa: BLE001
                logger.warning("synthesize failed: %s", e)
                answer = ""

        # 4) Reflect (single pass — Day 7 에서 retry loop 추가)
        critique = await self._safe_reflect(query, results, answer)

        return AgentTrace(
            trace_id=trace_id,
            query=query,
            plan=plan,
            iterations=[executed_steps],
            critiques=[critique],
            final_answer=answer,
            total_duration_ms=guard.elapsed_seconds * 1000.0,
            tokens=guard.tokens,
            llm_provider=self._llm.provider_name,
        )

    async def _safe_reflect(
        self, query: str, evidence: list[ToolResult], answer: str,
    ) -> Critique:
        try:
            return await self._llm.reflect(query, evidence, answer)
        except Exception as e:  # noqa: BLE001 — fallback critique if reflection fails
            logger.warning("reflection failed: %s — defaulting to confidence=0.5", e)
            return Critique(
                is_sufficient=bool(answer), confidence=0.5,
                next_action="answer", rationale=f"reflection failed: {type(e).__name__}",
            )


async def run_agent(query: str, state: dict, **kwargs) -> AgentTrace:
    """Module-level helper — 단일 호출용. 내부 Agent 인스턴스 매번 생성 (LLM client 재사용 X)."""
    agent = Agent(**kwargs)
    return await agent.run(query, state)
