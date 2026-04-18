"""Integration tests for the Agent loop (single iteration baseline)."""

from __future__ import annotations

from typing import Any

import pytest

from src.agentic.agent import Agent, run_agent
from src.agentic.cost_guard import CostGuardConfig
from src.agentic.protocols import (
    AgentStep,
    AgentTrace,
    Critique,
    Plan,
    Tool,
    ToolResult,
)
from src.agentic.tools import ToolRegistry


class _FakeLLM:
    """Minimal AgentLLM impl for tests — fixed plan/critique/answer."""

    def __init__(
        self,
        plan_steps: list[tuple[str, dict]] | None = None,
        critique_action: str = "answer",
        answer: str = "fake answer",
    ) -> None:
        self._plan_steps = plan_steps or []
        self._critique_action = critique_action
        self._answer = answer

    @property
    def provider_name(self) -> str:
        return "fake"

    async def plan(self, query, available_tools, context: str = "") -> Plan:
        steps = [
            AgentStep(step_id=f"s{i}", plan_index=i, tool=tool, args=args, rationale="")
            for i, (tool, args) in enumerate(self._plan_steps)
        ]
        return Plan(query=query, sub_queries=[query], steps=steps, estimated_complexity=2)

    async def reflect(self, query, evidence, answer) -> Critique:
        return Critique(
            is_sufficient=(self._critique_action == "answer"),
            confidence=0.9,
            next_action=self._critique_action,
        )

    async def synthesize(self, query, evidence) -> str:
        return self._answer


class _EchoTool(Tool):
    name = "echo"
    description = "Echo args back"
    args_schema: dict[str, Any] = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, data={"echoed": args.get("text", "")})


class _FailingTool(Tool):
    name = "fail"
    description = "Always fails"
    args_schema: dict[str, Any] = {"type": "object"}

    async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        raise RuntimeError("intentional fail")


class _CostlyTool(Tool):
    name = "costly"
    description = "expensive"
    args_schema: dict[str, Any] = {"type": "object"}

    async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, data="ok", metadata={"estimated_cost_usd": 0.05})


# =============================================================================
# Happy path
# =============================================================================


@pytest.mark.asyncio
async def test_agent_run_executes_planned_steps() -> None:
    registry = ToolRegistry([_EchoTool()])
    llm = _FakeLLM(plan_steps=[("echo", {"text": "hi"})], answer="hello world")
    agent = Agent(llm=llm, registry=registry)
    trace = await agent.run("test query", state={})
    assert trace.query == "test query"
    assert trace.final_answer == "hello world"
    assert trace.iteration_count == 1
    assert trace.total_steps_executed == 1
    assert trace.iterations[0][0].tool == "echo"
    assert trace.iterations[0][0].result is not None
    assert trace.iterations[0][0].result.data == {"echoed": "hi"}
    assert trace.llm_provider == "fake"


@pytest.mark.asyncio
async def test_agent_handles_tool_exception_gracefully() -> None:
    registry = ToolRegistry([_FailingTool()])
    llm = _FakeLLM(plan_steps=[("fail", {})])
    agent = Agent(llm=llm, registry=registry)
    trace = await agent.run("test", state={})
    # Agent should not crash — failure captured in trace
    assert trace.iterations[0][0].result is not None
    assert not trace.iterations[0][0].result.success
    assert "intentional fail" in (trace.iterations[0][0].result.error or "")


@pytest.mark.asyncio
async def test_agent_records_tokens_in_trace() -> None:
    registry = ToolRegistry([_CostlyTool()])
    llm = _FakeLLM(plan_steps=[("costly", {}), ("costly", {})])
    agent = Agent(llm=llm, registry=registry, cost_guard_config=CostGuardConfig(budget_usd=0.20))
    trace = await agent.run("q", state={})
    assert trace.tokens.estimated_cost_usd == pytest.approx(0.10, rel=0.01)


@pytest.mark.asyncio
async def test_agent_stops_on_budget_exceeded() -> None:
    registry = ToolRegistry([_CostlyTool()])
    llm = _FakeLLM(plan_steps=[("costly", {}), ("costly", {}), ("costly", {})])
    agent = Agent(llm=llm, registry=registry, cost_guard_config=CostGuardConfig(budget_usd=0.06))
    trace = await agent.run("q", state={})
    # 첫 step($0.05) → 두 번째($0.10) → budget 0.06 초과 → 세 번째 skip
    assert trace.total_steps_executed == 2


@pytest.mark.asyncio
async def test_agent_synthesize_failure_returns_empty_answer() -> None:
    class _BrokenSynth(_FakeLLM):
        async def synthesize(self, query, evidence):
            raise RuntimeError("synth failed")
    registry = ToolRegistry([_EchoTool()])
    llm = _BrokenSynth(plan_steps=[("echo", {"text": "x"})])
    agent = Agent(llm=llm, registry=registry)
    trace = await agent.run("q", state={})
    assert trace.final_answer == ""


@pytest.mark.asyncio
async def test_agent_reflect_failure_returns_default_critique() -> None:
    class _BrokenReflect(_FakeLLM):
        async def reflect(self, query, evidence, answer):
            raise RuntimeError("reflect failed")
    registry = ToolRegistry([_EchoTool()])
    llm = _BrokenReflect(plan_steps=[("echo", {"text": "x"})])
    agent = Agent(llm=llm, registry=registry)
    trace = await agent.run("q", state={})
    assert len(trace.critiques) == 1
    assert trace.critiques[0].confidence == 0.5  # fallback
    assert "reflection failed" in trace.critiques[0].rationale


@pytest.mark.asyncio
async def test_run_agent_helper_works() -> None:
    """Module-level helper smoke test."""
    registry = ToolRegistry([_EchoTool()])
    llm = _FakeLLM(plan_steps=[("echo", {"text": "y"})])
    trace = await run_agent("hello", state={}, llm=llm, registry=registry)
    assert isinstance(trace, AgentTrace)
    assert trace.final_answer == "fake answer"
