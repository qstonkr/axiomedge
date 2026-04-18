"""Tests for Day 10 — Edge ↔ HQ LLM routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agentic.protocols import (
    Critique,
    Plan,
    ToolSpec,
)
from src.agentic.routing import RoutingAgentLLM, maybe_wrap_with_routing


def _spec() -> ToolSpec:
    return ToolSpec(name="t", description="", args_schema={})


def _plan_with_complexity(c: int) -> Plan:
    return Plan(query="q", sub_queries=["q"], steps=[], estimated_complexity=c)


# =============================================================================
# RoutingAgentLLM
# =============================================================================


def test_routing_satisfies_protocol() -> None:
    hq = MagicMock()
    routing = RoutingAgentLLM(hq=hq, edge=None)
    # Note: MagicMock 자체로 Protocol check 는 pass — 구조적 typing
    assert hasattr(routing, "plan")
    assert hasattr(routing, "reflect")
    assert hasattr(routing, "synthesize")
    assert hasattr(routing, "provider_name")


def test_routing_provider_name_no_edge() -> None:
    hq = MagicMock()
    hq.provider_name = "sagemaker"
    routing = RoutingAgentLLM(hq=hq, edge=None)
    assert routing.provider_name == "sagemaker"


def test_routing_provider_name_with_edge() -> None:
    hq = MagicMock()
    hq.provider_name = "sagemaker"
    edge = MagicMock()
    edge.provider_name = "edge"
    routing = RoutingAgentLLM(hq=hq, edge=edge)
    assert "routing" in routing.provider_name
    assert "sagemaker" in routing.provider_name
    assert "edge" in routing.provider_name


@pytest.mark.asyncio
async def test_routing_plan_always_uses_hq() -> None:
    hq = MagicMock()
    hq.plan = AsyncMock(return_value=_plan_with_complexity(3))
    edge = MagicMock()
    edge.plan = AsyncMock(return_value=_plan_with_complexity(1))
    routing = RoutingAgentLLM(hq=hq, edge=edge)
    plan = await routing.plan("q", available_tools=[_spec()])
    assert hq.plan.called
    assert not edge.plan.called
    assert plan.estimated_complexity == 3


@pytest.mark.asyncio
async def test_routing_reflect_always_uses_hq() -> None:
    hq = MagicMock()
    hq.reflect = AsyncMock(return_value=Critique(
        is_sufficient=True, confidence=0.9, next_action="answer",
    ))
    edge = MagicMock()
    edge.reflect = AsyncMock()
    routing = RoutingAgentLLM(hq=hq, edge=edge)
    await routing.reflect("q", evidence=[], answer="x")
    assert hq.reflect.called
    assert not edge.reflect.called


@pytest.mark.asyncio
async def test_routing_synthesize_uses_edge_for_simple() -> None:
    hq = MagicMock()
    hq.plan = AsyncMock(return_value=_plan_with_complexity(1))  # 단순
    hq.synthesize = AsyncMock(return_value="hq answer")
    edge = MagicMock()
    edge.synthesize = AsyncMock(return_value="edge answer")
    routing = RoutingAgentLLM(hq=hq, edge=edge, complexity_threshold=2)
    await routing.plan("q", available_tools=[_spec()])  # complexity 1 → edge
    answer = await routing.synthesize("q", evidence=[])
    assert edge.synthesize.called
    assert not hq.synthesize.called
    assert answer == "edge answer"


@pytest.mark.asyncio
async def test_routing_synthesize_uses_hq_for_complex() -> None:
    hq = MagicMock()
    hq.plan = AsyncMock(return_value=_plan_with_complexity(4))
    hq.synthesize = AsyncMock(return_value="hq answer")
    edge = MagicMock()
    edge.synthesize = AsyncMock(return_value="edge answer")
    routing = RoutingAgentLLM(hq=hq, edge=edge, complexity_threshold=2)
    await routing.plan("q", available_tools=[_spec()])  # complexity 4 → HQ
    answer = await routing.synthesize("q", evidence=[])
    assert hq.synthesize.called
    assert not edge.synthesize.called
    assert answer == "hq answer"


@pytest.mark.asyncio
async def test_routing_falls_back_to_hq_when_no_edge() -> None:
    hq = MagicMock()
    hq.plan = AsyncMock(return_value=_plan_with_complexity(1))
    hq.synthesize = AsyncMock(return_value="hq")
    routing = RoutingAgentLLM(hq=hq, edge=None)
    await routing.plan("q", available_tools=[_spec()])
    answer = await routing.synthesize("q", evidence=[])
    assert hq.synthesize.called
    assert answer == "hq"


@pytest.mark.asyncio
async def test_routing_falls_back_to_hq_on_edge_failure() -> None:
    hq = MagicMock()
    hq.plan = AsyncMock(return_value=_plan_with_complexity(1))
    hq.synthesize = AsyncMock(return_value="hq fallback")
    edge = MagicMock()
    edge.synthesize = AsyncMock(side_effect=RuntimeError("edge down"))
    routing = RoutingAgentLLM(hq=hq, edge=edge, complexity_threshold=2)
    await routing.plan("q", available_tools=[_spec()])
    answer = await routing.synthesize("q", evidence=[])
    assert edge.synthesize.called
    assert hq.synthesize.called  # fallback
    assert answer == "hq fallback"


# =============================================================================
# maybe_wrap_with_routing — env-driven
# =============================================================================


def test_maybe_wrap_no_edge_url_returns_hq_unchanged() -> None:
    hq = MagicMock()
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("AGENTIC_EDGE_URL", None)
        result = maybe_wrap_with_routing(hq)
        assert result is hq


def test_maybe_wrap_edge_init_failure_returns_hq() -> None:
    hq = MagicMock()
    with patch.dict("os.environ", {"AGENTIC_EDGE_URL": "http://nonexistent:9999"}):
        with patch(
            "src.agentic.llm.edge.EdgeAgentLLM",
            side_effect=RuntimeError("init failed"),
        ):
            result = maybe_wrap_with_routing(hq)
            assert result is hq  # fallback


def test_maybe_wrap_with_url_returns_routing() -> None:
    hq = MagicMock()
    edge_instance = MagicMock()
    edge_instance.provider_name = "edge"
    with patch.dict("os.environ", {"AGENTIC_EDGE_URL": "http://store-001:8001"}):
        with patch("src.agentic.llm.edge.EdgeAgentLLM", return_value=edge_instance):
            result = maybe_wrap_with_routing(hq)
            assert isinstance(result, RoutingAgentLLM)


# =============================================================================
# EdgeAgentLLM init (env validation)
# =============================================================================


def test_edge_agent_llm_requires_url_env() -> None:
    from src.agentic.llm.edge import EdgeAgentLLM
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("AGENTIC_EDGE_URL", None)
        with pytest.raises(RuntimeError, match="AGENTIC_EDGE_URL not set"):
            EdgeAgentLLM()


def test_edge_agent_llm_init_with_explicit_url() -> None:
    from src.agentic.llm.edge import EdgeAgentLLM
    llm = EdgeAgentLLM(base_url="http://store-001:8001", api_key="x")
    assert llm.provider_name == "edge"
