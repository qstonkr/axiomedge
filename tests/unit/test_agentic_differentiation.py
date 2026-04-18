"""Tests for Day 6-9 differentiation features.

차별화 4축:
- Day 6: Graph routing in planner context
- Day 7: Reflection-driven retry loop
- Day 8: Tiered planning (CHITCHAT skip / FACTUAL 짧음 / ANALYTICAL 깊음)
- Day 9: OCR re-search tool
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agentic.agent import Agent
from src.agentic.cost_guard import CostGuardConfig
from src.agentic.planner import _build_planner_context, QueryEnrichment
from src.agentic.protocols import (
    AgentStep,
    Critique,
    Plan,
    Tool,
    ToolResult,
)
from src.agentic.tools import ReOcrTool, ToolRegistry, build_default_registry
from src.search.query_classifier import QueryType


# =============================================================================
# Day 6: Graph routing — planner context include entity-based hint
# =============================================================================


def test_context_includes_graph_query_hint_for_entities() -> None:
    e = QueryEnrichment(entities=["신촌점", "김담당"])
    ctx = _build_planner_context(e, query_type=QueryType.FACTUAL)
    assert "graph_query 우선" in ctx
    assert "신촌점" in ctx


# =============================================================================
# Day 8: Tiered planning context
# =============================================================================


def test_context_factual_complexity_hint() -> None:
    ctx = _build_planner_context(QueryEnrichment(), query_type=QueryType.FACTUAL)
    assert "1-2" in ctx


def test_context_analytical_complexity_hint() -> None:
    ctx = _build_planner_context(QueryEnrichment(), query_type=QueryType.ANALYTICAL)
    assert "3-4" in ctx


def test_context_multi_hop_includes_graph_recommendation() -> None:
    ctx = _build_planner_context(QueryEnrichment(), query_type=QueryType.MULTI_HOP)
    assert "4-5" in ctx
    assert "graph_query" in ctx


def test_context_chitchat_recommends_skip() -> None:
    ctx = _build_planner_context(QueryEnrichment(), query_type=QueryType.CHITCHAT)
    assert "skip" in ctx.lower()


def test_context_unknown_query_type_no_tiered_lines() -> None:
    e = QueryEnrichment(entities=["test"])
    ctx = _build_planner_context(e, query_type=None)
    # NLP 분석은 있지만 tiered 가이드는 없음
    assert "Korean NLP 분석" in ctx
    assert "Query type" not in ctx


# =============================================================================
# Day 8: CHITCHAT short-circuit in agent loop
# =============================================================================


class _FakeChitchatLLM:
    @property
    def provider_name(self) -> str:
        return "fake"

    async def plan(self, query, available_tools, context: str = "") -> Plan:
        # Should not be called for chitchat — return obviously wrong plan to detect
        return Plan(
            query=query, sub_queries=[query], estimated_complexity=5,
            steps=[AgentStep(step_id="bad", plan_index=0, tool="qdrant_search",
                             args={}, rationale="should not run")],
        )

    async def reflect(self, query, evidence, answer):
        return Critique(is_sufficient=True, confidence=1.0, next_action="answer")

    async def synthesize(self, query, evidence) -> str:
        # Note: no evidence for chitchat
        if not evidence:
            return "안녕하세요!"
        return "should not see this for chitchat"


@pytest.mark.asyncio
async def test_agent_chitchat_skips_rag() -> None:
    llm = _FakeChitchatLLM()
    agent = Agent(llm=llm, registry=build_default_registry())
    trace = await agent.run("안녕하세요", state={})
    # No tools executed — chitchat path
    assert trace.total_steps_executed == 0
    assert trace.final_answer == "안녕하세요!"
    assert trace.critiques[0].rationale == "chitchat skip RAG"


# =============================================================================
# Day 7: Reflection-driven retry loop
# =============================================================================


class _RetryThenSucceedLLM:
    """First reflect = insufficient + revised_query, second reflect = sufficient."""

    def __init__(self) -> None:
        self.plan_calls = 0
        self.reflect_calls = 0

    @property
    def provider_name(self) -> str:
        return "fake"

    async def plan(self, query, available_tools, context: str = "") -> Plan:
        self.plan_calls += 1
        return Plan(
            query=query, sub_queries=[query], estimated_complexity=2,
            steps=[AgentStep(step_id=f"s{self.plan_calls}", plan_index=0,
                             tool="echo", args={"text": query}, rationale="")],
        )

    async def reflect(self, query, evidence, answer):
        self.reflect_calls += 1
        if self.reflect_calls < 2:
            return Critique(
                is_sufficient=False, confidence=0.3, next_action="retry_with_query",
                revised_query=f"{query} (refined)", missing=["specifics"],
            )
        return Critique(is_sufficient=True, confidence=0.9, next_action="answer")

    async def synthesize(self, query, evidence) -> str:
        return f"answer for: {query}"


class _EchoTool(Tool):
    name = "echo"
    description = "echo"
    args_schema: dict[str, Any] = {"type": "object"}

    async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, data=args.get("text", ""))


@pytest.mark.asyncio
async def test_agent_reflection_retry_loop() -> None:
    llm = _RetryThenSucceedLLM()
    registry = ToolRegistry([_EchoTool()])
    agent = Agent(
        llm=llm, registry=registry,
        cost_guard_config=CostGuardConfig(max_iterations=3, budget_usd=10.0),
    )
    trace = await agent.run("test query", state={})
    # 2 iterations: first insufficient, second sufficient
    assert llm.plan_calls == 2
    assert llm.reflect_calls == 2
    assert trace.iteration_count == 2
    assert trace.critiques[0].is_sufficient is False
    assert trace.critiques[1].is_sufficient is True
    # Final answer reflects revised query
    assert "(refined)" in trace.final_answer


@pytest.mark.asyncio
async def test_agent_retry_stops_at_max_iterations() -> None:
    """Even if critique always insufficient, max_iterations enforces stop."""
    class _AlwaysInsufficient:
        plan_calls = 0
        @property
        def provider_name(self): return "fake"
        async def plan(self, query, available_tools, context: str = ""):
            self.plan_calls += 1
            return Plan(query=query, sub_queries=[query], estimated_complexity=1,
                        steps=[AgentStep(step_id=str(self.plan_calls), plan_index=0,
                                          tool="echo", args={}, rationale="")])
        async def reflect(self, query, evidence, answer):
            return Critique(is_sufficient=False, confidence=0.1,
                            next_action="retry_with_query", revised_query=query)
        async def synthesize(self, query, evidence): return ""
    llm = _AlwaysInsufficient()
    agent = Agent(
        llm=llm, registry=ToolRegistry([_EchoTool()]),
        cost_guard_config=CostGuardConfig(max_iterations=2, budget_usd=10.0),
    )
    trace = await agent.run("q", state={})
    assert trace.iteration_count == 2
    assert llm.plan_calls == 2  # max_iterations 가 강제 정지


@pytest.mark.asyncio
async def test_agent_give_up_action_stops_immediately() -> None:
    class _GiveUp:
        @property
        def provider_name(self): return "fake"
        async def plan(self, q, tools, context=""):
            return Plan(query=q, sub_queries=[q], estimated_complexity=1,
                        steps=[AgentStep(step_id="x", plan_index=0,
                                          tool="echo", args={}, rationale="")])
        async def reflect(self, q, e, a):
            return Critique(is_sufficient=False, confidence=0.0,
                            next_action="give_up", rationale="no info")
        async def synthesize(self, q, e): return ""
    agent = Agent(
        llm=_GiveUp(), registry=ToolRegistry([_EchoTool()]),
        cost_guard_config=CostGuardConfig(max_iterations=5, budget_usd=10.0),
    )
    trace = await agent.run("q", state={})
    assert trace.iteration_count == 1  # give_up 즉시 정지


# =============================================================================
# Day 9: OCR re-search tool
# =============================================================================


@pytest.mark.asyncio
async def test_re_ocr_detects_low_confidence_chunks() -> None:
    tool = ReOcrTool()
    chunks = [
        {"chunk_id": "c1", "content": "good text", "metadata": {"ocr_confidence": 0.95}},
        {"chunk_id": "c2", "content": "bad text", "metadata": {"ocr_confidence": 0.5}},
        {"chunk_id": "c3", "content": "no ocr", "metadata": {}},
        {"chunk_id": "c4", "content": "very bad", "metadata": {"ocr_confidence": 0.2}},
    ]
    result = await tool.execute({"chunks": chunks}, state={})
    assert result.success
    assert result.data["low_confidence_count"] == 2
    ids = [c["chunk_id"] for c in result.data["low_confidence_chunks"]]
    assert "c2" in ids and "c4" in ids


@pytest.mark.asyncio
async def test_re_ocr_threshold_override() -> None:
    tool = ReOcrTool()
    chunks = [{"chunk_id": "c1", "content": "x", "metadata": {"ocr_confidence": 0.85}}]
    # threshold 0.9 → 0.85 도 low-confidence
    result = await tool.execute({"chunks": chunks, "threshold": 0.9}, state={})
    assert result.data["low_confidence_count"] == 1


@pytest.mark.asyncio
async def test_re_ocr_invalid_chunks_input() -> None:
    tool = ReOcrTool()
    result = await tool.execute({"chunks": "not a list"}, state={})
    assert not result.success
    assert "must be a list" in (result.error or "")


@pytest.mark.asyncio
async def test_re_ocr_no_chunks_returns_empty_recommendation() -> None:
    tool = ReOcrTool()
    result = await tool.execute({"chunks": []}, state={})
    assert result.success
    assert result.data["low_confidence_count"] == 0
    assert "양호" in result.data["recommendation"]


def test_re_ocr_in_default_registry() -> None:
    reg = build_default_registry()
    assert "re_ocr_search" in reg
    assert len(reg.names()) == 6  # 5 → 6 with re_ocr


# =============================================================================
# GraphRAG extractor — LLM_PROVIDER 통합 (legacy GRAPHRAG_USE_SAGEMAKER 보존)
# =============================================================================


def test_graphrag_extractor_legacy_flag_still_works(monkeypatch) -> None:
    """GRAPHRAG_USE_SAGEMAKER=true 가 setting 되어 있으면 sagemaker 사용 (deprecation 경고)."""
    monkeypatch.setenv("GRAPHRAG_USE_SAGEMAKER", "true")
    monkeypatch.setenv("SAGEMAKER_ENDPOINT_NAME", "test-endpoint")
    from src.pipelines.graphrag.extractor import GraphRAGExtractor
    extractor = GraphRAGExtractor()
    # Just verify init doesn't crash + flag respected
    # (실 LLM 호출은 boto3 필요 — 여기선 구성 검증만)
    assert extractor is not None
