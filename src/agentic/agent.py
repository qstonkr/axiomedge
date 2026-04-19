"""Agent loop — plan → execute tools → synthesize → reflect → (retry).

Day 7 reflection-driven retry loop 적용 — critique 가 충분 안 하면 revised query 로 재시도.
Day 8 tiered planning — CHITCHAT 은 RAG skip.
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
    Plan,
    ToolResult,
)
from src.agentic.tools import ToolRegistry, build_default_registry
from src.search.query_classifier import QueryType

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
        enable_edge_routing: bool = True,
    ) -> None:
        base_llm = llm or create_agent_llm()
        if enable_edge_routing:
            from src.agentic.routing import maybe_wrap_with_routing
            self._llm = maybe_wrap_with_routing(base_llm)
        else:
            self._llm = base_llm
        self._registry = registry or build_default_registry()
        self._planner = KoreanQueryPlanner(self._llm, self._registry)
        self._cost_guard_config = cost_guard_config

    async def run(self, query: str, state: dict) -> AgentTrace:
        """Agent loop with reflection-driven retry (max_iterations 까지).

        state: FastAPI app state — qdrant_search/graph_repo/glossary/embedder/kb_registry 가용.
        """
        guard = CostGuard(self._cost_guard_config)
        trace_id = str(uuid.uuid4())
        errors: list[str] = []

        # ── CHITCHAT short-circuit (Day 8 tiered planning) ──
        query_type = self._planner.classify_query(query)
        if query_type == QueryType.CHITCHAT:
            return await self._chitchat_response(trace_id, query, guard)

        # ── First plan ──
        plan = await self._planner.make_plan(query, state=state)

        all_iterations: list[list[AgentStep]] = []
        all_critiques: list[Critique] = []
        answer = ""
        # best-so-far answer across iterations — iter N 이 0 step 또는 synthesize
        # 실패로 빈 answer 를 만들어도 iter <N 의 좋은 answer 가 final 에 남도록.
        best_answer = ""
        best_confidence = -1.0
        current_query = query

        # ── Reflection-driven loop (Day 7) ──
        while not guard.iteration_limit_reached():
            # 새 iteration 시작 전 cost-guard 가 이미 한도라면 begin 하지 않음
            # (빈 iteration 이 trace 에 쌓이는 걸 방지).
            stop, reason = guard.should_stop()
            if stop:
                logger.info("agent loop stop before iteration: %s", reason)
                break
            guard.begin_iteration()
            executed_steps, results = await self._execute_plan(plan, state, guard, errors)
            all_iterations.append(executed_steps)

            # 이 iteration 에서 단 하나의 tool 도 못 돌렸다면 (예: cost guard
            # 첫 step 전에 발동) — synthesize/reflect 호출하지 말고 그대로 break.
            # answer 를 빈 값으로 덮어쓰는 걸 방지.
            if not executed_steps:
                errors.append(
                    "iteration aborted with 0 executed steps "
                    "(likely cost/time guard). 이전 iteration answer 유지.",
                )
                break

            answer = await self._safe_synthesize(current_query, results, errors)
            critique = await self._safe_reflect(current_query, results, answer, errors)
            all_critiques.append(critique)

            # best-so-far 업데이트 — confidence 가 더 높은 비-빈 answer 만 채택.
            if answer and critique.confidence > best_confidence:
                best_answer = answer
                best_confidence = critique.confidence

            # Stop criteria
            if critique.is_sufficient or critique.next_action in ("answer", "give_up"):
                break
            stop, reason = guard.should_stop()
            if stop:
                logger.info("agent loop stop after iteration: %s", reason)
                break
            # Re-plan with revised query / KB
            next_query = critique.revised_query or current_query
            next_kb_hint = (
                f"이전 시도 누락: {', '.join(critique.missing) or '(미명시)'}\n"
                f"→ 다른 KB / 변형 query 시도. KB 후보: {critique.revised_kb_ids or '미지정'}"
            )
            try:
                plan = await self._planner.make_plan(
                    next_query, extra_context=next_kb_hint, state=state,
                )
                current_query = next_query
            except Exception as e:  # noqa: BLE001 — graceful: stop loop
                logger.warning("re-plan failed: %s", e)
                errors.append(f"re-plan failed: {type(e).__name__}: {e}")
                break

        # 빈 final 보다 best-so-far 우선. 둘 다 비면 최종도 빈 채로.
        final_answer = best_answer or answer

        return AgentTrace(
            trace_id=trace_id,
            query=query,
            plan=plan,
            iterations=all_iterations,
            critiques=all_critiques,
            final_answer=final_answer,
            total_duration_ms=guard.elapsed_seconds * 1000.0,
            tokens=guard.tokens,
            llm_provider=self._llm.provider_name,
            errors=errors,
        )

    async def _execute_plan(
        self, plan: Plan, state: dict, guard: CostGuard, errors: list[str],
    ) -> tuple[list[AgentStep], list[ToolResult]]:
        """plan.steps 를 순차 실행 — cost guard 한도 내."""
        executed: list[AgentStep] = []
        results: list[ToolResult] = []
        for step in plan.steps:
            stop, reason = guard.should_stop()
            if stop:
                logger.info("execute_plan stopping: %s", reason)
                break
            t0 = time.perf_counter()
            try:
                tool = self._registry.get(step.tool)
                result = await tool.execute(step.args, state)
            except Exception as e:  # noqa: BLE001 — defensive at agent boundary
                logger.warning("tool %s raised: %s", step.tool, e)
                errors.append(f"tool {step.tool} raised: {type(e).__name__}: {e}")
                result = ToolResult(success=False, data=None, error=str(e))
            else:
                if not result.success and result.error:
                    errors.append(f"tool {step.tool} returned error: {result.error}")
            duration_ms = (time.perf_counter() - t0) * 1000
            executed.append(replace(step, result=result, duration_ms=duration_ms))
            results.append(result)
            guard.record_step(result)
        return executed, results

    async def _chitchat_response(
        self, trace_id: str, query: str, guard: CostGuard,
    ) -> AgentTrace:
        """CHITCHAT 은 RAG skip — 직접 LLM 응답."""
        errors: list[str] = []
        try:
            answer = await self._llm.synthesize(query, evidence=[])
        except Exception as e:  # noqa: BLE001
            logger.warning("chitchat synthesize failed: %s", e)
            errors.append(f"chitchat synthesize failed: {type(e).__name__}: {e}")
            answer = "안녕하세요. 무엇을 도와드릴까요?"
        empty_plan = Plan(query=query, sub_queries=[query], steps=[], estimated_complexity=1)
        return AgentTrace(
            trace_id=trace_id, query=query, plan=empty_plan,
            iterations=[[]], critiques=[Critique(
                is_sufficient=True, confidence=1.0, next_action="answer",
                rationale="chitchat skip RAG",
            )],
            final_answer=answer,
            total_duration_ms=guard.elapsed_seconds * 1000.0,
            tokens=guard.tokens,
            llm_provider=self._llm.provider_name,
            errors=errors,
        )

    async def _safe_synthesize(
        self, query: str, results: list[ToolResult], errors: list[str],
    ) -> str:
        if not any(r.success for r in results):
            errors.append("synthesize skipped: all tool steps failed (no successful evidence)")
            return ""
        try:
            return await self._llm.synthesize(query, results)
        except Exception as e:  # noqa: BLE001
            logger.warning("synthesize failed: %s", e)
            errors.append(f"synthesize failed: {type(e).__name__}: {e}")
            return ""

    async def _safe_reflect(
        self, query: str, evidence: list[ToolResult], answer: str, errors: list[str],
    ) -> Critique:
        try:
            return await self._llm.reflect(query, evidence, answer)
        except Exception as e:  # noqa: BLE001 — fallback critique if reflection fails
            logger.warning("reflection failed: %s — defaulting to confidence=0.5", e)
            errors.append(f"reflect failed: {type(e).__name__}: {e}")
            return Critique(
                is_sufficient=bool(answer), confidence=0.5,
                next_action="answer", rationale=f"reflection failed: {type(e).__name__}",
            )


async def run_agent(query: str, state: dict, **kwargs) -> AgentTrace:
    """Module-level helper — 단일 호출용. 내부 Agent 인스턴스 매번 생성 (LLM client 재사용 X)."""
    agent = Agent(**kwargs)
    return await agent.run(query, state)
