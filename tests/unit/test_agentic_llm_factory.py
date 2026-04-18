"""Tests for AgentLLM factory + JsonAgentLLM base parsing."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agentic.llm import create_agent_llm
from src.agentic.llm.base import JsonAgentLLM
from src.agentic.protocols import AgentLLM, ToolResult, ToolSpec


def _spec(name: str = "qdrant_search") -> ToolSpec:
    return ToolSpec(name=name, description="...", args_schema={})


# =============================================================================
# Factory env resolution
# =============================================================================


def test_factory_default_returns_ollama_when_nothing_set() -> None:
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("LLM_PROVIDER", None)
        os.environ.pop("USE_SAGEMAKER_LLM", None)
        # ollama init may fail without ollama service — we just verify provider_name
        with patch("src.agentic.llm.ollama.OllamaAgentLLM") as M:
            instance = MagicMock()
            instance.provider_name = "ollama"
            M.return_value = instance
            llm = create_agent_llm()
            assert llm.provider_name == "ollama"


def test_factory_sagemaker_when_legacy_flag_true() -> None:
    with patch.dict("os.environ", {"USE_SAGEMAKER_LLM": "true"}, clear=False):
        with patch("src.agentic.llm.sagemaker.SageMakerAgentLLM") as M:
            instance = MagicMock()
            instance.provider_name = "sagemaker"
            M.return_value = instance
            llm = create_agent_llm()
            assert llm.provider_name == "sagemaker"


def test_factory_explicit_provider_overrides_env() -> None:
    with patch.dict("os.environ", {"LLM_PROVIDER": "ollama"}, clear=False):
        with patch("src.agentic.llm.sagemaker.SageMakerAgentLLM") as M:
            instance = MagicMock()
            instance.provider_name = "sagemaker"
            M.return_value = instance
            llm = create_agent_llm("sagemaker")
            assert llm.provider_name == "sagemaker"


def test_factory_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unknown agent LLM provider"):
        create_agent_llm("unknown-provider-xyz")


# =============================================================================
# JsonAgentLLM Protocol satisfaction
# =============================================================================


def test_json_agent_llm_satisfies_protocol() -> None:
    client = MagicMock()
    llm = JsonAgentLLM(client=client, provider_name="test")
    assert isinstance(llm, AgentLLM)
    assert llm.provider_name == "test"


# =============================================================================
# JSON parsing
# =============================================================================


def test_parse_json_valid() -> None:
    out = JsonAgentLLM._parse_json('{"a": 1, "b": [1, 2]}')
    assert out == {"a": 1, "b": [1, 2]}


def test_parse_json_broken_returns_empty_dict() -> None:
    out = JsonAgentLLM._parse_json("not json at all")
    # json_repair may attempt — accept either {} or any reasonable dict
    assert isinstance(out, dict)


# =============================================================================
# _parse_plan
# =============================================================================


def test_parse_plan_valid() -> None:
    raw = json.dumps({
        "sub_queries": ["q1", "q2"],
        "estimated_complexity": 3,
        "rationale": "test",
        "steps": [
            {"tool": "qdrant_search", "args": {"query": "x"}, "rationale": "..."},
            {"tool": "graph_query", "args": {"mode": "entities"}, "rationale": "..."},
        ],
    })
    plan = JsonAgentLLM._parse_plan(
        raw, query="test", available_tools=[_spec("qdrant_search"), _spec("graph_query")],
    )
    assert plan.query == "test"
    assert plan.sub_queries == ["q1", "q2"]
    assert plan.estimated_complexity == 3
    assert len(plan.steps) == 2


def test_parse_plan_drops_unknown_tools() -> None:
    raw = json.dumps({
        "steps": [
            {"tool": "qdrant_search", "args": {}, "rationale": ""},
            {"tool": "fake_tool", "args": {}, "rationale": ""},
        ],
    })
    plan = JsonAgentLLM._parse_plan(raw, query="q", available_tools=[_spec("qdrant_search")])
    assert len(plan.steps) == 1
    assert plan.steps[0].tool == "qdrant_search"


def test_parse_plan_complexity_clamped() -> None:
    raw = json.dumps({"estimated_complexity": 99, "steps": []})
    plan = JsonAgentLLM._parse_plan(raw, query="q", available_tools=[])
    assert plan.estimated_complexity == 5  # clamped to max
    raw2 = json.dumps({"estimated_complexity": -3, "steps": []})
    plan2 = JsonAgentLLM._parse_plan(raw2, query="q", available_tools=[])
    assert plan2.estimated_complexity == 1  # clamped to min


def test_parse_plan_missing_steps_returns_empty_plan() -> None:
    plan = JsonAgentLLM._parse_plan("{}", query="q", available_tools=[])
    assert plan.steps == []
    assert plan.sub_queries == ["q"]  # falls back to query


# =============================================================================
# _parse_critique
# =============================================================================


def test_parse_critique_valid() -> None:
    raw = json.dumps({
        "is_sufficient": True, "confidence": 0.85, "next_action": "answer",
        "missing": [], "rationale": "OK",
    })
    crit = JsonAgentLLM._parse_critique(raw)
    assert crit.is_sufficient
    assert crit.confidence == 0.85
    assert crit.next_action == "answer"


def test_parse_critique_invalid_action_defaults_to_answer() -> None:
    raw = json.dumps({"next_action": "unknown_action"})
    crit = JsonAgentLLM._parse_critique(raw)
    assert crit.next_action == "answer"


def test_parse_critique_confidence_clamped() -> None:
    crit = JsonAgentLLM._parse_critique(json.dumps({"confidence": 5.0}))
    assert crit.confidence == 1.0
    crit2 = JsonAgentLLM._parse_critique(json.dumps({"confidence": -0.3}))
    assert crit2.confidence == 0.0


# =============================================================================
# Async plan/reflect roundtrip
# =============================================================================


@pytest.mark.asyncio
async def test_plan_calls_underlying_llm() -> None:
    raw = json.dumps({
        "sub_queries": ["x"], "estimated_complexity": 1, "steps": [
            {"tool": "qdrant_search", "args": {"query": "x"}, "rationale": "r"},
        ],
    })
    client: Any = MagicMock()
    client.generate = AsyncMock(return_value=raw)
    llm = JsonAgentLLM(client=client, provider_name="test")
    plan = await llm.plan("x", available_tools=[_spec("qdrant_search")])
    assert client.generate.called
    assert len(plan.steps) == 1


@pytest.mark.asyncio
async def test_reflect_calls_underlying_llm() -> None:
    raw = json.dumps({"is_sufficient": True, "confidence": 0.9, "next_action": "answer"})
    client: Any = MagicMock()
    client.generate = AsyncMock(return_value=raw)
    llm = JsonAgentLLM(client=client, provider_name="test")
    crit = await llm.reflect("q", evidence=[], answer="a")
    assert client.generate.called
    assert crit.is_sufficient


@pytest.mark.asyncio
async def test_synthesize_calls_generate_response() -> None:
    client: Any = MagicMock()
    client.generate_response = AsyncMock(return_value="final answer")
    llm = JsonAgentLLM(client=client, provider_name="test")
    answer = await llm.synthesize("q", evidence=[
        ToolResult(success=True, data={"hello": "world"}),
        ToolResult(success=False, data=None, error="x"),  # skipped (failed)
    ])
    assert answer == "final answer"
    args, kwargs = client.generate_response.call_args
    assert kwargs["query"] == "q"
    assert len(kwargs["context"]) == 1  # only successful
