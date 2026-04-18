"""Tests for agentic protocols + data model invariants."""

from __future__ import annotations

from typing import Any

import pytest

from src.agentic.protocols import (
    AgentLLM,
    AgentStep,
    AgentTrace,
    Critique,
    Plan,
    TokenUsage,
    Tool,
    ToolResult,
    ToolSpec,
)


# =============================================================================
# Tool ABC
# =============================================================================


class _DummyTool(Tool):
    name = "dummy"
    description = "Test tool"
    args_schema = {"type": "object", "properties": {"x": {"type": "integer"}}}

    async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, data={"echo": args.get("x", 0) * 2})


def test_tool_spec_returns_metadata() -> None:
    tool = _DummyTool()
    spec = tool.spec()
    assert isinstance(spec, ToolSpec)
    assert spec.name == "dummy"
    assert spec.description == "Test tool"
    assert spec.args_schema == {"type": "object", "properties": {"x": {"type": "integer"}}}


@pytest.mark.asyncio
async def test_tool_execute_returns_tool_result() -> None:
    tool = _DummyTool()
    result = await tool.execute({"x": 5}, state={})
    assert isinstance(result, ToolResult)
    assert result.success
    assert result.data == {"echo": 10}


def test_tool_abstract_cannot_instantiate() -> None:
    with pytest.raises(TypeError):
        Tool()  # type: ignore[abstract]


# =============================================================================
# Data model immutability + defaults
# =============================================================================


def test_tool_result_default_metadata_empty() -> None:
    result = ToolResult(success=True, data="x")
    assert result.metadata == {}
    assert result.error is None


def test_tool_result_failed_with_error() -> None:
    result = ToolResult(success=False, data=None, error="timeout")
    assert not result.success
    assert result.error == "timeout"


def test_agent_step_immutable() -> None:
    step = AgentStep(
        step_id="s1", plan_index=0, tool="qdrant_search",
        args={"query": "x"}, rationale="vector lookup",
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        step.tool = "graph_query"  # type: ignore[misc]


def test_plan_step_count() -> None:
    plan = Plan(
        query="q", sub_queries=["q1", "q2"],
        steps=[
            AgentStep(step_id=f"s{i}", plan_index=i, tool="t",
                      args={}, rationale="r")
            for i in range(3)
        ],
        estimated_complexity=3,
    )
    assert plan.step_count == 3


def test_critique_default_missing_empty() -> None:
    crit = Critique(is_sufficient=True, confidence=0.9, next_action="answer")
    assert crit.missing == []
    assert crit.revised_query is None


def test_token_usage_defaults_zero() -> None:
    usage = TokenUsage()
    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0
    assert usage.estimated_cost_usd == 0.0


def test_agent_trace_iteration_count_zero_initially() -> None:
    plan = Plan(query="q", sub_queries=[], steps=[], estimated_complexity=1)
    trace = AgentTrace(trace_id="t1", query="q", plan=plan)
    assert trace.iteration_count == 0
    assert trace.total_steps_executed == 0
    assert trace.final_answer == ""


def test_agent_trace_total_steps_executed() -> None:
    plan = Plan(query="q", sub_queries=[], steps=[], estimated_complexity=1)
    trace = AgentTrace(
        trace_id="t1", query="q", plan=plan,
        iterations=[
            [AgentStep(step_id="a", plan_index=0, tool="t", args={}, rationale="")],
            [AgentStep(step_id="b", plan_index=0, tool="t", args={}, rationale=""),
             AgentStep(step_id="c", plan_index=1, tool="t", args={}, rationale="")],
        ],
    )
    assert trace.iteration_count == 2
    assert trace.total_steps_executed == 3


# =============================================================================
# AgentLLM Protocol — runtime_checkable
# =============================================================================


class _FakeAgentLLM:
    """Minimal AgentLLM impl for Protocol check."""

    @property
    def provider_name(self) -> str:
        return "fake"

    async def plan(self, query: str, available_tools: list[ToolSpec], context: str = "") -> Plan:
        return Plan(query=query, sub_queries=[], steps=[], estimated_complexity=1)

    async def reflect(self, query: str, evidence: list[ToolResult], answer: str | None) -> Critique:
        return Critique(is_sufficient=True, confidence=1.0, next_action="answer")

    async def synthesize(self, query: str, evidence: list[ToolResult]) -> str:
        return "stub answer"


def test_fake_agent_llm_satisfies_protocol() -> None:
    llm = _FakeAgentLLM()
    assert isinstance(llm, AgentLLM)


def test_object_missing_methods_does_not_satisfy_protocol() -> None:
    class _Empty:
        pass
    assert not isinstance(_Empty(), AgentLLM)


@pytest.mark.asyncio
async def test_fake_agent_llm_methods_callable() -> None:
    llm = _FakeAgentLLM()
    plan = await llm.plan("test", available_tools=[])
    assert plan.query == "test"
    crit = await llm.reflect("test", evidence=[], answer="x")
    assert crit.is_sufficient
    answer = await llm.synthesize("test", evidence=[])
    assert answer == "stub answer"
    assert llm.provider_name == "fake"
